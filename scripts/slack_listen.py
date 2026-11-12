#!/usr/bin/env python3
"""Listen in Slack for an item request and post one proposal to approve."""

from __future__ import annotations

from datetime import date
from typing import Any

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from procurement_agent.approvals.service import ApprovalVault, init_database
from procurement_agent.approvals.writer import ErpNextDraftWriter
from procurement_agent.config import Settings, get_settings
from procurement_agent.erp.client import ErpNextReadClient
from procurement_agent.slack.decisions import apply_decision
from procurement_agent.slack.item_reply import reply_to_text


def main() -> None:
    settings = get_settings()
    bot_token, app_token, signing_secret = _tokens(settings)
    vault = ApprovalVault(init_database(settings.database_url), settings.approver_user_ids)
    writer = ErpNextDraftWriter(settings)
    app = App(token=bot_token, signing_secret=signing_secret)

    @app.command("/propose")
    def propose(ack: Any, command: dict[str, Any], client: Any) -> None:
        ack()
        text = str(command.get("text") or "")
        _post(client, str(command["channel_id"]), _answer(settings, vault, text))

    @app.event("message")
    def on_message(event: dict[str, Any], client: Any) -> None:
        if event.get("channel") != settings.slack_channel_id:
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        answer = _answer(settings, vault, str(event.get("text") or ""))
        if answer is None:
            return
        _post(client, settings.slack_channel_id, answer)

    @app.action("approve_proposal")
    def approve(ack: Any, body: dict[str, Any], client: Any) -> None:
        ack()
        _act(vault, writer, body, client, "approve_proposal")

    @app.action("reject_proposal")
    def reject(ack: Any, body: dict[str, Any], client: Any) -> None:
        ack()
        _act(vault, writer, body, client, "reject_proposal")

    print("Listening in Slack. Name an item, for example: I need S-66100")
    SocketModeHandler(app, app_token).start()


def _answer(
    settings: Settings,
    vault: ApprovalVault,
    text: str,
) -> tuple[str, list[dict[str, Any]] | None] | None:
    if not text.strip():
        return "Name an item, for example: I need S-66100", None
    orders, order_items, receipts, receipt_items = _load(settings)
    return reply_to_text(
        vault,
        text,
        orders,
        order_items,
        receipts,
        receipt_items,
        today=date.today(),
    )


def _load(
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
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
    return orders, order_items, receipts, receipt_items


def _post(
    client: Any,
    channel: str,
    answer: tuple[str, list[dict[str, Any]] | None] | None,
) -> None:
    if answer is None:
        return
    text, blocks = answer
    payload: dict[str, Any] = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    client.chat_postMessage(**payload)


def _act(
    vault: ApprovalVault,
    writer: ErpNextDraftWriter,
    body: dict[str, Any],
    client: Any,
    action: str,
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
    if channel and message_ts:
        client.chat_update(channel=channel, ts=message_ts, text=text, blocks=[])
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
