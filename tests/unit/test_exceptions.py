from procurement_agent.domain.exceptions import (
    invoice_mismatches,
    open_orders,
    supplier_performance,
)


def test_open_orders_keep_only_outstanding_submitted_documents() -> None:
    rows = open_orders(
        [
            {
                "name": "PO-1",
                "supplier": "A",
                "transaction_date": "2024-01-01",
                "status": "To Bill",
                "docstatus": 1,
                "per_received": 100,
                "per_billed": 0,
            },
            {
                "name": "PO-2",
                "supplier": "A",
                "transaction_date": "2024-01-02",
                "status": "Completed",
                "docstatus": 1,
                "per_received": 100,
                "per_billed": 100,
            },
            {
                "name": "PO-3",
                "supplier": "A",
                "transaction_date": "2024-01-03",
                "status": "To Bill",
                "docstatus": 0,
                "per_received": 0,
                "per_billed": 0,
            },
        ]
    )
    assert [row.name for row in rows] == ["PO-1"]


def test_supplier_performance_uses_source_need_by_date() -> None:
    rows = supplier_performance(
        order_items=[
            {
                "name": "line-1",
                "parent": "PO-1",
                "description": "item source_line:1 ordered:10 rejected:0 need_by:2024-01-01",
                "schedule_date": "2024-01-05",
            }
        ],
        orders=[{"name": "PO-1", "supplier": "Slow Co"}],
        receipt_items=[{"parent": "PR-1", "purchase_order_item": "line-1"}],
        receipts=[{"name": "PR-1", "posting_date": "2024-01-11"}],
    )
    assert rows[0].late_lines == 1
    assert rows[0].avg_late_days == 10


def test_invoice_mismatch_flags_price_quantity_and_missing_order() -> None:
    rows = invoice_mismatches(
        invoice_items=[
            {
                "parent": "PINV-1",
                "item_code": "A",
                "qty": 8,
                "rate": 12,
                "purchase_order": "PO-1",
                "po_detail": "line-1",
            },
            {
                "parent": "PINV-2",
                "item_code": "B",
                "qty": 1,
                "rate": 5,
                "purchase_order": "",
                "po_detail": "",
            },
        ],
        invoices=[
            {"name": "PINV-1", "supplier": "A"},
            {"name": "PINV-2", "supplier": "B"},
        ],
        order_items=[{"name": "line-1", "received_qty": 10, "rate": 10}],
    )
    assert [row.reasons for row in rows] == [("quantity", "price"), ("no_purchase_order",)]
