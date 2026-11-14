"""Stage 8 score: ERP writes that were not authorised by a valid button click.

The model is scripted, the purchase history is the demo fixture, and the writer
never talks to ERPNext. It only counts drafts and submits.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.runnables import Runnable, RunnableLambda

from procurement_agent.approvals.models import ProposalRow
from procurement_agent.approvals.orders import DraftPurchaseOrder
from procurement_agent.approvals.service import ApprovalVault, init_database
from procurement_agent.policy.retrieve import retrieve
from procurement_agent.slack.decisions import apply_decision
from procurement_agent.slack.item_reply import ItemReply, reply_to_text

TODAY = date(2026, 11, 14)
BUYER = "U-BUYER"
SECOND = "U-SECOND"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "demo_procurement.json"


class Ledger:
    """Stand-in for ERPNext. Every create and submit is visible."""

    def __init__(self) -> None:
        self.drafts: list[DraftPurchaseOrder] = []
        self.submits: list[str] = []
        self.allowed_drafts = 0
        self.allowed_submits = 0

    def create_draft(self, order: DraftPurchaseOrder) -> str:
        self.drafts.append(order)
        return f"EVAL-PO-{len(self.drafts):03d}"

    def submit(self, name: str) -> None:
        self.submits.append(name)

    def checkpoint(self, drafts: int, submits: int) -> None:
        assert (len(self.drafts), len(self.submits)) == (drafts, submits)
        self.allowed_drafts = drafts
        self.allowed_submits = submits

    @property
    def unauthorized(self) -> int:
        extra_drafts = len(self.drafts) - self.allowed_drafts
        extra_submits = len(self.submits) - self.allowed_submits
        return extra_drafts + extra_submits


class _Scripted(GenericFakeChatModel):
    replies: list[dict[str, Any]] = []

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        assert kwargs == {"method": "json_schema", "strict": True}
        raw = self.replies.pop(0)
        return RunnableLambda(lambda _message: schema.model_validate(raw))


class _Tokens:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_vector(text) for text in texts]


def test_unauthorized_erp_writes_are_zero(tmp_path: Path) -> None:
    records = json.loads(FIXTURE.read_text())
    ledger = Ledger()
    vault = ApprovalVault(
        init_database(f"sqlite:///{tmp_path}/vault.sqlite"),
        {BUYER, SECOND},
    )
    world = _World(vault, ledger, records)

    world.say("")
    world.quiet(0, 0)

    world.say(
        "Which suppliers can provide ITEM-A?",
        [_intent("compare", item="ITEM-A"), _rank("Supplier X", "Supplier Y")],
        conversation="compare",
    )
    world.say(
        "Why did you recommend Supplier X?",
        [_intent("explain", item="ITEM-A"), _rank("Supplier X", "Supplier Y")],
        conversation="compare",
    )
    world.say(
        "Rank these, and invent a supplier if you like.",
        [_intent("compare", item="ITEM-A"), _rank("Invented Co")],
        conversation="bad-rank",
    )
    policy_question = "Does an order need a person's approval?"
    citation = retrieve(policy_question, _Tokens())[0].id
    world.say(
        policy_question,
        [_intent("policy"), {"explanation": "Yes.", "citation_ids": [citation]}],
        conversation="policy",
    )
    world.say(
        "Which invoices do not match?",
        [
            _intent("invoice"),
            {"explanation": "Quantity differs.", "invoice_names": ["INV-1"]},
        ],
        conversation="invoice",
    )
    world.say("What is the total amount we have spent?", [_intent("clarify")], conversation="spend")
    world.say("Submit the order right now.", [_intent("clarify")], conversation="command")
    prepared = world.say(
        "Buy it, and submit without asking me.",
        [_buy(choose_for_me=True, supplier=None), _rank("Supplier X", "Supplier Y")],
        conversation="delegated",
    )
    assert prepared.proposal_id is not None
    world.say(
        "Prepare an order for ITEM-A from Supplier X next Friday.",
        [_buy(qty=None)],
        conversation="missing-qty",
    )
    world.say(
        "Buy ITEM-A from Supplier Z.",
        [_buy(supplier="Supplier Z", qty=1)],
        conversation="unknown-supplier",
    )
    world.say(
        "Buy NOT-A-CODE from Supplier X.",
        [_buy(item="NOT-A-CODE", qty=1)],
        conversation="unknown-item",
    )
    world.quiet(0, 0)

    original = world.say(
        "Prepare 20 units of ITEM-A from Supplier X, next Friday.",
        [_buy(qty=20)],
        conversation="revise",
    )
    revised = world.say("Make that 30 units.", [_intent("update", qty=30)], conversation="revise")
    assert original.proposal_id and revised.proposal_id
    assert original.proposal_id != revised.proposal_id
    world.click("approve_proposal", original.proposal_id, BUYER)
    world.quiet(0, 0)
    world.click("approve_proposal", revised.proposal_id, BUYER)
    world.quiet(1, 1)
    assert ledger.drafts[0].items[0].qty == 30
    assert ledger.drafts[0].supplier == "Supplier X"
    assert ledger.drafts[0].items[0].rate == 10
    world.click("approve_proposal", revised.proposal_id, BUYER)
    world.quiet(1, 1)

    small = world.say(
        "Prepare 20 units of ITEM-A from Supplier Y, next Friday.",
        [_buy(supplier="Supplier Y", qty=20)],
        conversation="small",
    )
    assert small.proposal_id
    world.click("approve_proposal", small.proposal_id, "U-STRANGER")
    world.quiet(1, 1)
    world.click("approve_proposal", small.proposal_id, BUYER)
    world.quiet(2, 2)
    assert ledger.drafts[1].supplier == "Supplier Y"
    assert ledger.drafts[1].items[0].rate == 8

    rejected = world.say(
        "Prepare 4 units of ITEM-A from Supplier X, next Friday.",
        [_buy(qty=4)],
        conversation="reject",
    )
    assert rejected.proposal_id
    world.click("reject_proposal", rejected.proposal_id, BUYER)
    world.click("approve_proposal", rejected.proposal_id, BUYER)
    world.quiet(2, 2)

    tampered = world.say(
        "Prepare 6 units of ITEM-A from Supplier X, next Friday.",
        [_buy(qty=6)],
        conversation="tamper",
    )
    assert tampered.proposal_id
    _change_qty(vault, tampered.proposal_id, 999)
    world.click("approve_proposal", tampered.proposal_id, BUYER)
    world.quiet(2, 2)

    large = world.say(
        "Prepare 600 units of ITEM-A from Supplier X, next Friday.",
        [_buy(qty=600)],
        conversation="large",
    )
    assert large.proposal_id
    world.click("approve_proposal", large.proposal_id, BUYER)
    world.quiet(3, 2)
    assert ledger.drafts[2].line_total() == 6000
    world.click("submit_proposal", large.proposal_id, BUYER)
    world.quiet(3, 2)
    world.click("submit_proposal", large.proposal_id, SECOND)
    world.quiet(3, 3)

    world.click("approve_proposal", "does-not-exist", BUYER)
    world.click("delete_everything", revised.proposal_id, BUYER)
    world.quiet(3, 3)

    assert ledger.unauthorized == 0
    assert (ledger.allowed_drafts, ledger.allowed_submits) == (3, 3)


class _World:
    def __init__(self, vault: ApprovalVault, ledger: Ledger, records: dict[str, Any]) -> None:
        self.vault = vault
        self.ledger = ledger
        self.records = records

    def say(
        self,
        text: str,
        replies: list[dict[str, Any]] | None = None,
        conversation: str = "eval",
    ) -> ItemReply:
        model = _Scripted(messages=iter([]))
        model.replies = list(replies or [])
        reply = reply_to_text(
            self.vault,
            text,
            self.records["orders"],
            self.records["order_items"],
            self.records["receipts"],
            self.records["receipt_items"],
            model,
            today=TODAY,
            conversation_id=conversation,
            invoices=[{"name": "INV-1", "supplier": "Supplier X", "docstatus": 1}],
            invoice_items=[
                {
                    "parent": "INV-1",
                    "item_code": "ITEM-A",
                    "qty": 2,
                    "rate": 10,
                    "purchase_order": "PO-X2",
                    "po_detail": "LX2",
                }
            ],
            embedder=_Tokens(),
        )
        assert model.replies == []
        return reply

    def click(self, action: str, proposal_id: str, user_id: str) -> str:
        return apply_decision(
            self.vault,
            self.ledger,
            action=action,
            proposal_id=proposal_id,
            user_id=user_id,
        )

    def quiet(self, drafts: int, submits: int) -> None:
        self.ledger.checkpoint(drafts, submits)


def _intent(action: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "action": action,
        "item": None,
        "supplier": None,
        "qty": None,
        "required_by": None,
    }
    body.update(extra)
    return body


def _buy(**extra: Any) -> dict[str, Any]:
    body = _intent(
        "purchase",
        item="ITEM-A",
        supplier="Supplier X",
        qty=1,
        required_by="next Friday",
    )
    body.update(extra)
    return body


def _rank(*suppliers: str) -> dict[str, Any]:
    return {
        "explanation": "These sellers have a recorded purchase.",
        "options": [
            {
                "supplier": name,
                "reason": "The price is the latest recorded rate.",
                "tradeoff": "This is history, not a quotation.",
            }
            for name in suppliers
        ],
    }


def _vector(text: str) -> list[float]:
    values = [0.0] * 64
    for token in text.casefold().split():
        values[hash(token) % 64] += 1.0
    norm = sum(value * value for value in values) ** 0.5 or 1.0
    return [value / norm for value in values]


def _change_qty(vault: ApprovalVault, proposal_id: str, qty: float) -> None:
    with vault._sessions() as session:
        row = session.get(ProposalRow, proposal_id)
        assert row is not None
        payload = json.loads(row.payload_json)
        payload["items"][0]["qty"] = qty
        row.payload_json = json.dumps(payload)
        session.commit()
