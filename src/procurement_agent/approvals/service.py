"""Store a proposed purchase order and enforce approval in Python."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from procurement_agent.approvals.errors import ApprovalError
from procurement_agent.approvals.models import AuditEventRow, Base, ConversationRow, ProposalRow
from procurement_agent.approvals.orders import DraftPurchaseOrder

ACTION = "create_draft_purchase_order"


class DraftPurchaseOrderWriter(Protocol):
    def create_draft(self, order: DraftPurchaseOrder) -> str:
        """Create one ERPNext draft and return its document name."""

    def submit(self, name: str) -> None:
        """Submit an existing ERPNext purchase order."""


@dataclass(frozen=True)
class StoredProposal:
    id: str
    idempotency_key: str
    status: str
    payload: DraftPurchaseOrder
    payload_hash: str
    approved_by: str | None
    erp_document_name: str | None
    slack_channel: str | None
    slack_ts: str | None


class ApprovalVault:
    def __init__(self, sessions: sessionmaker[Session], approvers: set[str]) -> None:
        self._sessions = sessions
        self._approvers = approvers

    def conversation(self, conversation_id: str) -> str | None:
        with self._sessions() as session:
            row = session.get(ConversationRow, conversation_id)
            return None if row is None else row.state_json

    def save_conversation(self, conversation_id: str, state_json: str) -> None:
        with self._sessions() as session:
            session.merge(ConversationRow(id=conversation_id, state_json=state_json))
            session.commit()

    def stage(self, idempotency_key: str, order: DraftPurchaseOrder) -> StoredProposal:
        """Save the order, or return the existing row for this key. Never overwrites it."""
        session = self._sessions()
        try:
            existing = session.scalar(
                select(ProposalRow).where(ProposalRow.idempotency_key == idempotency_key)
            )
            if existing is not None:
                return _stored(existing)
            row = ProposalRow(
                id=str(uuid.uuid4()),
                idempotency_key=idempotency_key,
                action=ACTION,
                payload_json=order.canonical_json(),
                payload_hash=order.fingerprint(),
                status="proposed",
                approved_by=None,
                erp_document_name=None,
                slack_channel=None,
                slack_ts=None,
                created_at=_now(),
            )
            session.add(row)
            session.flush()
            _audit(session, row.id, "staged", "system", order.fingerprint())
            session.commit()
            return _stored(row)
        finally:
            session.close()

    def get_by_key(self, idempotency_key: str) -> StoredProposal | None:
        session = self._sessions()
        try:
            row = session.scalar(
                select(ProposalRow).where(ProposalRow.idempotency_key == idempotency_key)
            )
            return None if row is None else _stored(row)
        finally:
            session.close()

    def get(self, proposal_id: str) -> StoredProposal | None:
        session = self._sessions()
        try:
            row = session.get(ProposalRow, proposal_id)
            return None if row is None else _stored(row)
        finally:
            session.close()

    def replace(self, proposal_id: str, order: DraftPurchaseOrder, actor: str) -> StoredProposal:
        """Change a proposal that nobody has approved yet."""
        session = self._sessions()
        try:
            row = _require(session, proposal_id)
            if row.status != "proposed":
                _audit(session, row.id, "replace_denied", actor, row.status)
                session.commit()
                raise ApprovalError("immutable", "Approved parameters cannot be changed.")
            row.payload_json = order.canonical_json()
            row.payload_hash = order.fingerprint()
            _audit(session, row.id, "replaced", actor, row.payload_hash)
            session.commit()
            return _stored(row)
        finally:
            session.close()

    def remember_slack(self, proposal_id: str, channel: str, ts: str) -> StoredProposal:
        """Remember which Slack message shows this proposal, so a later reply can edit it."""
        session = self._sessions()
        try:
            row = _require(session, proposal_id)
            row.slack_channel = channel
            row.slack_ts = ts
            _audit(session, row.id, "slack_posted", "system", ts)
            session.commit()
            return _stored(row)
        finally:
            session.close()

    def supersede(
        self, proposal_id: str, reason: str = "slack message was not on file"
    ) -> StoredProposal:
        """Close an unapproved proposal whose Slack message can no longer be edited."""
        session = self._sessions()
        try:
            row = _require(session, proposal_id)
            if row.status != "proposed":
                _audit(session, row.id, "supersede_denied", "system", row.status)
                session.commit()
                raise ApprovalError("invalid_state", f"Cannot supersede a {row.status} proposal.")
            row.status = "rejected"
            _audit(session, row.id, "superseded", "system", reason)
            session.commit()
            return _stored(row)
        finally:
            session.close()

    def approve(self, proposal_id: str, user_id: str) -> StoredProposal:
        session = self._sessions()
        try:
            row = _require(session, proposal_id)
            _require_approver(session, row, user_id, "approve_denied", self._approvers)
            _require_intact(session, row, user_id)
            if row.status != "proposed":
                _audit(session, row.id, "approve_denied", user_id, row.status)
                session.commit()
                raise ApprovalError("invalid_state", f"Cannot approve a {row.status} proposal.")
            row.status = "approved"
            row.approved_by = user_id
            _audit(session, row.id, "approved", user_id, row.payload_hash)
            session.commit()
            return _stored(row)
        finally:
            session.close()

    def reject(self, proposal_id: str, user_id: str) -> StoredProposal:
        session = self._sessions()
        try:
            row = _require(session, proposal_id)
            _require_approver(session, row, user_id, "reject_denied", self._approvers)
            if row.status != "proposed":
                _audit(session, row.id, "reject_denied", user_id, row.status)
                session.commit()
                raise ApprovalError("invalid_state", f"Cannot reject a {row.status} proposal.")
            row.status = "rejected"
            _audit(session, row.id, "rejected", user_id, "")
            session.commit()
            return _stored(row)
        finally:
            session.close()

    def execute(
        self,
        proposal_id: str,
        user_id: str,
        writer: DraftPurchaseOrderWriter,
        *,
        submit: bool = False,
        hold_for_second_approver: bool = False,
    ) -> StoredProposal:
        """Create the ERPNext draft once. A second call returns the same document name."""
        session = self._sessions()
        try:
            row = _require(session, proposal_id)
            _require_approver(session, row, user_id, "execute_denied", self._approvers)
            _require_intact(session, row, user_id)
            if row.status == "executed":
                _audit(session, row.id, "execute_replayed", user_id, row.erp_document_name or "")
                session.commit()
                return _stored(row)
            if row.status == "executing":
                _audit(session, row.id, "execute_denied", user_id, "already started")
                session.commit()
                raise ApprovalError("invalid_state", "Execution already started.")
            if row.status != "approved":
                _audit(session, row.id, "execute_denied", user_id, row.status)
                session.commit()
                raise ApprovalError("invalid_state", "Only an approved proposal can be executed.")
            order = DraftPurchaseOrder.model_validate_json(row.payload_json)
            row.status = "executing"
            _audit(session, row.id, "execution_started", user_id, row.payload_hash)
            session.commit()
            document_name = writer.create_draft(order)
            row.erp_document_name = document_name
            row.status = "drafted"
            _audit(session, row.id, "drafted", user_id, document_name)
            session.commit()
            if hold_for_second_approver:
                return _stored(row)
            if submit:
                return _finish_submit(session, row, user_id, writer)
            row.status = "executed"
            _audit(session, row.id, "executed", user_id, document_name)
            session.commit()
            return _stored(row)
        finally:
            session.close()

    def submit_draft(
        self,
        proposal_id: str,
        user_id: str,
        writer: DraftPurchaseOrderWriter,
        *,
        allow_same_approver: bool = False,
    ) -> StoredProposal:
        """Submit a draft already created for an over-limit order. The first approver cannot."""
        session = self._sessions()
        try:
            row = _require(session, proposal_id)
            _require_approver(session, row, user_id, "submit_denied", self._approvers)
            _require_intact(session, row, user_id)
            if row.status == "executed" and row.erp_document_name:
                return _stored(row)
            if row.status == "executing" and row.erp_document_name:
                return _finish_submit(session, row, user_id, writer)
            if row.status != "drafted" or not row.erp_document_name:
                _audit(session, row.id, "submit_denied", user_id, row.status)
                session.commit()
                raise ApprovalError("invalid_state", "There is no draft waiting for submission.")
            if user_id == row.approved_by and not allow_same_approver:
                _audit(session, row.id, "submit_denied", user_id, "same approver")
                session.commit()
                raise ApprovalError(
                    "second_approver",
                    "A second approver has to submit an order over the limit.",
                )
            name = row.erp_document_name
            row.status = "executing"
            _audit(session, row.id, "submission_started", user_id, name)
            session.commit()
            return _finish_submit(session, row, user_id, writer)
        finally:
            session.close()


def _finish_submit(
    session: Session,
    row: ProposalRow,
    user_id: str,
    writer: DraftPurchaseOrderWriter,
) -> StoredProposal:
    name = row.erp_document_name or ""
    try:
        writer.submit(name)
    except Exception as exc:
        row.status = "drafted"
        _audit(session, row.id, "submit_failed", user_id, str(exc)[:240])
        session.commit()
        raise ApprovalError("erp", f"ERPNext did not submit the draft: {exc}") from exc
    row.status = "executed"
    _audit(session, row.id, "submitted", user_id, name)
    session.commit()
    return _stored(row)


def init_database(url: str) -> sessionmaker[Session]:
    from sqlalchemy import create_engine

    engine = create_engine(url)
    Base.metadata.create_all(engine)
    _ensure_slack_columns(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _ensure_slack_columns(engine: Engine) -> None:
    present = {column["name"] for column in inspect(engine).get_columns("proposals")}
    with engine.begin() as connection:
        if "slack_channel" not in present:
            connection.execute(text("ALTER TABLE proposals ADD COLUMN slack_channel VARCHAR(64)"))
        if "slack_ts" not in present:
            connection.execute(text("ALTER TABLE proposals ADD COLUMN slack_ts VARCHAR(64)"))


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(session: Session, proposal_id: str, event: str, actor: str, detail: str) -> None:
    session.add(
        AuditEventRow(
            proposal_id=proposal_id,
            event=event,
            actor=actor,
            detail=detail,
            created_at=_now(),
        )
    )


def _require(session: Session, proposal_id: str) -> ProposalRow:
    row = session.get(ProposalRow, proposal_id)
    if row is None:
        raise ApprovalError("not_found", "Proposal does not exist.")
    return row


def _require_approver(
    session: Session,
    row: ProposalRow,
    user_id: str,
    event: str,
    approvers: set[str],
) -> None:
    if user_id in approvers:
        return
    _audit(session, row.id, event, user_id, "not an approver")
    session.commit()
    raise ApprovalError("unauthorized", "This user cannot approve or execute ERP writes.")


def _require_intact(session: Session, row: ProposalRow, user_id: str) -> None:
    try:
        order = DraftPurchaseOrder.model_validate_json(row.payload_json)
    except ValueError:
        order = None
    if order is None or order.fingerprint() != row.payload_hash:
        _audit(session, row.id, "tamper_detected", user_id, row.payload_hash)
        session.commit()
        raise ApprovalError("tampered", "Stored parameters do not match their fingerprint.")


def _stored(row: ProposalRow) -> StoredProposal:
    return StoredProposal(
        id=row.id,
        idempotency_key=row.idempotency_key,
        status=row.status,
        payload=DraftPurchaseOrder.model_validate_json(row.payload_json),
        payload_hash=row.payload_hash,
        approved_by=row.approved_by,
        erp_document_name=row.erp_document_name,
        slack_channel=row.slack_channel,
        slack_ts=row.slack_ts,
    )
