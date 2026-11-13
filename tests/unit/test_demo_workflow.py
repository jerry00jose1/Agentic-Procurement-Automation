import json
import runpy
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda
from sqlalchemy import func, select

from procurement_agent.agent.conversation import BuyerIntent, Conversation, resolve_date
from procurement_agent.agent.recommendations import SupplierDecision
from procurement_agent.approvals.models import ProposalRow
from procurement_agent.approvals.orders import DraftPurchaseOrder
from procurement_agent.approvals.service import ApprovalVault, init_database
from procurement_agent.approvals.writer import ErpNextDraftWriter
from procurement_agent.config import Settings
from procurement_agent.slack.decisions import apply_decision
from procurement_agent.slack.item_reply import reply_to_text
from procurement_agent.slack.messages import button_values

TODAY = date(2026, 11, 13)
ROOT = Path(__file__).resolve().parents[2]


class IntentModel(GenericFakeChatModel):
    decisions: list[dict[str, Any]] = []

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        assert kwargs == {"method": "json_schema", "strict": True}
        if schema is BuyerIntent:
            return self | RunnableLambda(lambda message: schema.model_validate_json(message.content))
        assert schema is SupplierDecision
        decision = self.decisions.pop(0)
        return RunnableLambda(lambda _message: schema.model_validate(decision))


def intent(action: str, **values: Any) -> dict[str, Any]:
    return dict(action=action, item=None, supplier=None, qty=None, required_by=None) | values


class Demo:
    def __init__(self, path: Path):
        self.url = f"sqlite:///{path}"
        self.sessions = init_database(self.url)
        self.vault = ApprovalVault(self.sessions, {"U1"})
        self.data = json.loads((ROOT / "tests/fixtures/demo_procurement.json").read_text())

    def say(
        self,
        text: str,
        parsed: dict[str, Any],
        conversation_id: str = "demo",
        decision: dict[str, Any] | None = None,
    ):
        model = IntentModel(messages=iter([AIMessage(content=json.dumps(parsed))]))
        model.decisions = [] if decision is None else [decision]
        return reply_to_text(
            self.vault,
            text,
            **self.data,
            model=model,
            today=TODAY,
            conversation_id=conversation_id,
        )

    def count(self) -> int:
        with self.sessions() as session:
            return session.scalar(select(func.count()).select_from(ProposalRow)) or 0

    def prepare(self):
        return self.say(
            "Prepare 20 ITEM-A from Supplier X next Friday",
            intent(
                "purchase", item="ITEM-A", supplier="Supplier X", qty=20, required_by="next Friday"
            ),
        )


@pytest.fixture
def demo(tmp_path):
    return Demo(tmp_path / "demo.sqlite")


def test_three_scenarios_end_to_end_with_actual_writer_http_contract(demo):
    comparison = demo.say(
        "Which suppliers can provide ITEM-A? Compare price and delivery history.",
        intent("compare", item="ITEM-A"),
        decision=_shortlist(),
    )
    assert "Supplier X: latest unit price 10 EUR" in comparison.text
    assert "Supplier Y: latest unit price 8 EUR" in comparison.text
    assert "100% on time across 2" in comparison.text
    assert "0% on time across 2" in comparison.text
    assert comparison.blocks is None and demo.count() == 0

    prepared = demo.say(
        "Prepare an order for 20 units from Supplier X, needed next Friday.",
        intent("purchase", supplier="Supplier X", qty=20, required_by="next Friday"),
    )
    original = demo.vault.get(prepared.proposal_id)
    assert original.payload.items[0].qty == 20
    assert original.payload.schedule_date == date(2026, 11, 20)
    demo.vault.remember_slack(original.id, "C1", "123.456")

    # Recreate the service to prove context survives a listener restart.
    demo.vault = ApprovalVault(init_database(demo.url), {"U1"})
    revised = demo.say("Make that 30 units.", intent("update", qty=30))
    stored = demo.vault.get(revised.proposal_id)
    assert stored.id != original.id
    assert demo.vault.get(original.id).status == "rejected"
    assert stored.payload.items[0].qty == 30
    assert stored.payload.supplier == "Supplier X"
    assert stored.payload.schedule_date == date(2026, 11, 20)
    assert revised.slack_ts == "123.456"
    assert button_values(revised.blocks) == [stored.id, stored.id]
    assert "300.00 EUR" in revised.text and "2026-11-20" in revised.text

    settings = Settings(
        _env_file=None,
        erpnext_base_url="http://erpnext.test",
        erpnext_api_key="test-key",
        erpnext_api_secret="test-secret",
    )
    writer = ErpNextDraftWriter(settings)
    try:
        with respx.mock(base_url="http://erpnext.test") as router:
            post = router.post("/api/resource/Purchase Order").mock(
                return_value=httpx.Response(200, json={"data": {"name": "PO-DEMO"}})
            )
            stale = apply_decision(
                demo.vault, writer, action="approve_proposal", proposal_id=original.id, user_id="U1"
            )
            assert "Created draft" not in stale and not post.called
            result = apply_decision(
                demo.vault, writer, action="approve_proposal", proposal_id=stored.id, user_id="U1"
            )
            assert "Created draft purchase order PO-DEMO" in result
            body = json.loads(post.calls.last.request.content)
            assert body["items"][0]["qty"] == 30
            assert body["schedule_date"] == "2026-11-20"
            assert body["supplier"] == "Supplier X"
            assert body.get("docstatus", 0) == 0
            apply_decision(
                demo.vault, writer, action="approve_proposal", proposal_id=stored.id, user_id="U1"
            )
            assert post.call_count == 1
    finally:
        writer.close()


