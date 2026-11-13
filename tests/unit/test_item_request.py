from datetime import date, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from procurement_agent.agent.buyer_request import BuyerRequest, default_request, mentioned_item_code
from procurement_agent.agent.item_request import (
    ItemPurchase,
    build_item_purchase,
    file_item_purchase,
)
from procurement_agent.approvals.models import Base
from procurement_agent.approvals.service import ApprovalVault
from procurement_agent.slack.messages import button_labels, purchase_message

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
    purchase = _purchase(books, default_request("ITEM-A"))
    assert purchase is not None
    assert purchase.supplier == "Good Co"
    assert purchase.order.items[0].qty == 8
    assert purchase.order.items[0].rate == 7
    assert purchase.order.schedule_date == TODAY + timedelta(days=14)
    assert [row.supplier for row in purchase.considered] == ["Good Co", "Late Co"]
    assert purchase.considered[0].eligible is True
    assert purchase.considered[1].eligible is True
    stored = file_item_purchase(_vault(), purchase).stored
    text, blocks = purchase_message(stored, purchase)
    assert "Good Co was on time for 100% of 5 deliveries" in text
    assert "Late Co was on time for 0% of 5 deliveries" in text
    assert "Good Co wins." in text
    assert "Late Co is 2 EUR cheaper, and price was not the rule." in text
    assert text.index("Good Co wins.") < text.index("Draft purchase order")
    assert button_labels(blocks) == ["Create draft PO", "Don't order"]


def test_an_exclusion_removes_that_supplier_before_the_ranking() -> None:
    books = _sample()
    purchase = _purchase(books, BuyerRequest("ITEM-A", "on_time", ("Good Co",), None, None))
    assert purchase is not None
    assert purchase.supplier == "Late Co"
    assert purchase.order.items[0].rate == 5
    assert purchase.order.items[0].qty == 12


def test_lowest_price_stays_inside_the_five_delivery_rule() -> None:
    books = _sample()
    books.add("Short Co", "ITEM-A", 3, 1, late_days=0, when=date(2024, 4, 1))
    books.add("Short Co", "ITEM-A", 3, 1, late_days=0, when=date(2024, 4, 2))
    purchase = _purchase(books, BuyerRequest("ITEM-A", "lowest_price", (), None, None))
    assert purchase is not None
    assert purchase.supplier == "Late Co"
    assert purchase.order.items[0].rate == 5


def test_a_named_supplier_can_override_the_five_delivery_rule() -> None:
    books = _sample()
    books.add("Short Co", "ITEM-A", 3, 1, late_days=0, when=date(2024, 4, 1))
    books.add("Short Co", "ITEM-A", 3, 1, late_days=0, when=date(2024, 4, 2))
    purchase = _purchase(books, BuyerRequest("ITEM-A", "named_supplier", (), "Short Co", None))
    assert purchase is not None
    assert purchase.supplier == "Short Co"
    assert purchase.order.items[0].rate == 1
    assert purchase.considered[0].eligible is False


def test_a_stated_quantity_replaces_the_latest_order_quantity() -> None:
    books = _sample()
    purchase = _purchase(books, BuyerRequest("ITEM-A", "on_time", (), None, 10))
    assert purchase is not None
    assert purchase.supplier == "Good Co"
    assert purchase.order.items[0].qty == 10
    assert purchase.order.items[0].rate == 7
    assert purchase.qty_from_buyer is True


def test_without_scored_deliveries_the_most_recent_seller_is_used() -> None:
    orders = [
        {
            "name": "PO-1",
            "supplier": "Old Co",
            "transaction_date": "2024-01-01",
            "company": "Northwind Components",
        },
        {
            "name": "PO-2",
            "supplier": "New Co",
            "transaction_date": "2024-06-01",
            "company": "Northwind Components",
        },
    ]
    items = [
        {
            "name": "L1",
            "parent": "PO-1",
            "item_code": "ITEM-A",
            "qty": 1,
            "rate": 4,
            "warehouse": "Stores - NC",
        },
        {
            "name": "L2",
            "parent": "PO-2",
            "item_code": "ITEM-A",
            "qty": 2,
            "rate": 9,
            "warehouse": "Stores - NC",
        },
    ]
    purchase = build_item_purchase(orders, items, [], [], default_request("ITEM-A"), today=TODAY)
    assert purchase is not None
    assert purchase.supplier == "New Co"
    assert purchase.order.items[0].qty == 2
    assert purchase.order.items[0].rate == 9


def test_unknown_item_has_no_purchase() -> None:
    books = _sample()
    assert _purchase(books, default_request("MISSING")) is None


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
    purchase = _purchase(books, default_request("ITEM-A"))
    assert purchase is not None
    vault = _vault()
    first = file_item_purchase(vault, purchase)
    vault.reject(first.stored.id, "U-APPROVER")
    second = file_item_purchase(vault, purchase)
    assert second.stored.id != first.stored.id
    assert second.stored.status == "proposed"


def test_a_follow_up_edits_the_card_already_posted() -> None:
    books = _sample()
    original = _purchase(books, default_request("ITEM-A"))
    revised = _purchase(books, BuyerRequest("ITEM-A", "on_time", ("Good Co",), None, None))
    assert original is not None and revised is not None
    vault = _vault()
    first = file_item_purchase(vault, original)
    vault.remember_slack(first.stored.id, "C1", "111.222")
    second = file_item_purchase(vault, revised)
    assert second.stored.id == first.stored.id
    assert second.posted_anew is False
    assert second.stored.slack_ts == "111.222"
    assert second.stored.payload.supplier == "Late Co"
    assert second.stored.payload_hash == revised.order.fingerprint()


def test_a_follow_up_without_a_card_closes_the_old_proposal() -> None:
    books = _sample()
    original = _purchase(books, default_request("ITEM-A"))
    revised = _purchase(books, BuyerRequest("ITEM-A", "on_time", (), None, 10))
    assert original is not None and revised is not None
    vault = _vault()
    first = file_item_purchase(vault, original)
    second = file_item_purchase(vault, revised)
    closed = vault.get(first.stored.id)
    assert closed is not None
    assert closed.status == "rejected"
    assert second.stored.id != first.stored.id
    assert second.posted_anew is True
    assert second.notice == "The previous card for this item no longer applies."
    assert second.stored.payload.items[0].qty == 10


def _purchase(books: _Books, request: BuyerRequest) -> ItemPurchase | None:
    return build_item_purchase(
        books.orders,
        books.items,
        books.receipts,
        books.receipt_items,
        request,
        today=TODAY,
    )
