# Execution Plan

Build order for the FDE portfolio project. Each stage ends with something observable.

| Stage | Outcome | Proof | Stop rule |
|---|---|---|---|
| 0 | Repo foundation (`uv`, lint, ADRs, compose files) | `uv sync` succeeds; docs present | Do not invent ERP endpoints |
| 1 | ERPNext live on Docker | Browser login at `:8080`; `make probe` writes discovery notes | Do not seed until schemas are verified |
| 2 | Broken-supply-chain seed | Delayed POs / unreliable suppliers visible in ERPNext UI | Do not build agent tools against mocked inventations |
| 3 | Typed ERP client | Unit + live read of one PO | No write client for agent tools |
| 4 | Approval vault + audit | Tests reject unauthorized / mutated payloads | No LLM yet |
| 5 | LangGraph vertical slice (CLI) | Primary demo scenario from CLI with staged proposal | No Slack until CLI works |
| 6 | Slack HITL | Approve creates Draft PO in ERPNext | No second agent / queue |
| 7 | Policy RAG + invoice matching | Citations + 3-way match exceptions | Only after Stage 6 is repeatable |
| 8 | Chaos + evals + CI | Unauthorized writes = 0 on eval suite | Polish, don't expand scope |

## Primary Demo Sentence

> Identify suppliers responsible for delayed purchase orders, recommend alternatives, and prepare replacement purchase orders.
