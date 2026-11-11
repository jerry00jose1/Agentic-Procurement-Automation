"""Load the findi360 Mizan sample into the live ERPNext site.

Run inside the ERPNext container, where `frappe` is already connected:

    bench --site frontend execute seed_erpnext.run

The CSV files are read from /tmp/mizan-data (copied there by `make seed`).
"""

from __future__ import annotations

import csv
import traceback
from collections import defaultdict
from pathlib import Path

import frappe

COMPANY = "Northwind Components"
WAREHOUSE = "Stores - NC"
REJECTED_WAREHOUSE = "Rejected - NC"
PAYABLE = "3300 - Verb. aus Lieferungen und Leistungen - NC"
DATA_DIR = Path("/tmp/mizan-data")

UOM_MAP = {
    "kg": "Kg",
    "pcs": "Pcs",
    "lump sum": "Lump Sum",
    "1000 pcs": "Thousand Pcs",
}


def delete_bills(bill_nos: str) -> None:
    """Cancel and delete purchase invoices by supplier bill number. Used to relink bad rows."""
    frappe.flags.ignore_permissions = True
    for bill_no in [part.strip() for part in bill_nos.split(",") if part.strip()]:
        name = frappe.db.get_value("Purchase Invoice", {"bill_no": bill_no}, "name")
        if not name:
            print(f"missing {bill_no}", flush=True)
            continue
        doc = frappe.get_doc("Purchase Invoice", name)
        if doc.docstatus == 1:
            doc.cancel()
        frappe.delete_doc("Purchase Invoice", name, force=1, ignore_permissions=True)
        print(f"deleted {bill_no} {name}", flush=True)
    frappe.db.commit()


def run(limit: int = 0) -> None:
    """Create masters, then purchase orders, receipts, and invoices.

    `limit` caps how many purchase orders are loaded. Zero means all of them.
    """
    limit = int(limit or 0)
    frappe.flags.ignore_permissions = True
    frappe.flags.mute_emails = True

    _ensure_fiscal_years()
    _relax_match_tolerances()
    expense_account = _expense_account()
    _ensure_warehouse(REJECTED_WAREHOUSE)

    suppliers = _read("DimSupplier.csv")
    items = _read("DimItem.csv")
    po_lines = _read("FactPOLine.csv")
    invoice_lines = _read("FactInvoiceLine.csv")

    supplier_name = _load_suppliers(suppliers)
    item_uom = _load_items(items, expense_account)
    print(f"masters suppliers={len(supplier_name)} items={len(item_uom)}", flush=True)

    orders = _group_orders(po_lines)
    order_numbers = sorted(orders)
    if limit:
        order_numbers = order_numbers[:limit]
    selected = set(order_numbers)

    line_to_po_item: dict[str, tuple[str, str]] = {}
    created_pos = 0
    for index, po_number in enumerate(order_numbers, start=1):
        lines = orders[po_number]
        try:
            mapping = _ensure_purchase_order(po_number, lines, supplier_name, item_uom)
            frappe.db.commit()
            line_to_po_item.update(mapping)
        except Exception as exc:  # noqa: BLE001 — keep seeding after one bad order
            _record_error("purchase_order", po_number, exc)
            continue
        try:
            _ensure_purchase_receipt(po_number, lines, supplier_name, mapping)
            frappe.db.commit()
            created_pos += 1
        except Exception as exc:  # noqa: BLE001
            _record_error("purchase_receipt", po_number, exc)
        if index % 25 == 0:
            print(f"orders {index}/{len(order_numbers)}", flush=True)

    created_invoices = 0
    for key, lines in _group_invoices(invoice_lines).items():
        supplier_code, invoice_no = key
        linked_ids = [_norm_id(line["POLineID"]) for line in lines if _norm_id(line["POLineID"])]
        if limit and (not linked_ids or any(line_id not in line_to_po_item for line_id in linked_ids)):
            continue
        try:
            _ensure_purchase_invoice(
                invoice_no,
                supplier_name[supplier_code],
                lines,
                line_to_po_item,
            )
            frappe.db.commit()
            created_invoices += 1
        except Exception as exc:  # noqa: BLE001
            _record_error("purchase_invoice", invoice_no, exc)
        if created_invoices and created_invoices % 25 == 0:
            print(f"invoices {created_invoices}", flush=True)

    frappe.db.commit()
    print(
        "done",
        f"purchase_orders_touched={created_pos}",
        f"invoices_touched={created_invoices}",
        f"suppliers={frappe.db.count('Supplier')}",
        f"items={frappe.db.count('Item')}",
        f"pos={frappe.db.count('Purchase Order')}",
        f"receipts={frappe.db.count('Purchase Receipt')}",
        f"invoices={frappe.db.count('Purchase Invoice')}",
        flush=True,
    )