def _shortlist(first: str = "Supplier X", second: str = "Supplier Y") -> dict[str, Any]:
    return {
        "explanation": f"{first} matches the request more closely than {second}.",
        "options": [
            {"supplier": first, "reason": "Better recorded delivery.", "tradeoff": "Higher price."},
            {"supplier": second, "reason": "Lower historical price.", "tradeoff": "Weaker delivery."},
        ],
    }


def test_a_request_for_options_lists_them_and_does_not_order(demo):
    reply = demo.say(
        "I want ITEM-A. Give me the top options.",
        intent("compare", item="ITEM-A", option_count=2),
        decision=_shortlist(),
    )
    assert "1. Supplier X" in reply.text
    assert "2. Supplier Y" in reply.text
    assert "No proposal created." in reply.text
    assert reply.proposal_id is None and demo.count() == 0


def test_buying_the_cheapest_prepares_that_supplier(demo):
    reply = demo.say(
        "Buy 20 of ITEM-A from the cheapest supplier, needed next Friday.",
        intent(
            "purchase",
            item="ITEM-A",
            qty=20,
            required_by="next Friday",
            objective="cheapest",
            choose_for_me=True,
        ),
        decision=_shortlist("Supplier Y", "Supplier X"),
    )
    stored = demo.vault.get(reply.proposal_id)
    assert stored is not None
    assert stored.payload.supplier == "Supplier Y"
    assert stored.payload.items[0].rate == 8
    assert stored.payload.items[0].qty == 20


def test_choosing_a_listed_option_prepares_that_supplier(demo):
    demo.say(
        "Show me options for ITEM-A.",
        intent("compare", item="ITEM-A", option_count=2),
        decision=_shortlist(),
    )
    reply = demo.say(
        "Choose the second one, 20 units, next Friday.",
        intent("purchase", selection_index=2, qty=20, required_by="next Friday"),
    )
    stored = demo.vault.get(reply.proposal_id)
    assert stored is not None
    assert stored.payload.supplier == "Supplier Y"
    assert stored.payload.items[0].qty == 20


def test_packaging_asks_item_and_quantity_even_after_a_comparison(demo):
    demo.say("Compare ITEM-A", intent("compare", item="ITEM-A"))
    reply = demo.say("Order some packaging", intent("purchase", item="packaging"))
    assert "Which item" in reply.text and "How many units" in reply.text
    assert reply.blocks is None and demo.count() == 0
    filled = demo.say(
        "20 ITEM-A from Supplier X next Friday",
        intent("update", item="ITEM-A", qty=20, supplier="Supplier X", required_by="next Friday"),
    )
    assert filled.proposal_id and demo.count() == 1


def test_questions_never_change_a_pending_proposal(demo):
    prepared = demo.prepare()
    before = demo.vault.get(prepared.proposal_id)
    reply = demo.say("What would 100 cost? Don't order", intent("compare", item="ITEM-A", qty=100))
    after = demo.vault.get(prepared.proposal_id)
    assert reply.proposal_id is None and demo.count() == 1
    assert before.payload_hash == after.payload_hash and after.status == "proposed"


