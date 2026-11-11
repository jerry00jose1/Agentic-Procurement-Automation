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
| Company | Yes (`200`) | **0** | **UNVERIFIED** | Setup wizard not completed; company must be created before purchasing docs |
| Supplier | Yes (`200`) | 0 | **UNVERIFIED** | Empty until seed |
| Item | Yes (`200`) | 0 | **UNVERIFIED** | Empty until seed |
| Warehouse | Yes (`200`) | 0 | **UNVERIFIED** | Empty until seed |
| Purchase Order | Yes (`200`) | 0 | **UNVERIFIED** | Empty until seed |
| Purchase Receipt | Yes (`200`) | 0 | **UNVERIFIED** | Empty until seed |
| Purchase Invoice | Yes (`200`) | 0 | **UNVERIFIED** | Empty until seed |

## Verified API Quirks

1. Fresh `pwd.yml` site installs Frappe + ERPNext successfully, but **does not create a Company** until setup wizard / explicit Company insert.
2. Resource list endpoint shape confirmed: `GET /api/resource/{DocType}?limit_page_length=5` → `{"data":[...]}`.
3. Token API key/secret generation is **UNVERIFIED** (next discovery task before long-running scripts).

## Seed Implications

- Stage 2 must create, in order: **Company → Warehouses → Item Groups / Items → Suppliers → Purchase Orders / Receipts / Invoices**.
- Do not invent create payloads until each DocType's required fields are verified with a live `POST` (or by reading DocType meta via API).
- Expected empty lists today are correct — not a bug.
