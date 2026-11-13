"""Facts about who has sold an item.

The supplier and the price have to come from these facts. This module does not choose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from procurement_agent.approvals.orders import DraftPurchaseOrder, PurchaseOrderLine
from procurement_agent.approvals.service import StoredProposal
from procurement_agent.domain.exceptions import _as_date, supplier_performance

_LEAD_TIME_DAYS = 14


@dataclass(frozen=True)
class SellerFact:
    supplier: str
    company: str
    warehouse: str
    qty: float
    rate: float
    when: date
    on_time_rate: float | None
    scored_lines: int
    currency: str = "EUR"
    uom: str | None = None


@dataclass(frozen=True)
class FiledPurchase:
    stored: StoredProposal
    posted_anew: bool
    notice: str | None = None


def seller_facts(
    item_code: str,
    orders: list[dict[str, Any]],
    order_items: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    receipt_items: list[dict[str, Any]],
) -> list[SellerFact]:
    """One row per supplier who has a real purchase line for this item."""
    supplier_of = {row["name"]: str(row.get("supplier") or "") for row in orders}
    order_of = {row["name"]: row for row in orders}
    performance = {
        row.supplier: row
        for row in supplier_performance(order_items, orders, receipt_items, receipts)
    }
    facts: list[SellerFact] = []
    for seller in _sellers(item_code, order_items, supplier_of, order_of):
        record = performance.get(seller.supplier)
        facts.append(
            SellerFact(
                supplier=seller.supplier,
                company=seller.company,
                warehouse=seller.warehouse,
                qty=seller.qty,
                rate=seller.rate,
                when=seller.when,
                on_time_rate=None if record is None else record.on_time_rate,
                scored_lines=0 if record is None else record.lines,
                currency=str(order_of[seller.order_name].get("currency") or "EUR"),
                uom=next((str(item["uom"]) for item in order_items
                          if item.get("parent") == seller.order_name
                          and item.get("item_code") == item_code and item.get("uom")), None),
            )
        )
    facts.sort(key=lambda fact: fact.supplier)
    return facts


def order_for(fact: SellerFact, item_code: str, qty: float, today: date) -> DraftPurchaseOrder:
    """Build a draft from one seller row. The rate is that row's latest rate."""
    return DraftPurchaseOrder(
        supplier=fact.supplier,
        company=fact.company,
        warehouse=fact.warehouse,
        currency=fact.currency,
        transaction_date=today,
        schedule_date=today + timedelta(days=_LEAD_TIME_DAYS),
        items=[PurchaseOrderLine(item_code=item_code, qty=qty, rate=fact.rate)],
    )


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
