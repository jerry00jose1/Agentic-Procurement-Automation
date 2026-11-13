from datetime import date

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from procurement_agent.agent.buyer_request import BuyerRequest
from procurement_agent.approvals.models import Base
from procurement_agent.approvals.service import ApprovalVault
from procurement_agent.slack.item_reply import reply_to_text
from procurement_agent.slack.messages import button_labels, button_values

TODAY = date(2026, 11, 12)


class _Reader:
    def __init__(self, request: BuyerRequest) -> None:
        self.request = request

    def read(self, text: str, item_codes: set[str], suppliers: set[str]) -> BuyerRequest:
        return self.request


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
    return ApprovalVault(sessionmaker(bind=engine, expire_on_commit=False), set())


def _books() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    orders = [
        {
            "name": "PO-1",
            "supplier": "Good Co",
            "transaction_date": "2024-05-01",
            "company": "Northwind Components",
        },
        {
            "name": "PO-2",
            "supplier": "Late Co",
            "transaction_date": "2024-03-01",
            "company": "Northwind Components",
        },
    ]
    items = [
        {
            "name": "L1",
            "parent": "PO-1",
            "item_code": "ITEM-A",
            "qty": 8,
            "rate": 7,
            "warehouse": "Stores - NC",
        },
        {
            "name": "L2",
            "parent": "PO-2",
            "item_code": "ITEM-A",
            "qty": 12,
            "rate": 5,
            "warehouse": "Stores - NC",
        },
    ]
    return orders, items


def test_naming_an_item_files_a_proposal() -> None:
    orders, items = _books()
    vault = _vault()
    reader = _Reader(BuyerRequest("ITEM-A", "on_time", (), None, None))
    answer = reply_to_text(vault, "I need ITEM-A", orders, items, [], [], reader, today=TODAY)
    assert "You asked for ITEM-A." in answer.text
    assert "2 suppliers have sold ITEM-A." in answer.text
    assert "Good Co is the most recent seller." in answer.text
    assert "*Draft purchase order*" in answer.text
    assert "ITEM-A · qty 8 · 7 EUR" in answer.text
    assert answer.text.index("You asked for ITEM-A.") < answer.text.index("Draft purchase order")
    stored = vault.get_by_key("item:ITEM-A")
    assert stored is not None
    assert answer.blocks is not None
    assert button_values(answer.blocks) == [stored.id, stored.id]
    assert button_labels(answer.blocks) == ["Create draft PO", "Don't order"]
    assert answer.proposal_id == stored.id


def test_a_message_without_an_item_asks_for_one() -> None:
    reader = _Reader(BuyerRequest(None, "on_time", (), None, None))
    answer = reply_to_text(_vault(), "hello", [], [], [], [], reader, today=TODAY)
    assert answer.blocks is None
    assert answer.proposal_id is None
    assert "Name an item" in answer.text


def test_an_exclusion_rewrites_the_same_card() -> None:
    orders, items = _books()
    vault = _vault()
    first = reply_to_text(
        vault,
        "I need ITEM-A",
        orders,
        items,
        [],
        [],
        _Reader(BuyerRequest("ITEM-A", "on_time", (), None, None)),
        today=TODAY,
    )
    assert first.proposal_id is not None
    vault.remember_slack(first.proposal_id, "C1", "111.222")
    second = reply_to_text(
        vault,
        "I need ITEM-A, not from Good Co",
        orders,
        items,
        [],
        [],
        _Reader(BuyerRequest("ITEM-A", "on_time", ("Good Co",), None, None)),
        today=TODAY,
    )
    assert second.proposal_id == first.proposal_id
    assert second.slack_channel == "C1"
    assert second.slack_ts == "111.222"
    assert "not from Good Co" in second.text
    assert "Excluded: Good Co." in second.text
    assert "Late Co" in second.text
    assert "qty 12 · 5 EUR" in second.text
