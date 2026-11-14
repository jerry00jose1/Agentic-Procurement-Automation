from datetime import date

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from procurement_agent.approvals.models import Base
from procurement_agent.approvals.orders import DraftPurchaseOrder, PurchaseOrderLine
from procurement_agent.approvals.service import ApprovalVault
from procurement_agent.slack.decisions import apply_decision
from procurement_agent.slack.messages import button_values, order_card


class _Writer:
    def __init__(self) -> None:
        self.calls = 0
        self.submissions = 0

    def create_draft(self, order: DraftPurchaseOrder) -> str:
        self.calls += 1
        return "PUR-ORD-2026-01000"

    def submit(self, name: str) -> None:
        self.submissions += 1


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
    return vault.stage("request:line:1", order)


def test_buttons_carry_only_the_proposal_id() -> None:
    vault = _vault()
    stored = _stage(vault)
    _text, blocks = order_card("Buy S-66100 from Jebel.", stored)
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
    assert first == "Submitted purchase order PUR-ORD-2026-01000."
    assert "No second purchase order" in second
    assert writer.calls == 1
    assert writer.submissions == 1


def test_an_order_over_5000_needs_a_different_approver() -> None:
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
    vault = ApprovalVault(sessionmaker(bind=engine, expire_on_commit=False), {"U-A", "U-B"})
    stored = vault.stage(
        "request:over:1",
        DraftPurchaseOrder(
            supplier="Jebel Enterprises LLC 120",
            company="Northwind Components",
            warehouse="Stores - NC",
            transaction_date=date(2026, 11, 12),
            schedule_date=date(2026, 11, 26),
            items=[PurchaseOrderLine(item_code="S-66100", qty=2, rate=3119.43)],
        ),
    )
    writer = _Writer()
    first = apply_decision(
        vault, writer, action="approve_proposal", proposal_id=stored.id, user_id="U-A"
    )
    blocked = apply_decision(
        vault, writer, action="submit_proposal", proposal_id=stored.id, user_id="U-A"
    )
    second = apply_decision(
        vault, writer, action="submit_proposal", proposal_id=stored.id, user_id="U-B"
    )
    assert "second approver must submit it" in first
    assert "second approver has to submit" in blocked
    assert second == "Submitted purchase order PUR-ORD-2026-01000."
    assert writer.calls == 1
    assert writer.submissions == 1


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
