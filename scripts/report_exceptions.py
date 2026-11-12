#!/usr/bin/env python3
"""Print the purchasing exceptions currently stored in ERPNext."""

from __future__ import annotations

from collections import Counter

from procurement_agent.config import get_settings
from procurement_agent.domain.exceptions import (
    invoice_mismatches,
    open_orders,
    supplier_performance,
)
from procurement_agent.erp.client import ErpNextReadClient


def main() -> None:
    settings = get_settings()
    with ErpNextReadClient(settings) as erp:
        orders = erp.list_all(
            "Purchase Order",
            [
                "name",
                "supplier",
                "transaction_date",
                "status",
                "docstatus",
                "per_received",
                "per_billed",
            ],
            filters=[["docstatus", "=", 1]],
        )
        order_items = erp.list_all(
            "Purchase Order Item",
            [
                "name",
                "parent",
                "item_code",
                "qty",
                "rate",
                "received_qty",
                "schedule_date",
                "description",
            ],
            parent="Purchase Order",
        )
        receipts = erp.list_all(
            "Purchase Receipt",
            ["name", "posting_date", "supplier", "docstatus"],
            filters=[["docstatus", "=", 1]],
        )
        receipt_items = erp.list_all(
            "Purchase Receipt Item",
            ["name", "parent", "purchase_order", "purchase_order_item", "qty"],
            parent="Purchase Receipt",
        )
        invoices = erp.list_all(
            "Purchase Invoice",
            ["name", "supplier", "bill_no", "posting_date", "docstatus"],
            filters=[["docstatus", "=", 1]],
        )
        invoice_items = erp.list_all(
            "Purchase Invoice Item",
            ["name", "parent", "item_code", "qty", "rate", "purchase_order", "po_detail"],
            parent="Purchase Invoice",
        )

    opened = open_orders(orders)
    by_status = Counter(row.status for row in opened)
    print(f"Open purchase orders: {len(opened)}")
    for status in ("To Receive and Bill", "To Bill", "To Receive"):
        print(f"  {status}: {by_status[status]}")

    performance = supplier_performance(order_items, orders, receipt_items, receipts)
    late_lines = sum(row.late_lines for row in performance)
    scored_lines = sum(row.lines for row in performance)
    print(f"Late receipt lines: {late_lines} of {scored_lines}")
    print("Weakest suppliers with at least 5 lines:")
    shown = 0
    for row in performance:
        if row.lines < 5:
            continue
        print(
            f"  {row.supplier}: on time {row.on_time_rate:.0%}, "
            f"late {row.late_lines}/{row.lines}, avg late {row.avg_late_days:.1f} days"
        )
        shown += 1
        if shown == 8:
            break

    mismatches = invoice_mismatches(invoice_items, invoices, order_items)
    reasons = Counter(reason for row in mismatches for reason in row.reasons)
    print(f"Invoice exceptions: {len(mismatches)} lines")
    print(f"  price: {reasons['price']}")
    print(f"  quantity: {reasons['quantity']}")
    print(f"  no purchase order: {reasons['no_purchase_order']}")


if __name__ == "__main__":
    main()
