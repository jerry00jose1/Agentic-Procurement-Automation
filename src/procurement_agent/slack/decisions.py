"""Turn a Slack button into a vault call. The button value is reread from Postgres."""

from __future__ import annotations

from procurement_agent.approvals.errors import ApprovalError
from procurement_agent.approvals.service import (
    ApprovalVault,
    DraftPurchaseOrderWriter,
    StoredProposal,
)


def apply_decision(
    vault: ApprovalVault,
    writer: DraftPurchaseOrderWriter,
    *,
    action: str,
    proposal_id: str,
    user_id: str,
    submit_limit: float = 5000,
) -> str:
    if action == "reject_proposal":
        return _reject(vault, proposal_id, user_id)
    if action in {"approve_proposal", "submit_proposal"}:
        return _approve(vault, writer, proposal_id, user_id, submit_limit)
    return "Unknown action."


def _reject(vault: ApprovalVault, proposal_id: str, user_id: str) -> str:
    try:
        vault.reject(proposal_id, user_id)
    except ApprovalError as exc:
        return str(exc)
    return "No order created."


def _approve(
    vault: ApprovalVault,
    writer: DraftPurchaseOrderWriter,
    proposal_id: str,
    user_id: str,
    submit_limit: float,
) -> str:
    before = vault.get(proposal_id)
    if before is not None and before.status == "executed" and before.erp_document_name:
        return f"Already created {before.erp_document_name}. No second purchase order."
    within_limit = _within_limit(before, submit_limit)
    try:
        if before is not None and before.status in {"drafted", "executing"}:
            stored = vault.submit_draft(
                proposal_id,
                user_id,
                writer,
                allow_same_approver=within_limit,
            )
        else:
            if before is None or before.status == "proposed":
                vault.approve(proposal_id, user_id)
            stored = vault.execute(
                proposal_id,
                user_id,
                writer,
                submit=within_limit,
                hold_for_second_approver=not within_limit,
            )
    except ApprovalError as exc:
        return str(exc)
    if stored.status == "drafted":
        return (
            f"Created draft purchase order {stored.erp_document_name}. "
            f"The total is over {submit_limit:,.0f} {stored.payload.currency}, "
            "so a second approver must submit it."
        )
    return f"Submitted purchase order {stored.erp_document_name}."


def _within_limit(stored: StoredProposal | None, limit: float) -> bool:
    if stored is None:
        return False
    return stored.payload.line_total() <= limit
