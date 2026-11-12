"""Choose one late supplier and one better supplier from purchasing records.

The numbers come from ERPNext documents. A model does not pick the quantity or the price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from procurement_agent.approvals.orders import DraftPurchaseOrder, PurchaseOrderLine
from procurement_agent.domain.exceptions import (
    SupplierPerformance,
    _as_date,
    _need_by,
    supplier_performance,
)

MIN_SCORED_LINES = 5
_LEAD_TIME_DAYS = 14


@dataclass(frozen=True)
class ReplacementCase:
    case_id: str
    late_supplier: str
    late_lines: int
    scored_lines: int
    on_time_rate: float
    avg_late_days: float
    alternative_supplier: str
    alternative_on_time_rate: float
    source_order: str
    source_line: str
    order: DraftPurchaseOrder

    def public_view(self) -> dict[str, object]:
        line = self.order.items[0]
        return {
            "case_id": self.case_id,
            "late_supplier": self.late_supplier,
            "late_lines": self.late_lines,
            "scored_lines": self.scored_lines,
            "on_time_rate": self.on_time_rate,
            "avg_late_days": self.avg_late_days,
            "alternative_supplier": self.alternative_supplier,
            "alternative_on_time_rate": self.alternative_on_time_rate,
            "source_order": self.source_order,
            "source_line": self.source_line,
            "item_code": line.item_code,
            "qty": line.qty,
            "rate": line.rate,
            "currency": self.order.currency,
            "company": self.order.company,
            "warehouse": self.order.warehouse,
            "transaction_date": self.order.transaction_date.isoformat(),
            "schedule_date": self.order.schedule_date.isoformat(),
        }


def build_replacement_case(
    orders: list[dict[str, Any]],
    order_items: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    receipt_items: list[dict[str, Any]],
    *,
    today: date,
) -> ReplacementCase | None:
    """Worst supplier with at least five scored lines, plus a better supplier of that item."""
    performance = supplier_performance(order_items, orders, receipt_items, receipts)
    late_suppliers = [
        row for row in performance if row.lines >= MIN_SCORED_LINES and row.late_lines > 0
    ]
    by_supplier = {row.supplier: row for row in performance}
    supplier_of = {row["name"]: row.get("supplier") or "" for row in orders}
    order_of = {row["name"]: row for row in orders}
    received_on = _receipt_dates(receipts, receipt_items)

    for late in late_suppliers:
        chosen = _latest_late_line(
            late.supplier,
            order_items,
            supplier_of,
            received_on,
        )
        for _delay, item in chosen:
            alternative = _alternative_for(
                item,
                late.supplier,
                by_supplier,
                supplier_of,
                order_of,
                order_items,
            )
            if alternative is None:
                continue
            alternative_supplier, alternative_rate = alternative
            built = _build(
                item,
                order_of.get(item.get("parent") or ""),
                late,
                by_supplier[alternative_supplier],
                alternative_rate,
                today,
            )
            if built is not None:
                return built
    return None


def _receipt_dates(
    receipts: list[dict[str, Any]],
    receipt_items: list[dict[str, Any]],
) -> dict[str, date]:
    posted_on = {row["name"]: _as_date(row.get("posting_date")) for row in receipts}
    received: dict[str, date] = {}
    for item in receipt_items:
        po_item = item.get("purchase_order_item")
        posted = posted_on.get(item.get("parent") or "")
        if not po_item or posted is None:
            continue
        previous = received.get(po_item)
        if previous is None or posted > previous:
            received[po_item] = posted
    return received


def _latest_late_line(
    supplier: str,
    order_items: list[dict[str, Any]],
    supplier_of: dict[str, str],
    received_on: dict[str, date],
) -> list[tuple[int, dict[str, Any]]]:
    late_lines: list[tuple[int, dict[str, Any]]] = []
    for item in order_items:
        if supplier_of.get(item.get("parent") or "") != supplier:
            continue
        needed = _need_by(item)
        received = received_on.get(item.get("name") or "")
        if needed is None or received is None:
            continue
        delay = (received - needed).days
        if delay > 0 and float(item.get("qty") or 0) > 0 and item.get("item_code"):
            late_lines.append((delay, item))
    late_lines.sort(key=lambda row: (-row[0], str(row[1].get("name"))))
    return late_lines


def _alternative_for(
    item: dict[str, Any],
    late_supplier: str,
    by_supplier: dict[str, SupplierPerformance],
    supplier_of: dict[str, str],
    order_of: dict[str, dict[str, Any]],
    order_items: list[dict[str, Any]],
) -> tuple[str, float] | None:
    late = by_supplier[late_supplier]
    item_code = item.get("item_code")
    candidates: list[tuple[float, int, str, float]] = []
    for supplier, performance in by_supplier.items():
        if supplier == late_supplier or performance.lines < MIN_SCORED_LINES:
            continue
        if performance.on_time_rate <= late.on_time_rate:
            continue
        rate = _latest_rate(supplier, item_code, order_items, supplier_of, order_of)
        if rate is None:
            continue
        candidates.append((performance.on_time_rate, performance.lines, supplier, rate))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row[0], -row[1], row[2]))
    best = candidates[0]
    return best[2], best[3]


def _latest_rate(
    supplier: str,
    item_code: object,
    order_items: list[dict[str, Any]],
    supplier_of: dict[str, str],
    order_of: dict[str, dict[str, Any]],
) -> float | None:
    best: tuple[date, str, float] | None = None
    for item in order_items:
        if item.get("item_code") != item_code:
            continue
        parent = str(item.get("parent") or "")
        if supplier_of.get(parent) != supplier:
            continue
        when = _as_date(order_of.get(parent, {}).get("transaction_date"))
        rate = float(item.get("rate") or 0)
        if when is None or rate <= 0:
            continue
        if best is None or (when, parent) > (best[0], best[1]):
            best = (when, parent, rate)
    return None if best is None else best[2]


def _build(
    item: dict[str, Any],
    order: dict[str, Any] | None,
    late: SupplierPerformance,
    alternative: SupplierPerformance,
    rate: float,
    today: date,
) -> ReplacementCase | None:
    if order is None:
        return None
    company = order.get("company") or ""
    warehouse = item.get("warehouse") or ""
    item_code = item.get("item_code") or ""
    source_line = item.get("name") or ""
    if not company or not warehouse or not item_code or not source_line:
        return None
    draft = DraftPurchaseOrder(
        supplier=alternative.supplier,
        company=str(company),
        warehouse=str(warehouse),
        transaction_date=today,
        schedule_date=today + timedelta(days=_LEAD_TIME_DAYS),
        items=[
            PurchaseOrderLine(
                item_code=str(item_code),
                qty=float(item["qty"]),
                rate=rate,
            )
        ],
    )
    return ReplacementCase(
        case_id=f"replacement:{source_line}",
        late_supplier=late.supplier,
        late_lines=late.late_lines,
        scored_lines=late.lines,
        on_time_rate=late.on_time_rate,
        avg_late_days=late.avg_late_days,
        alternative_supplier=alternative.supplier,
        alternative_on_time_rate=alternative.on_time_rate,
        source_order=str(order.get("name") or ""),
        source_line=str(source_line),
        order=draft,
    )
