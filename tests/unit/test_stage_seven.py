import math
import re
from datetime import date
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.runnables import Runnable, RunnableLambda

from procurement_agent.approvals.orders import DraftPurchaseOrder, PurchaseOrderLine
from procurement_agent.approvals.service import ApprovalVault, init_database
from procurement_agent.policy.retrieve import retrieve
from procurement_agent.slack.item_reply import reply_to_text

TODAY = date(2026, 11, 13)


class TokenEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_vector(text) for text in texts]


class Scripted(GenericFakeChatModel):
    replies: list[dict[str, Any]] = []

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        assert kwargs == {"method": "json_schema", "strict": True}
        raw = self.replies.pop(0)
        return RunnableLambda(lambda _message: schema.model_validate(raw))


def _vector(text: str) -> list[float]:
    values = [0.0] * 64
    for token in re.findall(r"[a-z0-9]+", text.casefold()):
        values[hash(token) % 64] += 1.0
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def _intent(action: str) -> dict[str, Any]:
    return {
        "action": action,
        "item": None,
        "supplier": None,
        "qty": None,
        "required_by": None,
    }


def _ask(
    vault: ApprovalVault,
    action: str,
    answer: dict[str, Any],
    question: str = "A stage seven question",
    **extra: Any,
):
    model = Scripted(messages=iter([]))
    model.replies = [_intent(action), answer]
    return reply_to_text(
        vault,
        question,
        [],
        extra.get("order_items", []),
        [],
        [],
        model,
        today=TODAY,
        invoices=extra.get("invoices"),
        invoice_items=extra.get("invoice_items"),
        embedder=TokenEmbedder(),
    )


def test_policy_question_cites_a_real_section_and_does_not_order(tmp_path):
    found = retrieve(
        "Does the billed quantity have to match the quantity received?",
        TokenEmbedder(),
    )
    assert found[0].id == "invoice-match"
    vault = ApprovalVault(init_database(f"sqlite:///{tmp_path}/policy.sqlite"), {"U1"})
    stored = vault.stage(
        "open",
        DraftPurchaseOrder(
            supplier="Supplier X",
            company="Northwind Components",
            warehouse="Stores - NC",
            transaction_date=TODAY,
            schedule_date=TODAY,
            items=[PurchaseOrderLine(item_code="ITEM-A", qty=1, rate=2)],
        ),
    )
    reply = _ask(
        vault,
        "policy",
        {
            "explanation": "The billed quantity must match the quantity received.",
            "citation_ids": ["invoice-match"],
        },
        question="Does the billed quantity have to match the quantity received?",
    )
    assert "invoice-match" in reply.text
    assert "No purchase was created." in reply.text
    assert reply.proposal_id is None
    assert vault.get(stored.id).status == "proposed"


def test_invented_policy_citation_is_refused(tmp_path):
    vault = ApprovalVault(init_database(f"sqlite:///{tmp_path}/policy.sqlite"), {"U1"})
    reply = _ask(vault, "policy", {"explanation": "Anything.", "citation_ids": ["made-up"]})
    assert "do not support a cited answer" in reply.text
    assert "made-up" not in reply.text
    assert reply.blocks is None


def test_invoice_question_uses_calculated_figures(tmp_path):
    vault = ApprovalVault(init_database(f"sqlite:///{tmp_path}/invoice.sqlite"), {"U1"})
    reply = _ask(
        vault,
        "invoice",
        {
            "explanation": "PINV-1 bills fewer units than were received.",
            "invoice_names": ["PINV-1"],
        },
        invoices=[{"name": "PINV-1", "supplier": "Supplier A"}],
        invoice_items=[
            {
                "parent": "PINV-1",
                "item_code": "ITEM-A",
                "qty": 8,
                "rate": 12,
                "purchase_order": "PO-1",
                "po_detail": "line-1",
            }
        ],
        order_items=[{"name": "line-1", "received_qty": 10, "rate": 10}],
    )
    assert "billed quantity 8 against received 10" in reply.text
    assert "billed price 12 against order price 10" in reply.text
    assert "PINV-1 bills fewer units" in reply.text
    assert reply.proposal_id is None


def test_invented_invoice_name_does_not_replace_the_rows(tmp_path):
    vault = ApprovalVault(init_database(f"sqlite:///{tmp_path}/invoice.sqlite"), {"U1"})
    reply = _ask(
        vault,
        "invoice",
        {"explanation": "PINV-999 is the problem.", "invoice_names": ["PINV-999"]},
        invoices=[{"name": "PINV-1", "supplier": "Supplier A"}],
        invoice_items=[
            {
                "parent": "PINV-1",
                "item_code": "ITEM-A",
                "qty": 8,
                "rate": 12,
                "purchase_order": "PO-1",
                "po_detail": "line-1",
            }
        ],
        order_items=[{"name": "line-1", "received_qty": 10, "rate": 10}],
    )
    assert "could not check the commentary" in reply.text
    assert "PINV-999" not in reply.text
    assert "billed quantity 8 against received 10" in reply.text


def test_an_unrecognised_question_lists_what_it_can_answer(tmp_path):
    vault = ApprovalVault(init_database(f"sqlite:///{tmp_path}/clarify.sqlite"), {"U1"})
    model = Scripted(messages=iter([]))
    model.replies = [_intent("clarify")]
    reply = reply_to_text(vault, "Who is the prime minister?", [], [], [], [], model, today=TODAY)
    assert "I couldn't answer that" in reply.text
    assert "prepare one purchase" in reply.text
    assert "purchasing policy" in reply.text
    assert "do not match" in reply.text
    assert reply.blocks is None and reply.proposal_id is None


def test_matching_invoices_need_no_model_commentary(tmp_path):
    vault = ApprovalVault(init_database(f"sqlite:///{tmp_path}/invoice.sqlite"), {"U1"})
    model = Scripted(messages=iter([]))
    model.replies = [_intent("invoice")]
    reply = reply_to_text(
        vault,
        "Any invoice problems?",
        [],
        [{"name": "line-1", "received_qty": 8, "rate": 12}],
        [],
        [],
        model,
        today=TODAY,
        invoices=[{"name": "PINV-1", "supplier": "Supplier A"}],
        invoice_items=[
            {
                "parent": "PINV-1",
                "item_code": "ITEM-A",
                "qty": 8,
                "rate": 12,
                "purchase_order": "PO-1",
                "po_detail": "line-1",
            }
        ],
        embedder=TokenEmbedder(),
    )
    assert "match the received quantities" in reply.text
    assert model.replies == []
    assert reply.proposal_id is None