def _read(name: str) -> list[dict[str, str]]:
    with (DATA_DIR / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def _norm_id(value: str) -> str:
    value = (value or "").strip()
    if value.endswith(".0"):
        return value[:-2]
    return value


def _num(value: str) -> float:
    return float(value) if value else 0.0


def _group_orders(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["PONumber"]].append(row)
    for lines in grouped.values():
        lines.sort(key=lambda row: int(row["POLineNo"]))
    return grouped


def _group_invoices(rows: list[dict[str, str]]) -> dict[tuple[str, str], list[dict[str, str]]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["SupplierCode"], row["InvoiceNo"])].append(row)
    return grouped


def _ensure_fiscal_years() -> None:
    for year in (2023, 2024, 2025):
        if frappe.db.exists("Fiscal Year", str(year)):
            continue
        frappe.get_doc(
            {
                "doctype": "Fiscal Year",
                "year": str(year),
                "year_start_date": f"{year}-01-01",
                "year_end_date": f"{year}-12-31",
            }
        ).insert(ignore_permissions=True)
    frappe.db.commit()


def _relax_match_tolerances() -> None:
    """Let the source variances exist as documents instead of being rejected.

    Rejected quantity sits on top of received quantity, up to about 4.1%.
    Invoice prices and quantities also differ from the order on purpose.
    """
    _set_single("Buying Settings", {"over_receipt_allowance": 10, "maintain_same_rate": 0})
    _set_single(
        "Stock Settings",
        {"over_delivery_receipt_allowance": 10, "allow_negative_stock": 0},
    )
    _set_single(
        "Accounts Settings",
        {"over_billing_allowance": 100, "maintain_same_rate": 0},
    )
    frappe.db.commit()


def _set_single(doctype: str, values: dict[str, object]) -> None:
    doc = frappe.get_single(doctype)
    changed = False
    for fieldname, value in values.items():
        if not doc.meta.has_field(fieldname):
            print(f"skip missing {doctype}.{fieldname}", flush=True)
            continue
        doc.set(fieldname, value)
        changed = True
    if changed:
        doc.save(ignore_permissions=True)


def _expense_account() -> str:
    account = frappe.db.get_value("Company", COMPANY, "default_expense_account")
    if not account:
        raise RuntimeError(f"{COMPANY} has no default expense account")
    return account


def _ensure_warehouse(name: str) -> None:
    if frappe.db.exists("Warehouse", name):
        return
    warehouse_name = name.replace(f" - {frappe.db.get_value('Company', COMPANY, 'abbr')}", "")
    frappe.get_doc(
        {
            "doctype": "Warehouse",
            "warehouse_name": warehouse_name,
            "company": COMPANY,
            "parent_warehouse": f"All Warehouses - {frappe.db.get_value('Company', COMPANY, 'abbr')}",
        }
    ).insert(ignore_permissions=True)


