from datetime import date

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from procurement_agent.approvals.models import Base
from procurement_agent.approvals.service import ApprovalVault
from procurement_agent.slack.item_reply import reply_to_text
from procurement_agent.slack.messages import button_values

TODAY = date(2026, 11, 12)


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


def test_naming_an_item_files_a_proposal() -> None:
    orders = [
        {
            "name": "PO-1",
            "supplier": "Good Co",
            "transaction_date": "2024-05-01",
            "company": "Northwind Components",
        }
    ]
    items = [
        {
            "name": "L1",
            "parent": "PO-1",
            "item_code": "ITEM-A",
            "qty": 8,
            "rate": 7,
            "warehouse": "Stores - NC",
        }
    ]
    vault = _vault()
    answer = reply_to_text(vault, "I need ITEM-A", orders, items, [], [], today=TODAY)
    assert answer is not None
    text, blocks = answer
    assert "Buy ITEM-A from Good Co: qty 8, rate 7 EUR." in text
    assert "1 supplier has sold ITEM-A." in text
    assert "Good Co is the only supplier on record for this item." in text
    assert "Good Co: no scored deliveries, latest rate 7 (chosen)" in text
    stored = vault.get_by_key("item:ITEM-A")
    assert stored is not None
    assert blocks is not None
    assert button_values(blocks) == [stored.id, stored.id]


def test_a_message_without_an_item_is_ignored() -> None:
    assert reply_to_text(_vault(), "hello", [], [], [], [], today=TODAY) is None
