"""Prepare one purchase for an item a person named.

The supplier and price come from purchase orders already in ERPNext.
A quantity is copied from the latest order unless the buyer stated one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from procurement_agent.agent.buyer_request import BuyerRequest
from procurement_agent.approvals.errors import ApprovalError
from procurement_agent.approvals.orders import DraftPurchaseOrder, PurchaseOrderLine
from procurement_agent.approvals.service import ApprovalVault, StoredProposal
from procurement_agent.domain.exceptions import SupplierPerformance, _as_date, supplier_performance

MIN_SCORED_LINES = 5
_LEAD_TIME_DAYS = 14


@dataclass(frozen=True)
class ConsideredSupplier:
    supplier: str
    rate: float
    on_time_rate: float | None
    scored_lines: int
    eligible: bool
    excluded: bool


@dataclass(frozen=True)
class ItemPurchase:
    item_code: str
    supplier: str
    on_time_rate: float | None
    order: DraftPurchaseOrder
    considered: tuple[ConsideredSupplier, ...]
    objective: str
    excluded: tuple[str, ...]
    named_supplier: str | None
    qty_from_buyer: bool


@dataclass(frozen=True)
class FiledPurchase:
    stored: StoredProposal
    posted_anew: bool
    notice: str | None = None


def build_item_purchase(
    orders: list[dict[str, Any]],
    order_items: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    receipt_items: list[dict[str, Any]],
    request: BuyerRequest,
    *,
    today: date,
) -> ItemPurchase | None:
    """Apply the buyer's request to sellers of that item. The rate is always a recorded one."""
    if request.item_code is None:
        return None
    supplier_of = {row["name"]: str(row.get("supplier") or "") for row in orders}
    order_of = {row["name"]: row for row in orders}
    performance = {
        row.supplier: row
        for row in supplier_performance(order_items, orders, receipt_items, receipts)
    }
    sellers = _sellers(request.item_code, order_items, supplier_of, order_of)
    if not sellers:
        return None
    chosen = _choose(sellers, performance, request)
    if chosen is None:
        return None
    record = performance.get(chosen.supplier)
    qty = chosen.qty if request.qty is None else request.qty
    return ItemPurchase(
        item_code=request.item_code,
        supplier=chosen.supplier,
        on_time_rate=record.on_time_rate if record is not None else None,
        considered=_considered(sellers, performance, chosen.supplier, set(request.exclude)),
        objective=request.objective,
        excluded=request.exclude,
        named_supplier=request.named_supplier,
        qty_from_buyer=request.qty is not None,
        order=DraftPurchaseOrder(
            supplier=chosen.supplier,
            company=chosen.company,
            warehouse=chosen.warehouse,
            transaction_date=today,
            schedule_date=today + timedelta(days=_LEAD_TIME_DAYS),
            items=[PurchaseOrderLine(item_code=request.item_code, qty=qty, rate=chosen.rate)],
        ),
    )


def file_item_purchase(vault: ApprovalVault, purchase: ItemPurchase) -> FiledPurchase:
    """Save the purchase. An open card is updated; an unseen card is closed first."""
    base = f"item:{purchase.item_code}"
    notice: str | None = None
    for attempt in range(1, 21):
        key = base if attempt == 1 else f"{base}:{attempt}"
        existing = vault.get_by_key(key)
        if existing is None:
            return FiledPurchase(vault.stage(key, purchase.order), True, notice)
        if existing.status == "rejected":
            continue
        if existing.status != "proposed":
            return FiledPurchase(existing, False, None)
        if existing.payload_hash == purchase.order.fingerprint():
            has_card = bool(existing.slack_channel and existing.slack_ts)
            return FiledPurchase(existing, not has_card, None)
        if existing.slack_channel and existing.slack_ts:
            updated = vault.replace(existing.id, purchase.order, "buyer")
            return FiledPurchase(updated, False, None)
        vault.supersede(existing.id)
        notice = "The previous card for this item no longer applies."
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


def _choose(
    sellers: list[_Seller],
    performance: dict[str, SupplierPerformance],
    request: BuyerRequest,
) -> _Seller | None:
    excluded = set(request.exclude)
    if request.objective == "named_supplier":
        if request.named_supplier is None or request.named_supplier in excluded:
            return None
        return next(
            (seller for seller in sellers if seller.supplier == request.named_supplier),
            None,
        )
    pool = [seller for seller in sellers if seller.supplier not in excluded]
    if not pool:
        return None
    scored = [seller for seller in pool if _scored(performance.get(seller.supplier)) is not None]
    rank_pool = scored or pool
    if request.objective == "lowest_price":
        return min(rank_pool, key=lambda seller: _price_key(seller, performance))
    return max(rank_pool, key=lambda seller: _rank(seller, performance))


def _considered(
    sellers: list[_Seller],
    performance: dict[str, SupplierPerformance],
    chosen: str,
    excluded: set[str],
) -> tuple[ConsideredSupplier, ...]:
    rows: list[ConsideredSupplier] = []
    for seller in sellers:
        record = performance.get(seller.supplier)
        rows.append(
            ConsideredSupplier(
                supplier=seller.supplier,
                rate=seller.rate,
                on_time_rate=None if record is None else record.on_time_rate,
                scored_lines=0 if record is None else record.lines,
                eligible=_scored(record) is not None,
                excluded=seller.supplier in excluded,
            )
        )
    rows.sort(
        key=lambda row: (
            row.supplier != chosen,
            row.excluded,
            not row.eligible,
            -(row.on_time_rate or 0),
            row.supplier,
        )
    )
    return tuple(rows)


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


def _price_key(
    seller: _Seller,
    performance: dict[str, SupplierPerformance],
) -> tuple[float, int, str]:
    record = performance.get(seller.supplier)
    lines = 0 if record is None else record.lines
    return (seller.rate, -lines, seller.supplier)
