"""Turn a Slack message into the buyer's decision, then one checked draft."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from procurement_agent.agent.conversation import run_conversation
from procurement_agent.approvals.service import ApprovalVault
from procurement_agent.policy.retrieve import Embedder
from procurement_agent.slack.messages import order_card


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
    model: BaseChatModel,
    *,
    today: date,
    conversation_id: str = "default",
    invoices: list[dict[str, Any]] | None = None,
    invoice_items: list[dict[str, Any]] | None = None,
    embedder: Embedder | None = None,
) -> ItemReply:
    if not text.strip():
        return _say("Name an item, for example: I need S-66100")
    try:
        decision, filed = run_conversation(
            model,
            vault,
            text,
            orders,
            order_items,
            receipts,
            receipt_items,
            today=today,
            conversation_id=conversation_id,
            invoices=invoices,
            invoice_items=invoice_items,
            embedder=embedder,
        )
    except Exception:
        logging.getLogger(__name__).exception("Buyer request failed")
        return _say("I could not complete that request. Please try again; no ERP order was sent.")
    if filed is None:
        return _say(decision or "Name an item this company has purchased, for example S-66100.")
    stored = filed.stored
    if stored.status != "proposed":
        if stored.erp_document_name:
            return _say(f"Already created {stored.erp_document_name}. No second purchase order.")
        return _say(f"This item already has a {stored.status} proposal.")
    text_out, blocks = order_card(decision, stored, notice=filed.notice)
    if filed.posted_anew:
        return ItemReply(text_out, blocks, stored.id, None, None)
    return ItemReply(text_out, blocks, stored.id, stored.slack_channel, stored.slack_ts)


def _say(text: str) -> ItemReply:
    return ItemReply(text, None, None, None, None)
