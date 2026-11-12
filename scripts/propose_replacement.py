#!/usr/bin/env python3
"""File one replacement purchase order into the approval vault.

The agent reads ERPNext and stages a proposal. It does not approve it
and it does not create a purchase order.
"""

from __future__ import annotations

from datetime import date

from procurement_agent.agent.case import build_replacement_case
from procurement_agent.agent.graph import (
    build_proposal_agent,
    final_text,
    openai_chat_model,
    proposal_request,
)
from procurement_agent.approvals.service import ApprovalVault, init_database
from procurement_agent.config import get_settings
from procurement_agent.erp.client import ErpNextReadClient


def main() -> None:
    settings = get_settings()
    model = openai_chat_model(settings)
    with ErpNextReadClient(settings) as erp:
        orders = erp.list_all(
            "Purchase Order",
            ["name", "supplier", "transaction_date", "company", "docstatus"],
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
                "schedule_date",
                "description",
                "warehouse",
            ],
            parent="Purchase Order",
        )
        receipts = erp.list_all(
            "Purchase Receipt",
            ["name", "posting_date", "docstatus"],
            filters=[["docstatus", "=", 1]],
        )
        receipt_items = erp.list_all(
            "Purchase Receipt Item",
            ["name", "parent", "purchase_order_item"],
            parent="Purchase Receipt",
        )

    case = build_replacement_case(
        orders,
        order_items,
        receipts,
        receipt_items,
        today=date.today(),
    )
    if case is None:
        raise SystemExit("No late supplier has a better supplier for the same item.")

    vault = ApprovalVault(init_database(settings.database_url), settings.approver_user_ids)
    agent = build_proposal_agent(model, vault, case)
    result = agent.invoke(proposal_request())
    stored = vault.get_by_key(case.case_id)
    if stored is None or stored.status != "proposed":
        raise SystemExit("The agent did not file a proposal.")

    line = stored.payload.items[0]
    print(f"Late supplier: {case.late_supplier}")
    print(
        f"  late {case.late_lines}/{case.scored_lines}, "
        f"on time {case.on_time_rate:.0%}, avg {case.avg_late_days:.1f} days"
    )
    print(
        f"Alternative: {stored.payload.supplier} "
        f"(on time {case.alternative_on_time_rate:.0%})"
    )
    print(f"Item {line.item_code}  qty {line.qty:g}  rate {line.rate:g} {stored.payload.currency}")
    print(f"Source order: {case.source_order}")
    print(f"Proposal {stored.id}  status {stored.status}")
    print("No purchase order was created in ERPNext.")
    text = final_text(result)
    if text:
        print()
        print(text)


if __name__ == "__main__":
    main()
