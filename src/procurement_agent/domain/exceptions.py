"""Classify the purchasing exceptions already stored in ERPNext.

Dates and amounts come from the documents. Nothing here calls an LLM.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

OPEN_STATUSES = ("To Receive and Bill", "To Bill", "To Receive")
_NEED_BY = re.compile(r"need_by:(\d{4}-\d{2}-\d{2})")
_MONEY_TOLERANCE = 0.01
_QTY_TOLERANCE = 0.001


@dataclass(frozen=True)
class OpenOrder:
    name: str
    supplier: str
    transaction_date: str
    status: str
    per_received: float
    per_billed: float


@dataclass(frozen=True)
class SupplierPerformance:
    supplier: str
    lines: int
    late_lines: int
    on_time_rate: float
    avg_late_days: float


@dataclass(frozen=True)
class InvoiceMismatch:
    invoice: str
    supplier: str
    item_code: str
    purchase_order: str
    invoice_qty: float
    received_qty: float
    invoice_rate: float
    order_rate: float
    reasons: tuple[str, ...]


def open_orders(orders: list[dict[str, Any]]) -> list[OpenOrder]:
    selected = [
        row
        for row in orders
        if row.get("status") in OPEN_STATUSES and row.get("docstatus") == 1
    ]
    selected.sort(key=lambda row: (row.get("status") or "", row.get("name") or ""))
    return [
        OpenOrder(
            name=row["name"],
            supplier=row.get("supplier") or "",
            transaction_date=_as_text(row.get("transaction_date")),
            status=row["status"],
            per_received=float(row.get("per_received") or 0),
            per_billed=float(row.get("per_billed") or 0),
        )
        for row in selected
    ]


def supplier_performance(
    order_items: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    receipt_items: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
) -> list[SupplierPerformance]:
    supplier_of = {row["name"]: row.get("supplier") or "" for row in orders}
    posted_on = {row["name"]: _as_date(row.get("posting_date")) for row in receipts}
    receipt_date_of: dict[str, date] = {}
    for item in receipt_items:
        po_item = item.get("purchase_order_item")
        posted = posted_on.get(item.get("parent") or "")
        if not po_item or posted is None:
            continue
        previous = receipt_date_of.get(po_item)
        if previous is None or posted > previous:
            receipt_date_of[po_item] = posted

    buckets: dict[str, list[int]] = defaultdict(list)
    for item in order_items:
        supplier = supplier_of.get(item.get("parent") or "")
        needed = _need_by(item)
        received = receipt_date_of.get(item.get("name") or "")
        if not supplier or needed is None or received is None:
            continue
        buckets[supplier].append((received - needed).days)

    results: list[SupplierPerformance] = []
    for supplier, delays in buckets.items():
        late = [days for days in delays if days > 0]
        results.append(
            SupplierPerformance(
                supplier=supplier,
                lines=len(delays),
                late_lines=len(late),
                on_time_rate=(len(delays) - len(late)) / len(delays),
                avg_late_days=(sum(late) / len(late)) if late else 0.0,
            )
        )
    results.sort(key=lambda row: (row.on_time_rate, -row.lines, row.supplier))
    return results


def invoice_mismatches(
    invoice_items: list[dict[str, Any]],
    invoices: list[dict[str, Any]],
    order_items: list[dict[str, Any]],
) -> list[InvoiceMismatch]:
    invoice_of = {row["name"]: row for row in invoices}
    order_item_of = {row["name"]: row for row in order_items}
    mismatches: list[InvoiceMismatch] = []
    for item in invoice_items:
        header = invoice_of.get(item.get("parent") or "", {})
        order_item = order_item_of.get(item.get("po_detail") or "")
        reasons: list[str] = []
        received_qty = 0.0
        order_rate = 0.0
        purchase_order = item.get("purchase_order") or ""
        if order_item is None:
            reasons.append("no_purchase_order")
        else:
            received_qty = float(order_item.get("received_qty") or 0)
            order_rate = float(order_item.get("rate") or 0)
            invoice_qty = float(item.get("qty") or 0)
            invoice_rate = float(item.get("rate") or 0)
            if abs(invoice_qty - received_qty) > _QTY_TOLERANCE:
                reasons.append("quantity")
            if abs(invoice_rate - order_rate) > _MONEY_TOLERANCE:
                reasons.append("price")
        if not reasons:
            continue
        mismatches.append(
            InvoiceMismatch(
                invoice=item.get("parent") or "",
                supplier=header.get("supplier") or "",
                item_code=item.get("item_code") or "",
                purchase_order=purchase_order,
                invoice_qty=float(item.get("qty") or 0),
                received_qty=received_qty,
                invoice_rate=float(item.get("rate") or 0),
                order_rate=order_rate,
                reasons=tuple(reasons),
            )
        )
    mismatches.sort(key=lambda row: (row.invoice, row.item_code))
    return mismatches


def _need_by(item: dict[str, Any]) -> date | None:
    description = item.get("description") or ""
    match = _NEED_BY.search(description)
    if match:
        return _as_date(match.group(1))
    return _as_date(item.get("schedule_date"))


def _as_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _as_text(value: object) -> str:
    return "" if value is None else str(value)[:10]
