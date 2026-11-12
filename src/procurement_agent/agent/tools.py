"""Tools the agent is allowed to call. Approval and ERPNext writes are not among them."""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from procurement_agent.agent.case import ReplacementCase
from procurement_agent.approvals.service import ApprovalVault


def make_proposal_tools(case: ReplacementCase, vault: ApprovalVault) -> list[BaseTool]:
    @tool
    def find_replacement_case() -> str:
        """Return the late supplier, the better supplier, and the exact order terms."""
        return json.dumps(case.public_view())

    @tool
    def stage_replacement(case_id: str) -> str:
        """File the prepared order. Supplier, item, quantity, and price stay as prepared."""
        if case_id != case.case_id:
            return json.dumps({"error": "Unknown case_id. Use the id from find_replacement_case."})
        stored = vault.stage(case.case_id, case.order)
        line = stored.payload.items[0]
        return json.dumps(
            {
                "proposal_id": stored.id,
                "status": stored.status,
                "supplier": stored.payload.supplier,
                "item_code": line.item_code,
                "qty": line.qty,
                "rate": line.rate,
            }
        )

    return [find_replacement_case, stage_replacement]
