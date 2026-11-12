from datetime import date, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from procurement_agent.agent.item_request import (
    build_item_purchase,
    file_item_purchase,
    mentioned_item_code,
)
from procurement_agent.approvals.models import Base
from procurement_agent.approvals.service import ApprovalVault

TODAY = date(2026, 11, 12)
NEED = date(2024, 6, 1)


class _Books:
    def __init__(self) -> None:
        self.orders: list[dict[str, object]] = []
        self.items: list[dict[str, object]] = []
        self.receipts: list[dict[str, object]] = []
        self.receipt_items: list[dict[str, object]] = []
        self._n = 0

    def add(
        self,
        supplier: str,
        item_code: str,
        qty: float,
        rate: float,
        *,
        late_days: int,
        when: date,
    ) -> None:
        self._n += 1
        purchase_order = f"PO-{self._n}"
        line = f"LINE-{self._n}"
        receipt = f"PR-{self._n}"
        self.orders.append(
            {
                "name": purchase_order,
                "supplier": supplier,
                "transaction_date": when.isoformat(),
                "company": "Northwind Components",
                "docstatus": 1,
            }
        )
        self.items.append(
            {
                "name": line,
                "parent": purchase_order,
                "item_code": item_code,
                "qty": qty,
                "rate": rate,
                "schedule_date": NEED.isoformat(),
                "description": f"need_by:{NEED.isoformat()}",
                "warehouse": "Stores - NC",
            }
        )
        self.receipts.append(
            {"name": receipt, "posting_date": (NEED + timedelta(days=late_days)).isoformat()}
        )
        self.receipt_items.append(
            {"name": f"RI-{self._n}", "parent": receipt, "purchase_order_item": line}
        )


def _sample() -> _Books:
    books = _Books()
    for offset in range(4):
        books.add("Late Co", f"OTHER-{offset}", 1, 2, late_days=3, when=date(2024, 1, 1))
    books.add("Late Co", "ITEM-A", 12, 5, late_days=10, when=date(2024, 3, 1))
    books.add("Good Co", "ITEM-A", 4, 9, late_days=0, when=date(2024, 1, 1))
    books.add("Good Co", "ITEM-A", 8, 7, late_days=0, when=date(2024, 5, 1))
    for offset in range(3):
        books.add("Good Co", f"GOOD-{offset}", 1, 2, late_days=0, when=date(2024, 2, 1))
    return books


def test_named_text_finds_the_longest_item_code() -> None:
    assert mentioned_item_code("I need ITEM-A please", {"ITEM", "ITEM-A"}) == "ITEM-A"
    assert mentioned_item_code("hello", {"ITEM-A"}) is None


def test_purchase_uses_the_better_suppliers_latest_price() -> None:
    books = _sample()
    purchase = build_item_purchase(
        "ITEM-A",
        books.orders,
        books.items,
        books.receipts,
        books.receipt_items,
        today=TODAY,
    )
    assert purchase is not None
    assert purchase.supplier == "Good Co"
    assert purchase.order.items[0].qty == 8
    assert purchase.order.items[0].rate == 7
    assert purchase.order.schedule_date == TODAY + timedelta(days=14)
    assert [row.supplier for row in purchase.considered] == ["Good Co", "Late Co"]
    assert purchase.considered[0].eligible is True
    assert purchase.considered[1].eligible is True


def test_unknown_item_has_no_purchase() -> None:
    books = _sample()
    assert (
        build_item_purchase(
            "MISSING",
            books.orders,
            books.items,
            books.receipts,
            books.receipt_items,
            today=TODAY,
        )
        is None
    )


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
    return ApprovalVault(sessionmaker(bind=engine, expire_on_commit=False), {"U-APPROVER"})


def test_a_rejected_item_can_be_requested_again() -> None:
    books = _sample()
    purchase = build_item_purchase(
        "ITEM-A",
        books.orders,
        books.items,
        books.receipts,
        books.receipt_items,
        today=TODAY,
    )
    assert purchase is not None
    vault = _vault()
    first = file_item_purchase(vault, purchase)
    vault.reject(first.id, "U-APPROVER")
    second = file_item_purchase(vault, purchase)
    assert second.id != first.id
    assert second.status == "proposed"
