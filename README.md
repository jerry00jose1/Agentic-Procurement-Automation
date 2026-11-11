# Agentic Procurement Automation

AI-powered procurement automation that sits **beside** ERPNext: a LangGraph agent investigates purchasing exceptions from live ERP data, recommends supplier alternatives, and stages replacement purchase orders for **human approval** before any ERP write.

This repository is intentionally shaped like a Forward Deployed Engineering engagement—not a chatbot demo.

## What you can prove

An operator sends:

> Identify suppliers responsible for delayed purchase orders, recommend alternatives, and prepare replacement purchase orders.

The system must:

1. Query real ERPNext purchase orders
2. Score supplier reliability from historical deliveries
3. Stage an immutable replacement-PO proposal
4. Request Slack approval
5. Create a **Draft** Purchase Order in ERPNext only after backend authorization checks
6. Leave a complete audit trail

## Architecture (trust boundaries)

```text
Slack ──► FastAPI ──► LangGraph agent (read + propose only)
              │                │
              │                ├── ERPNext REST (reads)
              │                └── Postgres (proposals / audit / pgvector)
              │
              └── Deterministic Approval Gate ──► ERPNext REST (writes Draft POs)
```

See [`docs/adr/001-trust-boundaries.md`](docs/adr/001-trust-boundaries.md).

## Stack

| Layer | Choice |
|---|---|
| ERP | ERPNext via official [`frappe_docker`](https://github.com/frappe/frappe_docker) `pwd.yml` |
| Backend | Python 3.11+, FastAPI |
| Agent | LangGraph + LangChain |
| LLM | Configurable (`OPENAI` default) |
| Embeddings | Local `BAAI/bge-small-en-v1.5` via `fastembed` (optional OpenAI) |
| App state | PostgreSQL + `pgvector` |
| Chat | Slack Bolt (Socket Mode) |

## Prerequisites

- Docker Desktop running
- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

## Quick start (Stage 0 → 1)

```bash
# 1) Install Python tooling
cp .env.example .env
make install

# 2) Start ERPNext (first boot pulls images + creates site — often 3–8 minutes)
make up-erpnext
make wait-erpnext

# 3) Open the ERP UI
open http://localhost:8080
# Login: Administrator / admin

# 4) Probe the live REST API (no invented endpoints)
make probe
```

Then update [`docs/erpnext-discovery.md`](docs/erpnext-discovery.md) with verified DocType behavior before seeding data or writing agent tools.

## Project status

| Stage | Status |
|---|---|
| 0 Repo foundation | Done |
| 1 ERPNext Docker + discovery | Done (live at `:8080`, API probed, company empty) |
| 2 Seed broken supply-chain data | Next |
| 3–8 Agent, approvals, Slack, evals | Planned |

See [`docs/execution-plan.md`](docs/execution-plan.md).

## Safety rules (non-negotiable)

- The LLM never receives unrestricted ERP write credentials
- Approved proposal payloads are hashed and immutable
- Authorization is enforced in Python, not prompt text
- ERPNext source code is never modified
