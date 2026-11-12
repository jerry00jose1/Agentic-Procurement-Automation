from datetime import date

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from procurement_agent.approvals.models import Base
from procurement_agent.approvals.orders import DraftPurchaseOrder, PurchaseOrderLine
from procurement_agent.approvals.service import ApprovalVault
from procurement_agent.slack.decisions import apply_decision
from procurement_agent.slack.messages import button_values, proposal_message


class _Writer:
    def __init__(self) -> None:
        self.calls = 0

    def create_draft(self, order: DraftPurchaseOrder) -> str:
        self.calls += 1
        return "PUR-ORD-2026-01000"


def _vault() -> ApprovalVault:
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
    return ApprovalVault(sessions, {"U-APPROVER"})


def _stage(vault: ApprovalVault):
    order = DraftPurchaseOrder(
        supplier="Jebel Enterprises LLC 120",
        company="Northwind Components",
        warehouse="Stores - NC",
        transaction_date=date(2026, 11, 12),
        schedule_date=date(2026, 11, 26),
        items=[PurchaseOrderLine(item_code="S-66100", qty=1, rate=3119.43)],
    )
    return vault.stage("replacement:line", order)


def test_buttons_carry_only_the_proposal_id() -> None:
    vault = _vault()
    stored = _stage(vault)
    _text, blocks = proposal_message(stored, late_supplier="Oasis Industries LLC 107")
    assert button_values(blocks) == [stored.id, stored.id]
    assert "3119.43" not in "".join(button_values(blocks))


def test_approve_creates_one_draft_and_a_second_click_does_not() -> None:
    vault = _vault()
    stored = _stage(vault)
    writer = _Writer()
    first = apply_decision(
        vault,
        writer,
        action="approve_proposal",
        proposal_id=stored.id,
        user_id="U-APPROVER",
    )
    second = apply_decision(
        vault,
        writer,
        action="approve_proposal",
        proposal_id=stored.id,
        user_id="U-APPROVER",
    )
    assert first == "Created draft purchase order PUR-ORD-2026-01000."
    assert "No second purchase order" in second
    assert writer.calls == 1


def test_stranger_cannot_approve() -> None:
    vault = _vault()
    stored = _stage(vault)
    writer = _Writer()
    result = apply_decision(
        vault,
        writer,
        action="approve_proposal",
        proposal_id=stored.id,
        user_id="U-STRANGER",
    )
    assert "cannot approve" in result
    assert writer.calls == 0
    saved = vault.get(stored.id)
    assert saved is not None
    assert saved.status == "proposed"
