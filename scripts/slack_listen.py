#!/usr/bin/env python3
"""Listen in Slack for an item request and post one proposal to approve."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from langchain_core.language_models.chat_models import BaseChatModel
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.errors import SlackApiError

from procurement_agent.agent.model import openai_chat_model
from procurement_agent.approvals.service import ApprovalVault, init_database
from procurement_agent.approvals.writer import ErpNextDraftWriter
from procurement_agent.config import Settings, get_settings
from procurement_agent.erp.client import ErpNextReadClient
from procurement_agent.policy.retrieve import shared_embedder
from procurement_agent.slack.decisions import apply_decision
from procurement_agent.slack.item_reply import ItemReply, reply_to_text


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.app_log_level)
    bot_token, app_token, signing_secret = _tokens(settings)
    vault = ApprovalVault(init_database(settings.database_url), settings.approver_user_ids)
    writer = ErpNextDraftWriter(settings)
    model = openai_chat_model(settings)
    app = App(token=bot_token, signing_secret=signing_secret)

    @app.command("/propose")
    def propose(ack: Any, command: dict[str, Any], client: Any) -> None:
        ack()
        text = str(command.get("text") or "")
        channel = str(command["channel_id"])
        conversation_id = conversation_key(channel, str(command["user_id"]))
        _deliver(
            client,
            vault,
            channel,
            _answer(settings, vault, model, text, conversation_id=conversation_id),
            conversation_id=conversation_id,
        )

    @app.event("message")
    def on_message(event: dict[str, Any], client: Any) -> None:
        if event.get("channel") != settings.slack_channel_id:
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        channel = settings.slack_channel_id
        root_ts = str(event.get("thread_ts") or event["ts"])
        # Each Slack thread is a conversation. Root message and its replies share this key.
        conversation_id = conversation_key(channel, str(event.get("user") or ""), root_ts)
        if vault.conversation(conversation_id) is None:
            command_context = vault.conversation(conversation_key(channel, "", root_ts))
            if command_context:
                vault.save_conversation(conversation_id, command_context)
        _deliver(
            client,
            vault,
            settings.slack_channel_id,
            _answer(
                settings,
                vault,
                model,
                str(event.get("text") or ""),
                conversation_id=conversation_id,
            ),
            thread_ts=root_ts,
            conversation_id=conversation_id,
        )

    @app.action("approve_proposal")
    def approve(ack: Any, body: dict[str, Any], client: Any) -> None:
        ack()
        _act(vault, writer, body, client, "approve_proposal", settings.erpnext_base_url)

    @app.action("reject_proposal")
    def reject(ack: Any, body: dict[str, Any], client: Any) -> None:
        ack()
        _act(vault, writer, body, client, "reject_proposal")

    print("Listening in Slack. Compare suppliers for an item, then continue in that thread.")
    SocketModeHandler(app, app_token).start()


def _answer(
    settings: Settings,
    vault: ApprovalVault,
    model: BaseChatModel,
    text: str,
    *,
    conversation_id: str = "default",
) -> ItemReply:
    if not text.strip():
        return ItemReply("Name an item, for example: I need S-66100", None, None, None, None)
    try:
        orders, order_items, receipts, receipt_items, invoices, invoice_items = _load(settings)
    except Exception:
        logging.getLogger(__name__).exception("ERP lookup failed")
        return ItemReply("ERPNext is unavailable. Please try again.", None, None, None, None)
    return reply_to_text(
        vault,
        text,
        orders,
        order_items,
        receipts,
        receipt_items,
        model,
        today=datetime.now(ZoneInfo(settings.app_timezone)).date(),
        conversation_id=conversation_id,
        invoices=invoices,
        invoice_items=invoice_items,
        embedder=shared_embedder(),
    )


def _load(
    settings: Settings,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    with ErpNextReadClient(settings) as erp:
        orders = erp.list_all(
            "Purchase Order",
            ["name", "supplier", "transaction_date", "company", "currency", "docstatus"],
            filters=[["docstatus", "=", 1]],
        )
        order_items = erp.list_all(
            "Purchase Order Item",
            [
                "name",
                "parent",
                "item_code",
                "qty",
                "uom",
                "rate",
                "schedule_date",
                "description",
                "warehouse",
                "received_qty",
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
        invoices = erp.list_all(
            "Purchase Invoice",
            ["name", "supplier", "bill_no", "posting_date", "docstatus"],
            filters=[["docstatus", "=", 1]],
        )
        invoice_items = erp.list_all(
            "Purchase Invoice Item",
            ["name", "parent", "item_code", "qty", "rate", "purchase_order", "po_detail"],
            parent="Purchase Invoice",
        )
    return orders, order_items, receipts, receipt_items, invoices, invoice_items


def conversation_key(channel: str, user: str, thread_ts: str = "commands") -> str:
    return "slack:" + hashlib.sha256(f"{channel}:{user}:{thread_ts}".encode()).hexdigest()


def _deliver(
    client: Any,
    vault: ApprovalVault,
    channel: str,
    reply: ItemReply,
    *,
    thread_ts: str | None = None,
    conversation_id: str | None = None,
) -> None:
    if reply.slack_channel and reply.slack_ts:
        try:
            client.chat_update(
                channel=reply.slack_channel,
                ts=reply.slack_ts,
                text=reply.text,
                blocks=reply.blocks or [],
            )
            return
        except SlackApiError:
            logging.getLogger(__name__).exception("Card update failed; posting a new card")
    payload: dict[str, Any] = {"channel": channel, "text": reply.text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if reply.blocks:
        payload["blocks"] = reply.blocks
    response = client.chat_postMessage(**payload)
    getter = getattr(response, "get", None)
    ts = getter("ts") if callable(getter) else None
    if reply.proposal_id and isinstance(ts, str):
        vault.remember_slack(reply.proposal_id, channel, ts)
    # A /propose reply can become a normal Slack thread. Copy its durable context.
    if conversation_id and not thread_ts and isinstance(ts, str):
        saved = vault.conversation(conversation_id)
        if saved:
            # Resolve this alias before user-specific keys in on_message.
            vault.save_conversation(conversation_key(channel, "", ts), saved)


def _act(
    vault: ApprovalVault,
    writer: ErpNextDraftWriter,
    body: dict[str, Any],
    client: Any,
    action: str,
    erp_base_url: str | None = None,
) -> None:
    actions = body.get("actions") or []
    proposal_id = actions[0].get("value") if actions else ""
    user_id = str((body.get("user") or {}).get("id") or "")
    if not isinstance(proposal_id, str) or not proposal_id:
        text = "The button did not carry a proposal id."
    else:
        text = apply_decision(
            vault,
            writer,
            action=action,
            proposal_id=proposal_id,
            user_id=user_id,
        )
    channel = (body.get("channel") or {}).get("id")
    message_ts = (body.get("message") or {}).get("ts")
    stored = vault.get(proposal_id) if isinstance(proposal_id, str) and proposal_id else None
    if stored and stored.status == "executed" and stored.erp_document_name and erp_base_url:
        url = (
            erp_base_url.rstrip("/")
            + "/app/purchase-order/"
            + quote(stored.erp_document_name, safe="")
        )
        text += f" <{url}|Open draft in ERPNext>"
    if channel and message_ts:
        succeeded = bool(stored and stored.status == "executed") or text == "No order created."
        if succeeded:
            client.chat_update(channel=channel, ts=message_ts, text=text, blocks=[])
        else:
            client.chat_postEphemeral(channel=channel, user=user_id, text=text)
    else:
        print(text)


def _tokens(settings: Settings) -> tuple[str, str, str]:
    bot = settings.slack_bot_token
    app = settings.slack_app_token
    secret = settings.slack_signing_secret
    if bot is None or app is None or secret is None:
        raise SystemExit("Set SLACK_BOT_TOKEN, SLACK_APP_TOKEN, and SLACK_SIGNING_SECRET in .env.")
    return (
        bot.get_secret_value(),
        app.get_secret_value(),
        secret.get_secret_value(),
    )


if __name__ == "__main__":
    main()
