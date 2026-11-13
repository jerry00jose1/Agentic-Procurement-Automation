"""Turn a Slack message into a proposal card, or into a refusal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from procurement_agent.agent.buyer_request import BuyerRequest, RequestReadError
from procurement_agent.agent.item_request import build_item_purchase, file_item_purchase
from procurement_agent.approvals.service import ApprovalVault
from procurement_agent.slack.messages import purchase_message


class BuyerRequestReader(Protocol):
    def read(self, text: str, item_codes: set[str], suppliers: set[str]) -> BuyerRequest:
        """Turn the sentence into a request, or raise RequestReadError."""


@dataclass(frozen=True)
class ItemReply:
    text: str
    blocks: list[dict[str, Any]] | None
    proposal_id: str | None
    slack_channel: str | None
    slack_ts: str | None


def reply_to_text(
    vault: ApprovalVault,
    text: str,
    orders: list[dict[str, Any]],
    order_items: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    receipt_items: list[dict[str, Any]],
    reader: BuyerRequestReader,
    *,
    today: date,
) -> ItemReply:
    codes = {str(row.get("item_code")) for row in order_items if row.get("item_code")}
    suppliers = {str(row.get("supplier")) for row in orders if row.get("supplier")}
    try:
        request = reader.read(text, codes, suppliers)
    except RequestReadError as exc:
        return _say(exc.message)
    if request.item_code is None:
        return _say("Name an item this company has purchased, for example S-66100.")
    if request.named_supplier is not None and request.named_supplier in request.exclude:
        return _say(f"You asked for {request.named_supplier} and also excluded them.")
    purchase = build_item_purchase(
        orders,
        order_items,
        receipts,
        receipt_items,
        request,
        today=today,
    )
    if purchase is None:
        return _say(_missing(request, order_items))
    filed = file_item_purchase(vault, purchase)
    stored = filed.stored
    if stored.status != "proposed":
        if stored.erp_document_name:
            return _say(f"Already created {stored.erp_document_name}. No second purchase order.")
        return _say(f"This item already has a {stored.status} proposal.")
    text_out, blocks = purchase_message(stored, purchase, notice=filed.notice)
    if filed.posted_anew:
        return ItemReply(text_out, blocks, stored.id, None, None)
    return ItemReply(text_out, blocks, stored.id, stored.slack_channel, stored.slack_ts)


def _missing(request: BuyerRequest, order_items: list[dict[str, Any]]) -> str:
    sold = any(row.get("item_code") == request.item_code for row in order_items)
    if not sold:
        return f"Nobody has purchased {request.item_code}."
    if request.named_supplier:
        return f"{request.named_supplier} has not sold {request.item_code}."
    return f"Nobody left has sold {request.item_code} after those exclusions."


def _say(text: str) -> ItemReply:
    return ItemReply(text, None, None, None, None)
