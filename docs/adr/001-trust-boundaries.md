# ADR-001: Trust Boundaries and Single-Agent Design

## Status

Accepted — Stage 0

## Context

This project simulates a Forward Deployed Engineering engagement: an AI agent must investigate procurement exceptions in a live ERP (ERPNext) and prepare corrective purchase-order drafts, while remaining safe enough that a hiring manager would trust the architecture.

The temptation in agent demos is to give the LLM broad ERP write access and hope prompts prevent mistakes. That is the opposite of production FDE practice.

## Decision

1. **One LangGraph agent** with specialized tools. Multiple agents only if a later milestone proves a clear boundary benefit.
2. **ERPNext source code is never modified.** Integration uses supported REST APIs only.
3. **The LLM cannot write to ERPNext.** Agent tools may read ERP records and may *stage* an immutable proposal in our Postgres database. Only a deterministic Python execution gate creates ERP documents after human approval.
4. **Authorization lives in backend code**, not in system prompts. Slack button payloads carry a proposal ID; the gate reloads the stored payload, verifies the approver allowlist, checks the payload hash, and enforces idempotency.
5. **State separation:** ERPNext MariaDB owns suppliers, items, and purchasing documents. Our Postgres owns proposals, approvals, audit events, LangGraph checkpoints, and `pgvector` embeddings.
6. **Embeddings default to local open-weight models** (`BAAI/bge-small-en-v1.5` via `fastembed`) so policy/contract text stays in-process during development and CI. Cloud embeddings remain optional via `.env`.
7. **LLM provider is swappable** via `.env` (`openai` default). Enterprise customers can point the same interface at an approved VPC model without redesigning the agent.

## Consequences

- Slightly more code up front (proposal store + approval gate).
- Demos cannot "cheat" by skipping approval to make a screenshot work.
- Interview narrative maps cleanly to real FDE constraints: system of record integrity, human-in-the-loop for irreversible actions, and auditable execution.
