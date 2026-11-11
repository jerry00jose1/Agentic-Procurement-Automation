# ERPNext Discovery Notes

> Filled by `make probe` after the official Docker stack is healthy.
> This file is the only allowed source of ERP schema/API claims in later stages.
> Mark anything not verified here as **UNVERIFIED**.

## Environment

| Field | Value |
|---|---|
| Compose file | `docker/erpnext/docker-compose.yml` (official `frappe_docker` `pwd.yml`) |
| Image | `frappe/erpnext:v16.54.0` |
| URL | `http://localhost:8080` |
| Site name | `frontend` |
| Default login | `Administrator` / `admin` |
| Discovery date | 2026-11-11 |
| API auth method | Session cookie via `POST /api/method/login` (token key:secret still pending) |

## Verified connectivity (2026-11-11)

| Check | Result |
|---|---|
| `GET /api/method/frappe.ping` | `200` → `{"message":"pong"}` |
| Login as Administrator | Session `sid` cookie returned |
| Host / site headers | `Host: frontend` and `X-Frappe-Site-Name: frontend` required for API calls |

## DocTypes Probe Status

| DocType | List works? | Rows today | Required create fields | Notes |
|---|---|---|---|---|
| Company | Yes (`200`) | **1** (`Northwind Components`, `NC`, Germany, EUR) | Created in the setup wizard | Default warehouse `Stores - NC`. Payable `3300 - Verb. aus Lieferungen und Leistungen - NC` |
| Supplier | Yes (`200`) | 64 | `supplier_name`, `supplier_type` | Loaded from `DimSupplier.csv` |
| Item | Yes (`200`) | 51 | `item_code`, `item_group`, `stock_uom` | Loaded from `DimItem.csv` |
| Warehouse | Yes (`200`) | 6 | Created with the company | `Stores - NC` is the receipt warehouse |
| Purchase Order | Yes (`200`) | 910 submitted | `supplier`, `transaction_date`, `company`, `items` | 651 completed, 243 to bill, 4 to receive and bill |
| Purchase Receipt | Yes (`200`) | 910 submitted | `supplier`, `posting_date`, `items` | One receipt per source purchase order |
| Purchase Invoice | Yes (`200`) | 905 submitted | `supplier`, `posting_date`, `items`, `credit_to` | `bill_no` is the source invoice number |

## Verified API Quirks

1. Fresh `pwd.yml` site installs Frappe + ERPNext successfully, but **does not create a Company** until setup wizard / explicit Company insert.
2. Resource list endpoint shape confirmed: `GET /api/resource/{DocType}?limit_page_length=5` → `{"data":[...]}`.
3. Token API key/secret generation is **UNVERIFIED** (next discovery task before long-running scripts).

## Seed

Loaded on 2026-11-11 from `data/mizan/` via `make seed`. See `data/mizan/PROVENANCE.txt` for what was changed to satisfy ERPNext date and receipt rules. Source amounts are USD and are stored as EUR figures.
