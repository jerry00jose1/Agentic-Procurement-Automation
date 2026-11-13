"""Turn a Slack button into a vault call. The button value is reread from Postgres."""

from __future__ import annotations

from procurement_agent.approvals.errors import ApprovalError
from procurement_agent.approvals.service import ApprovalVault, DraftPurchaseOrderWriter


def apply_decision(
    vault: ApprovalVault,
    writer: DraftPurchaseOrderWriter,
    *,
    action: str,
    proposal_id: str,
    user_id: str,
) -> str:
    if action == "reject_proposal":
        return _reject(vault, proposal_id, user_id)
    if action == "approve_proposal":
        return _approve(vault, writer, proposal_id, user_id)
    return "Unknown action."


def _reject(vault: ApprovalVault, proposal_id: str, user_id: str) -> str:
    try:
        vault.reject(proposal_id, user_id)
    except ApprovalError as exc:
        return str(exc)
    return "No order created."


def _approve(
    vault: ApprovalVault,
    writer: DraftPurchaseOrderWriter,
    proposal_id: str,
    user_id: str,
) -> str:
    before = vault.get(proposal_id)
    if before is not None and before.status == "executed" and before.erp_document_name:
        return f"Already created {before.erp_document_name}. No second purchase order."
    try:
        if before is None or before.status == "proposed":
            vault.approve(proposal_id, user_id)
        stored = vault.execute(proposal_id, user_id, writer)
    except ApprovalError as exc:
        return str(exc)
    return f"Created draft purchase order {stored.erp_document_name}."
