from datetime import date, timedelta

from procurement_agent.agent.case import build_replacement_case

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
        warehouse: str = "Stores - NC",
    ) -> str:
        self._n += 1
        purchase_order = f"PO-{self._n}"
        line = f"LINE-{self._n}"
        receipt = f"PR-{self._n}"
        received = NEED + timedelta(days=late_days)
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
                "warehouse": warehouse,
            }
        )
        self.receipts.append(
            {"name": receipt, "posting_date": received.isoformat(), "docstatus": 1}
        )
        self.receipt_items.append(
            {"name": f"RI-{self._n}", "parent": receipt, "purchase_order_item": line}
        )
        return line


def _sample() -> _Books:
    books = _Books()
    for offset in range(4):
        books.add(
            "Late Co",
            f"OTHER-{offset}",
            1,
            2,
            late_days=3,
            when=date(2024, 1, 1),
        )
    books.add("Late Co", "ITEM-A", 12, 5, late_days=10, when=date(2024, 3, 1))
    books.add("Good Co", "ITEM-A", 4, 9, late_days=0, when=date(2024, 1, 1))
    books.add("Good Co", "ITEM-A", 4, 7, late_days=0, when=date(2024, 5, 1))
    for offset in range(3):
        books.add("Good Co", f"GOOD-{offset}", 1, 2, late_days=0, when=date(2024, 2, 1))
    books.add("Cheap Co", "ITEM-A", 4, 1, late_days=0, when=date(2024, 6, 1))
    for offset in range(4):
        books.add(
            "Cheap Co",
            f"CHEAP-{offset}",
            1,
            1,
            late_days=2,
            when=date(2024, 2, 1),
        )
    books.add("Tiny Co", "ITEM-A", 99, 1, late_days=0, when=date(2024, 6, 1))
    return books


def test_replacement_uses_the_better_suppliers_latest_rate() -> None:
    books = _sample()
    case = build_replacement_case(
        books.orders,
        books.items,
        books.receipts,
        books.receipt_items,
        today=TODAY,
    )
    assert case is not None
    assert case.late_supplier == "Late Co"
    assert case.alternative_supplier == "Good Co"
    assert case.order.items[0].item_code == "ITEM-A"
    assert case.order.items[0].qty == 12
    assert case.order.items[0].rate == 7
    assert case.order.transaction_date == TODAY
    assert case.order.schedule_date == TODAY + timedelta(days=14)
    assert case.case_id == "replacement:LINE-5"


def test_no_case_when_nobody_reliable_sells_the_item() -> None:
    books = _Books()
    for _offset in range(5):
        books.add("Late Co", "ONLY-LATE", 3, 5, late_days=4, when=date(2024, 1, 1))
    books.add("Tiny Co", "ONLY-LATE", 3, 1, late_days=0, when=date(2024, 2, 1))
    case = build_replacement_case(
        books.orders,
        books.items,
        books.receipts,
        books.receipt_items,
        today=TODAY,
    )
    assert case is None
