from datetime import date

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from procurement_agent.approvals.errors import ApprovalError
from procurement_agent.approvals.models import Base, ProposalRow
from procurement_agent.approvals.orders import DraftPurchaseOrder, PurchaseOrderLine
from procurement_agent.approvals.service import ApprovalVault


def _order(supplier: str = "Delta Distribution LLC 068") -> DraftPurchaseOrder:
    return DraftPurchaseOrder(
        supplier=supplier,
        company="Northwind Components",
        warehouse="Stores - NC",
        transaction_date=date(2024, 6, 1),
        schedule_date=date(2024, 6, 15),
        items=[PurchaseOrderLine(item_code="M01-01", qty=10, rate=4.5)],
    )


class FakeWriter:
    def __init__(self) -> None:
        self.calls: list[DraftPurchaseOrder] = []

    def create_draft(self, order: DraftPurchaseOrder) -> str:
        self.calls.append(order)
        return f"PUR-ORD-TEST-{len(self.calls)}"


@pytest.fixture
def vault() -> tuple[ApprovalVault, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    return ApprovalVault(sessions, {"U-APPROVER"}), sessions


def test_stage_keeps_the_exact_order_and_replays_the_same_key(
    vault: tuple[ApprovalVault, sessionmaker[Session]],
) -> None:
    store, _sessions = vault
    first = store.stage("req-1", _order())
    second = store.stage("req-1", _order(supplier="Someone Else"))
    assert first.id == second.id
    assert second.payload.supplier == "Delta Distribution LLC 068"
    assert second.payload.items[0].qty == 10
    assert second.payload.items[0].rate == 4.5


def test_stranger_cannot_approve(vault: tuple[ApprovalVault, sessionmaker[Session]]) -> None:
    store, _sessions = vault
    proposal = store.stage("req-2", _order())
    with pytest.raises(ApprovalError) as caught:
        store.approve(proposal.id, "U-STRANGER")
    assert caught.value.code == "unauthorized"
    assert store.stage("req-2", _order()).status == "proposed"


def test_approved_parameters_cannot_be_changed(
    vault: tuple[ApprovalVault, sessionmaker[Session]],
) -> None:
    store, sessions = vault
    proposal = store.stage("req-3", _order())
    store.approve(proposal.id, "U-APPROVER")
    with pytest.raises(ApprovalError) as caught:
        store.replace(proposal.id, _order(supplier="Changed Supplier"), "U-APPROVER")
    assert caught.value.code == "immutable"

    with sessions() as session:
        row = session.get(ProposalRow, proposal.id)
        assert row is not None
        row.payload_json = row.payload_json.replace(
            "Delta Distribution LLC 068",
            "Tampered Supplier",
        )
        session.commit()
    writer = FakeWriter()
    with pytest.raises(ApprovalError) as caught:
        store.execute(proposal.id, "U-APPROVER", writer)
    assert caught.value.code == "tampered"
    assert writer.calls == []


def test_executing_twice_creates_one_erp_document(
    vault: tuple[ApprovalVault, sessionmaker[Session]],
) -> None:
    store, _sessions = vault
    writer = FakeWriter()
    proposal = store.stage("req-4", _order())
    store.approve(proposal.id, "U-APPROVER")
    with pytest.raises(ApprovalError) as caught:
        store.approve(proposal.id, "U-APPROVER")
    assert caught.value.code == "invalid_state"

    first = store.execute(proposal.id, "U-APPROVER", writer)
    second = store.execute(proposal.id, "U-APPROVER", writer)
    assert first.erp_document_name == "PUR-ORD-TEST-1"
    assert second.erp_document_name == "PUR-ORD-TEST-1"
    assert len(writer.calls) == 1


def test_a_failed_send_cannot_be_retried_into_a_second_order(
    vault: tuple[ApprovalVault, sessionmaker[Session]],
) -> None:
    store, _sessions = vault
    proposal = store.stage("req-5", _order())
    store.approve(proposal.id, "U-APPROVER")

    class FailingWriter:
        def create_draft(self, order: DraftPurchaseOrder) -> str:
            raise RuntimeError("ERPNext did not answer")

    with pytest.raises(RuntimeError):
        store.execute(proposal.id, "U-APPROVER", FailingWriter())
    assert store.stage("req-5", _order()).status == "executing"

    retry = FakeWriter()
    with pytest.raises(ApprovalError) as caught:
        store.execute(proposal.id, "U-APPROVER", retry)
    assert caught.value.code == "invalid_state"
    assert retry.calls == []