def _load_suppliers(rows: list[dict[str, str]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in rows:
        name = row["SupplierName"].strip()
        if not frappe.db.exists("Supplier", name):
            country = row["Country"].strip()
            if country and not frappe.db.exists("Country", country):
                country = ""
            frappe.get_doc(
                {
                    "doctype": "Supplier",
                    "supplier_name": name,
                    "supplier_group": "All Supplier Groups",
                    "supplier_type": "Company",
                    "country": country,
                    "supplier_details": (
                        f"{row['SupplierCode']} | {row['Category']} | {row['PaymentTerms']}"
                    ),
                }
            ).insert(ignore_permissions=True)
        mapping[row["SupplierCode"]] = name
    frappe.db.commit()
    return mapping


def _load_items(rows: list[dict[str, str]], expense_account: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in rows:
        uom = _ensure_uom(row["UnitOfMeasure"])
        group = _ensure_item_group(row["Category"])
        code = row["ItemCode"].strip()
        if not frappe.db.exists("Item", code):
            frappe.get_doc(
                {
                    "doctype": "Item",
                    "item_code": code,
                    "item_name": row["ItemName"].strip(),
                    "item_group": group,
                    "stock_uom": uom,
                    "is_stock_item": 1,
                    "include_item_in_manufacturing": 0,
                    "description": f"{row['SpendType']} | {row['AccountName']}",
                    "over_delivery_receipt_allowance": 10,
                    "item_defaults": [
                        {
                            "company": COMPANY,
                            "default_warehouse": WAREHOUSE,
                            "expense_account": expense_account,
                        }
                    ],
                }
            ).insert(ignore_permissions=True)
        mapping[code] = uom
    frappe.db.commit()
    return mapping


def _ensure_uom(source: str) -> str:
    name = UOM_MAP[source.strip().lower()]
    if not frappe.db.exists("UOM", name):
        frappe.get_doc({"doctype": "UOM", "uom_name": name}).insert(ignore_permissions=True)
    return name


def _ensure_item_group(category: str) -> str:
    name = category.strip()
    if not frappe.db.exists("Item Group", name):
        frappe.get_doc(
            {
                "doctype": "Item Group",
                "item_group_name": name,
                "parent_item_group": "All Item Groups",
            }
        ).insert(ignore_permissions=True)
    return name


def _ensure_purchase_order(
    po_number: str,
    lines: list[dict[str, str]],
    supplier_name: dict[str, str],
    item_uom: dict[str, str],
) -> dict[str, tuple[str, str]]:
    remark = f"source_po:{po_number}"
    existing = frappe.db.get_value("Purchase Order", {"remarks": remark}, "name")
    if existing:
        return _line_map(existing)

    supplier = supplier_name[lines[0]["SupplierCode"]]
    items = []
    for line in lines:
        rejected = _num(line["RejectedQty"])
        ordered = _num(line["OrderedQty"])
        items.append(
            {
                "item_code": line["ItemCode"],
                "qty": ordered + rejected,
                "rate": round(_num(line["POUnitPrice"]), 2),
                "schedule_date": _required_by(line),
                "warehouse": WAREHOUSE,
                "uom": item_uom[line["ItemCode"]],
                "description": (
                    f"{line['ItemCode']} source_line:{line['POLineID']} "
                    f"ordered:{_format_qty(ordered)} rejected:{_format_qty(rejected)} "
                    f"need_by:{line['NeedByDate']}"
                ),
            }
        )
    doc = frappe.get_doc(
        {
            "doctype": "Purchase Order",
            "supplier": supplier,
            "company": COMPANY,
            "transaction_date": lines[0]["PODate"],
            "schedule_date": max(_required_by(line) for line in lines),
            "currency": "EUR",
            "conversion_rate": 1,
            "buying_price_list": _buying_price_list(),
            "remarks": remark,
            "items": items,
        }
    )
    doc.insert(ignore_permissions=True)
    doc.submit()
    return _line_map(doc.name)


def _line_map(po_name: str) -> dict[str, tuple[str, str]]:
    mapping: dict[str, tuple[str, str]] = {}
    for row in frappe.get_all(
        "Purchase Order Item",
        filters={"parent": po_name},
        fields=["name", "description"],
    ):
        marker = "source_line:"
        text = row.description or ""
        if marker not in text:
            continue
        line_id = text.split(marker, 1)[1].split()[0]
        mapping[line_id] = (po_name, row.name)
    return mapping


def _ensure_purchase_receipt(
    po_number: str,
    lines: list[dict[str, str]],
    supplier_name: dict[str, str],
    line_map: dict[str, tuple[str, str]],
) -> None:
    remark = f"source_receipt:{po_number}"
    if frappe.db.exists("Purchase Receipt", {"remarks": remark}):
        return
    posting_date = max(line["LastReceiptDate"] or line["NeedByDate"] for line in lines)
    po_date = lines[0]["PODate"]
    if posting_date < po_date:
        posting_date = po_date
    items = []
    for line in lines:
        po_name, po_item = line_map[line["POLineID"]]
        items.append(
            {
                "item_code": line["ItemCode"],
                "qty": _num(line["ReceivedQty"]),
                "rejected_qty": _num(line["RejectedQty"]),
                "rate": round(_num(line["POUnitPrice"]), 2),
                "warehouse": WAREHOUSE,
                "rejected_warehouse": REJECTED_WAREHOUSE if _num(line["RejectedQty"]) else None,
                "purchase_order": po_name,
                "purchase_order_item": po_item,
                "cost_center": frappe.db.get_value("Company", COMPANY, "cost_center"),
            }
        )
    doc = frappe.get_doc(
        {
            "doctype": "Purchase Receipt",
            "supplier": supplier_name[lines[0]["SupplierCode"]],
            "company": COMPANY,
            "posting_date": posting_date,
            "posting_time": "12:00:00",
            "set_posting_time": 1,
            "currency": "EUR",
            "conversion_rate": 1,
            "set_warehouse": WAREHOUSE,
            "remarks": remark,
            "items": items,
        }
    )
    doc.insert(ignore_permissions=True)
    doc.submit()


def _ensure_purchase_invoice(
    invoice_no: str,
    supplier: str,
    lines: list[dict[str, str]],
    line_map: dict[str, tuple[str, str]],
) -> None:
    if frappe.db.exists("Purchase Invoice", {"bill_no": invoice_no, "supplier": supplier}):
        return
    posting_date = lines[0]["PostingDate"] or lines[0]["InvoiceDate"]
    linked_po_dates = []
    for line in lines:
        linked = line_map.get(_norm_id(line["POLineID"]))
        if not linked:
            continue
        po_date = frappe.db.get_value("Purchase Order", linked[0], "transaction_date")
        if po_date:
            linked_po_dates.append(str(po_date))
    if linked_po_dates and posting_date < max(linked_po_dates):
        posting_date = max(linked_po_dates)
    due_date = lines[0]["DueDate"] or posting_date
    if due_date < posting_date:
        due_date = posting_date
    items = []
    for line in lines:
        line_id = _norm_id(line["POLineID"])
        linked = line_map.get(line_id) if line_id else None
        item = {
            "item_code": line["ItemCode"],
            "qty": _num(line["InvoiceQty"]),
            "rate": round(_num(line["InvoiceUnitPrice"]), 2),
            "cost_center": frappe.db.get_value("Company", COMPANY, "cost_center"),
            "description": f"source_invoice_line:{line['InvoiceLineID']}",
        }
        if linked:
            item["purchase_order"] = linked[0]
            item["po_detail"] = linked[1]
        items.append(item)
    doc = frappe.get_doc(
        {
            "doctype": "Purchase Invoice",
            "supplier": supplier,
            "company": COMPANY,
            "posting_date": posting_date,
            "posting_time": "12:00:00",
            "set_posting_time": 1,
            "bill_no": invoice_no,
            "bill_date": lines[0]["InvoiceDate"],
            "due_date": due_date,
            "currency": "EUR",
            "conversion_rate": 1,
            "credit_to": PAYABLE,
            "update_stock": 0,
            "remarks": f"source_invoice:{invoice_no}",
            "items": items,
        }
    )
    doc.insert(ignore_permissions=True)
    doc.submit()


def _required_by(line: dict[str, str]) -> str:
    """ERPNext rejects a required-by date earlier than the order date."""
    need_by = line["NeedByDate"]
    return need_by if need_by >= line["PODate"] else line["PODate"]


def _buying_price_list() -> str | None:
    if frappe.db.exists("Price List", "Standard Buying"):
        return "Standard Buying"
    return None


def _format_qty(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def _record_error(kind: str, key: str, exc: Exception) -> None:
    frappe.db.rollback()
    message = f"{kind} {key}: {exc}"
    print(message, flush=True)
    trace = traceback.format_exc(limit=4)
    path = Path("/tmp/mizan-seed-errors.log")
    with path.open("a") as handle:
        handle.write(message + "\n" + trace + "\n")
