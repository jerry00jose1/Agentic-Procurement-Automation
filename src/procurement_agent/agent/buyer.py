"""The buyer. It sees the seller rows, decides, and files one order.

Python calculates the rows and refuses a supplier or a price that is not in them.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, tool

from procurement_agent.agent.graph import final_text
from procurement_agent.agent.item_request import (
    MIN_SCORED_LINES,
    FiledPurchase,
    SellerFact,
    file_item_purchase,
    order_for,
    seller_facts,
)
from procurement_agent.approvals.service import ApprovalVault

BUYER_RULES = f"""You are the purchasing buyer for Northwind Components.
You decide one draft purchase order from the records a tool returns.
You do not invent suppliers, prices, or delivery results.

Call lookup_sellers with the item code in the message.
If the message has no item code, ask which item they want and do not file an order.

Each seller row has supplier, on_time_rate, scored_deliveries, latest_qty,
latest_rate, and latest_date.
on_time_rate and scored_deliveries are already calculated. Use them as given.

Choose in this order:
1. Match a supplier the buyer names only to these rows.
   One row matches: that is the supplier they mean.
   No row matches: say that company has not sold the item, then choose from the rows.
   Several rows match: ask which supplier they mean, and do not file yet.
2. If they named a seller, choose that seller.
   If they said not to use a seller, remove that row before choosing.
3. If they asked for a cheaper or the lowest price, choose the lowest latest_rate
   among rows with at least {MIN_SCORED_LINES} scored deliveries.
   If none have {MIN_SCORED_LINES}, choose the lowest latest_rate among the rows left.
4. Otherwise choose the highest on_time_rate
   among rows with at least {MIN_SCORED_LINES} scored deliveries.
   If none have {MIN_SCORED_LINES}, choose the most recent latest_date.
5. The price is that row's latest_rate.
   The quantity is the number they stated, otherwise that row's latest_qty.

Call file_purchase with the item code, the supplier name exactly as the row gives it,
and the quantity.
If file_purchase returns an error, correct the call from the rows.
Do not invent a supplier to get past the error.

After the order is filed, reply in this order, in a few short sentences:
What they asked.
The supplier you chose and the single reason.
The other sellers, one line each, when they asked to see the options.
Do not ask them to approve. Buttons are added after your reply.
You cannot approve the order, and you cannot create it in ERPNext.
"""


def run_buyer(
    model: BaseChatModel,
    vault: ApprovalVault,
    text: str,
    orders: list[dict[str, Any]],
    order_items: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    receipt_items: list[dict[str, Any]],
    *,
    today: date,
) -> tuple[str, FiledPurchase | None]:
    """Let the model read the seller rows and file one checked order."""
    filed: list[FiledPurchase] = []
    tools = make_buyer_tools(
        vault,
        orders,
        order_items,
        receipts,
        receipt_items,
        today=today,
        filed=filed,
    )
    agent = create_agent(model, tools, system_prompt=BUYER_RULES)
    result = agent.invoke({"messages": [HumanMessage(content=text)]})
    return final_text(result), filed[-1] if filed else None


def make_buyer_tools(
    vault: ApprovalVault,
    orders: list[dict[str, Any]],
    order_items: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    receipt_items: list[dict[str, Any]],
    *,
    today: date,
    filed: list[FiledPurchase],
) -> list[BaseTool]:
    def facts_for(item_code: str) -> list[SellerFact]:
        code = _stored_code(item_code, order_items)
        if code is None:
            return []
        return seller_facts(code, orders, order_items, receipts, receipt_items)

    @tool
    def lookup_sellers(item_code: str) -> str:
        """Return every supplier who has sold this item, with the recorded facts."""
        code = _stored_code(item_code, order_items)
        if code is None:
            return json.dumps({"error": f"Nobody has purchased {item_code}."})
        rows = [
            {
                "supplier": fact.supplier,
                "on_time_rate": fact.on_time_rate,
                "scored_deliveries": fact.scored_lines,
                "latest_qty": fact.qty,
                "latest_rate": fact.rate,
                "latest_date": fact.when.isoformat(),
            }
            for fact in facts_for(code)
        ]
        return json.dumps({"item_code": code, "sellers": rows})

    @tool
    def file_purchase(item_code: str, supplier: str, qty: float) -> str:
        """File one draft. The supplier must be a name lookup_sellers returned."""
        code = _stored_code(item_code, order_items)
        facts = [] if code is None else facts_for(code)
        fact = next((row for row in facts if row.supplier == supplier), None)
        if code is None or fact is None:
            names = [row.supplier for row in facts]
            return json.dumps(
                {
                    "error": f"{supplier} has not sold {item_code}.",
                    "sellers": names,
                }
            )
        if qty <= 0:
            return json.dumps({"error": "The quantity has to be greater than zero."})
        purchase = file_item_purchase(vault, code, order_for(fact, code, qty, today))
        filed.append(purchase)
        line = purchase.stored.payload.items[0]
        return json.dumps(
            {
                "proposal_id": purchase.stored.id,
                "supplier": purchase.stored.payload.supplier,
                "item_code": line.item_code,
                "qty": line.qty,
                "rate": line.rate,
                "currency": purchase.stored.payload.currency,
            }
        )

    return [lookup_sellers, file_purchase]


def _stored_code(item_code: str, order_items: list[dict[str, Any]]) -> str | None:
    folded = item_code.casefold()
    for row in order_items:
        code = str(row.get("item_code") or "")
        if code and code.casefold() == folded:
            return code
    return None
