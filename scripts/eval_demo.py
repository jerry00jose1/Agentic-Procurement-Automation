#!/usr/bin/env python3
"""Rehearse real LLM interpretation using synthetic ERP data and a temporary vault.

Uses the configured OpenAI model. Never connects to Slack or ERPNext.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from procurement_agent.agent.conversation import resolve_date
from procurement_agent.agent.model import openai_chat_model
from procurement_agent.approvals.orders import DraftPurchaseOrder
from procurement_agent.approvals.service import ApprovalVault, init_database
from procurement_agent.config import get_settings
from procurement_agent.slack.decisions import apply_decision
from procurement_agent.slack.item_reply import reply_to_text


class RecordingWriter:
    def __init__(self) -> None:
        self.orders: list[DraftPurchaseOrder] = []

    def create_draft(self, order: DraftPurchaseOrder) -> str:
        self.orders.append(order)
        return "DEMO-PO-001"

    def submit(self, name: str) -> None:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        required=True,
        help="Call the configured LLM; uses API credits, synthetic data only.",
    )
    parser.parse_args()
    # Provider errors can contain sensitive request details; only report the failed check below.
    logging.disable(logging.CRITICAL)
    settings = get_settings()
    model = openai_chat_model(settings)
    today = datetime.now(ZoneInfo(settings.app_timezone)).date()
    path = Path(__file__).resolve().parents[1] / "tests/fixtures/demo_procurement.json"
    records = json.loads(path.read_text())

    with TemporaryDirectory(prefix="procurement-demo-") as directory:
        vault = ApprovalVault(init_database(f"sqlite:///{directory}/vault.sqlite"), {"DEMO"})

        def say(text: str, conversation: str = "demo"):
            reply = reply_to_text(
                vault, text, **records, model=model, today=today, conversation_id=conversation
            )
            print(f"\nUSER: {text}\nBOT: {reply.text}", flush=True)
            return reply

        compared = say("Which suppliers can provide ITEM-A? Compare price and delivery history.")
        assert compared.proposal_id is None
        assert "Supplier X" in compared.text and "Supplier Y" in compared.text
        prepared = say("Prepare an order for 20 units from Supplier X, needed next Friday.")
        assert prepared.proposal_id, "The complete purchase should produce an approval card."
        original = vault.get(prepared.proposal_id)
        assert original and original.payload.items[0].qty == 20
        assert original.payload.schedule_date == resolve_date("next Friday", today)

        question = say("What are the supplier prices for ITEM-A? Just compare; don't order.")
        assert question.proposal_id is None
        assert vault.get(original.id).payload_hash == original.payload_hash
        revised = say("Make that 30 units.")
        assert revised.proposal_id and revised.proposal_id != original.id
        stored = vault.get(revised.proposal_id)
        assert stored and stored.payload.items[0].qty == 30
        assert stored.payload.supplier == "Supplier X"
        assert stored.payload.schedule_date == original.payload.schedule_date

        writer = RecordingWriter()
        apply_decision(
            vault, writer, action="approve_proposal", proposal_id=original.id, user_id="DEMO"
        )
        assert not writer.orders, "A stale approval must not execute."
        apply_decision(
            vault, writer, action="approve_proposal", proposal_id=stored.id, user_id="DEMO"
        )
        assert len(writer.orders) == 1 and writer.orders[0].items[0].qty == 30

        vague = say("Order some packaging", "clarification")
        assert vague.proposal_id is None
        assert "Which item" in vague.text and "How many" in vague.text
        filled = say("ITEM-A, 20 units from Supplier X, next Friday", "clarification")
        assert filled.proposal_id
        missing = say("Prepare an order for ITEM-A from Supplier X next Friday", "missing-qty")
        assert missing.proposal_id is None and "How many" in missing.text
        print(
            "\nPASS: comparison, purchase, question-only, revision, stale approval, "
            "draft execution, clarification and missing quantity.",
            flush=True,
        )


if __name__ == "__main__":
    main()
