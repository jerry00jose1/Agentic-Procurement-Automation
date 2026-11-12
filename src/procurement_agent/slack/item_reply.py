"""Turn a Slack message that names an item into a proposal card."""

from __future__ import annotations

from datetime import date
from typing import Any

from procurement_agent.agent.item_request import (
    build_item_purchase,
    file_item_purchase,
    mentioned_item_code,
)
from procurement_agent.approvals.service import ApprovalVault
from procurement_agent.slack.messages import purchase_message


def reply_to_text(
    vault: ApprovalVault,
    text: str,
    orders: list[dict[str, Any]],
    order_items: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    receipt_items: list[dict[str, Any]],
    *,
    today: date,
) -> tuple[str, list[dict[str, Any]] | None] | None:
    codes = {str(row.get("item_code")) for row in order_items if row.get("item_code")}
    item_code = mentioned_item_code(text, codes)
    if item_code is None:
        return None
    purchase = build_item_purchase(
        item_code,
        orders,
        order_items,
        receipts,
        receipt_items,
        today=today,
    )
    if purchase is None:
        return f"Nobody has purchased {item_code}.", None
    stored = file_item_purchase(vault, purchase)
    return purchase_message(stored, purchase.considered)