@pytest.mark.parametrize(
    "values, expected",
    [
        ({"qty": None}, "How many units"),
        ({"qty": 0}, "positive quantity"),
        ({"qty": -2}, "positive quantity"),
        ({"supplier": "Unknown"}, "Choose a supplier"),
        ({"supplier": "Supplier"}, "Choose a supplier"),
        ({"required_by": None}, "When do you need"),
        ({"required_by": "2026-11-01"}, "today or later"),
        ({"required_by": "sometime soon"}, "YYYY-MM-DD"),
        ({"item": "UNKNOWN"}, "Which item"),
    ],
)
def test_incomplete_or_invalid_purchase_never_stages(demo, values, expected):
    parsed = (
        intent("purchase", item="ITEM-A", supplier="Supplier X", qty=20, required_by="next Friday")
        | values
    )
    reply = demo.say("A purchase request", parsed)
    assert expected in reply.text
    assert reply.proposal_id is None and demo.count() == 0


def test_invalid_revision_disables_previous_button(demo):
    first = demo.prepare()
    reply = demo.say("Make that zero", intent("update", qty=0))
    assert reply.proposal_id is None
    assert demo.vault.get(first.proposal_id).status == "rejected"


def test_followup_is_scoped_to_conversation(demo):
    demo.prepare()
    reply = demo.say("Make that 30", intent("update", qty=30), conversation_id="another-thread")
    assert "no open purchase" in reply.text and demo.count() == 1


def test_new_order_for_same_item_after_execution(demo):
    class Writer:
        def create_draft(self, order: DraftPurchaseOrder) -> str:
            return "PO-DEMO"

    first = demo.prepare()
    apply_decision(
        demo.vault, Writer(), action="approve_proposal", proposal_id=first.proposal_id, user_id="U1"
    )
    second = demo.prepare()
    assert first.proposal_id != second.proposal_id
    assert demo.vault.get(second.proposal_id).status == "proposed"


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("next Friday", "2026-11-20"),
        ("Friday", "2026-11-13"),
        ("next Monday", "2026-11-16"),
        ("tomorrow", "2026-11-14"),
        ("2026-12-01", "2026-12-01"),
    ],
)
def test_dates_have_a_defined_calendar_interpretation(phrase, expected):
    assert resolve_date(phrase, TODAY).isoformat() == expected


def test_slack_delivery_updates_revised_card_and_threads_new_replies(demo):
    slack = runpy.run_path(str(ROOT / "scripts/slack_listen.py"))

    class Client:
        calls: list[dict[str, Any]] = []

        def chat_postMessage(self, **kwargs):
            self.calls.append(kwargs)
            return {"ts": "111.222"}

        def chat_update(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    prepared = demo.prepare()
    slack["_deliver"](client, demo.vault, "C1", prepared, thread_ts="100.100")
    assert client.calls[-1]["thread_ts"] == "100.100"
    revised = demo.say("Make that 30", intent("update", qty=30))
    slack["_deliver"](client, demo.vault, "C1", revised, thread_ts="100.100")
    assert client.calls[-1]["ts"] == "111.222"
    assert "qty 30" in client.calls[-1]["text"]
    state = Conversation.model_validate_json(demo.vault.conversation("demo"))
    assert state.proposal_id == revised.proposal_id


def test_stale_click_does_not_remove_the_new_card(demo):
    slack = runpy.run_path(str(ROOT / "scripts/slack_listen.py"))
    first = demo.prepare()
    demo.vault.remember_slack(first.proposal_id, "C1", "123.456")
    revised = demo.say("Make that 30 units", intent("update", qty=30))

    class Client:
        def __init__(self):
            self.updated = []
            self.ephemeral = []

        def chat_update(self, **kwargs):
            self.updated.append(kwargs)

        def chat_postEphemeral(self, **kwargs):
            self.ephemeral.append(kwargs)

    class Writer:
        def create_draft(self, order: DraftPurchaseOrder) -> str:
            assert order.items[0].qty == 30
            return "PO-DEMO"

    client = Client()
    body = {
        "actions": [{"value": first.proposal_id}],
        "user": {"id": "U1"},
        "channel": {"id": "C1"},
        "message": {"ts": "123.456"},
    }
    slack["_act"](demo.vault, Writer(), body, client, "approve_proposal")
    assert len(client.ephemeral) == 1 and not client.updated
    body["actions"] = [{"value": revised.proposal_id}]
    slack["_act"](demo.vault, Writer(), body, client, "approve_proposal", "http://localhost:8080")
    assert "http://localhost:8080/app/purchase-order/PO-DEMO" in client.updated[-1]["text"]
    assert client.updated[-1]["blocks"] == []
