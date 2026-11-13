from datetime import date, timedelta

from procurement_agent.agent.item_request import order_for, seller_facts

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


def test_seller_facts_are_recorded_prices_and_delivery_counts() -> None:
    books = _sample()
    facts = {
        fact.supplier: fact
        for fact in seller_facts(
            "ITEM-A",
            books.orders,
            books.items,
            books.receipts,
            books.receipt_items,
        )
    }
    assert set(facts) == {"Good Co", "Late Co"}
    assert facts["Good Co"].qty == 8
    assert facts["Good Co"].rate == 7
    assert facts["Good Co"].scored_lines == 5
    assert facts["Good Co"].on_time_rate == 1
    assert facts["Late Co"].qty == 12
    assert facts["Late Co"].rate == 5
    assert facts["Late Co"].on_time_rate == 0


def test_an_order_uses_the_row_rate() -> None:
    books = _sample()
    fact = next(
        fact
        for fact in seller_facts(
            "ITEM-A", books.orders, books.items, books.receipts, books.receipt_items
        )
        if fact.supplier == "Good Co"
    )
    order = order_for(fact, "ITEM-A", 10, TODAY)
    assert order.items[0].qty == 10
    assert order.items[0].rate == 7
    assert order.schedule_date == TODAY + timedelta(days=14)

