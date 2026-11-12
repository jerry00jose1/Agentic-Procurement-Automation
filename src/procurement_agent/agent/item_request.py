"""Prepare one purchase for an item a person named.

The supplier, quantity, and price come from purchase orders already in ERPNext.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from procurement_agent.approvals.errors import ApprovalError
from procurement_agent.approvals.orders import DraftPurchaseOrder, PurchaseOrderLine
from procurement_agent.approvals.service import ApprovalVault, StoredProposal
from procurement_agent.domain.exceptions import SupplierPerformance, _as_date, supplier_performance

MIN_SCORED_LINES = 5
_LEAD_TIME_DAYS = 14


@dataclass(frozen=True)
class ItemPurchase:
    item_code: str
    supplier: str
    on_time_rate: float | None
    order: DraftPurchaseOrder


def mentioned_item_code(text: str, known_codes: set[str]) -> str | None:
    """Return the longest known item code that appears in the message."""
    folded = text.casefold()
    found: str | None = None
    for code in known_codes:
        if code and code.casefold() in folded and (found is None or len(code) > len(found)):
            found = code
    return found


def build_item_purchase(
    item_code: str,
    orders: list[dict[str, Any]],
    order_items: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    receipt_items: list[dict[str, Any]],
    *,
    today: date,
) -> ItemPurchase | None:
    """The best on-time supplier who has sold this item, at their latest price."""
    supplier_of = {row["name"]: str(row.get("supplier") or "") for row in orders}
    order_of = {row["name"]: row for row in orders}
    performance = {
        row.supplier: row
        for row in supplier_performance(order_items, orders, receipt_items, receipts)
    }
    sellers = _sellers(item_code, order_items, supplier_of, order_of)
    if not sellers:
        return None
    scored = [
        seller
        for seller in sellers
        if _scored(performance.get(seller.supplier)) is not None
    ]
    pool = scored or sellers
    chosen = max(pool, key=lambda seller: _rank(seller, performance))
    record = performance.get(chosen.supplier)
    return ItemPurchase(
        item_code=item_code,
        supplier=chosen.supplier,
        on_time_rate=record.on_time_rate if record is not None else None,
        order=DraftPurchaseOrder(
            supplier=chosen.supplier,
            company=chosen.company,
            warehouse=chosen.warehouse,
            transaction_date=today,
            schedule_date=today + timedelta(days=_LEAD_TIME_DAYS),
            items=[
                PurchaseOrderLine(
                    item_code=item_code,
                    qty=chosen.qty,
                    rate=chosen.rate,
                )
            ],
        ),
    )


def file_item_purchase(vault: ApprovalVault, purchase: ItemPurchase) -> StoredProposal:
    """Save the purchase, or return the open proposal for this item."""
    base = f"item:{purchase.item_code}"
    for attempt in range(1, 21):
        key = base if attempt == 1 else f"{base}:{attempt}"
        existing = vault.get_by_key(key)
        if existing is None:
            return vault.stage(key, purchase.order)
        if existing.status != "rejected":
            return existing
    raise ApprovalError("invalid_state", "This item has been rejected too many times.")


@dataclass(frozen=True)
class _Seller:
    supplier: str
    company: str
    warehouse: str
    qty: float
    rate: float
    when: date
    order_name: str


def _sellers(
    item_code: str,
    order_items: list[dict[str, Any]],
    supplier_of: dict[str, str],
    order_of: dict[str, dict[str, Any]],
) -> list[_Seller]:
    latest: dict[str, _Seller] = {}
    for item in order_items:
        if item.get("item_code") != item_code:
            continue
        parent = str(item.get("parent") or "")
        supplier = supplier_of.get(parent) or ""
        order = order_of.get(parent) or {}
        company = str(order.get("company") or "")
        warehouse = str(item.get("warehouse") or "")
        when = _as_date(order.get("transaction_date"))
        qty = float(item.get("qty") or 0)
        rate = float(item.get("rate") or 0)
        if not supplier or not company or not warehouse or when is None or qty <= 0 or rate <= 0:
            continue
        seller = _Seller(supplier, company, warehouse, qty, rate, when, parent)
        previous = latest.get(supplier)
        if previous is None or (seller.when, seller.order_name) > (
            previous.when,
            previous.order_name,
        ):
            latest[supplier] = seller
    return list(latest.values())


def _scored(record: SupplierPerformance | None) -> SupplierPerformance | None:
    if record is None or record.lines < MIN_SCORED_LINES:
        return None
    return record


def _rank(
    seller: _Seller,
    performance: dict[str, SupplierPerformance],
) -> tuple[int, float, int, int, str]:
    record = _scored(performance.get(seller.supplier))
    if record is None:
        return (0, 0.0, 0, seller.when.toordinal(), seller.supplier)
    return (1, record.on_time_rate, record.lines, seller.when.toordinal(), seller.supplier)
