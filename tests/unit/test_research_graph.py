"""Exercise result-dependent tool selection and the graph's write/iteration boundaries."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableLambda
from sqlalchemy import func, select

from procurement_agent.agent.graph import MAX_ROUNDS, MAX_TOOL_CALLS, build_buyer_graph
from procurement_agent.approvals.models import ProposalRow
from procurement_agent.approvals.service import ApprovalVault, init_database

TODAY = date(2026, 11, 14)


def call(name: str, args: dict[str, Any], ident: str = "read") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": ident}])


class FlatEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class ResearchModel(GenericFakeChatModel):
    structured: list[dict[str, Any]] = []
    steps: list[AIMessage] = []
    observed: list[list[Any]] = []
    adaptive: bool = False

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        raw = self.structured.pop(0)
        return RunnableLambda(lambda _messages: schema.model_validate(raw))

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable[Any, AIMessage]:
        names = {entry.name for entry in tools}
        assert names == {
            "lookup_sellers",
            "search_policy",
            "check_invoices",
            "get_purchase_order",
            "compare_suppliers",
        }
        assert not names.intersection({"approve", "submit", "prepare_purchase", "execute"})

        def respond(messages: list[Any]) -> AIMessage:
            self.observed.append(list(messages))
            if self.adaptive:
                count = len(self.observed)
                if count == 1:
                    return call("check_invoices", {})
                assert isinstance(messages[-1], ToolMessage)
                payload = json.loads(messages[-1].content)
                if count == 2:
                    # Choose the next tool and its argument from the preceding result.
                    po = payload["rows"][0]["purchase_order"]
                    assert po == "PO-X2"
                    return call("get_purchase_order", {"name": po}, "order")
                if count == 3:
                    assert payload["order"]["supplier"] == "Supplier X"
                    return call(
                        "search_policy", {"query": "invoice quantity and price match"}, "policy"
                    )
                assert "invoice-match" in {row["id"] for row in payload}
                return AIMessage(content="The evidence is sufficient.")
            return self.steps.pop(0)

        return RunnableLambda(respond)


def root(action: str = "invoice", **extra: Any) -> dict[str, Any]:
    return dict(action=action, item=None, supplier=None, qty=None, required_by=None) | extra


@pytest.fixture
def world(tmp_path):
    sessions = init_database(f"sqlite:///{tmp_path}/vault.sqlite")
    vault = ApprovalVault(sessions, {"BUYER"})
    path = Path(__file__).resolve().parents[1] / "fixtures/demo_procurement.json"
    records = json.loads(path.read_text())
    records["invoices"] = [{"name": "INV-1", "supplier": "Supplier X", "docstatus": 1}]
    records["invoice_items"] = [
        {
            "parent": "INV-1",
            "item_code": "ITEM-A",
            "qty": 2,
            "rate": 12,
            "po_detail": "LX2",
            "purchase_order": "PO-X2",
        }
    ]

    def graph(model):
        return build_buyer_graph(
            model,
            vault,
            **records,
            today=TODAY,
            conversation_id="research",
            embedder=FlatEmbedder(),
        )

    return vault, sessions, graph


def test_combined_investigation_chooses_next_tool_from_actual_results(world):
    vault, sessions, graph = world
    model = ResearchModel(
        messages=iter([]),
        adaptive=True,
        structured=[
            root(),
            {
                "explanation": "INV-1 differs from PO-X2. Review it under invoice-match.",
                "citation_ids": ["invoice-match"],
                "invoice_names": ["INV-1"],
                "purchase_orders": ["PO-X2"],
            },
        ],
    )
    result = graph(model).invoke(
        {"text": "Check invoice exceptions against our purchasing policy."}
    )
    text, filed = result["result"]
    assert filed is None
    assert "invoice-match" in text and "billed price 12 against order price 10" in text
    assert result["rounds"] == 4 and result["tool_calls"] == 3
    assert len(model.observed) == 4
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProposalRow)) == 0
    assert vault.conversation("research") is None


def test_cannot_invent_source_identifiers_in_combined_answer(world):
    _, _, graph = world
    model = ResearchModel(
        messages=iter([]),
        structured=[
            root(),
            {
                "explanation": "Made-up invoice is wrong.",
                "citation_ids": ["fake-policy"],
                "invoice_names": ["INVENTED"],
            },
        ],
        steps=[call("search_policy", {"query": "approval"}), AIMessage(content="Done")],
    )
    text, filed = graph(model).invoke({"text": "Check the policy"})["result"]
    assert "do not support a cited answer" in text
    assert "fake-policy" not in text and "INVENTED" not in text and filed is None


def test_unknown_write_tool_is_rejected_and_model_can_recover(world):
    _, sessions, graph = world
    model = ResearchModel(
        messages=iter([]),
        structured=[root()],
        steps=[
            call("submit", {"name": "PO-X2"}),
            call("check_invoices", {"invoice_name": "UNKNOWN"}),
            AIMessage(content="No matching evidence"),
        ],
    )
    result = graph(model).invoke({"text": "Inspect invoice exceptions"})
    errors = [message for message in model.observed[1] if isinstance(message, ToolMessage)]
    assert "disallowed" in errors[0].content
    assert "No invoice exceptions" in result["result"][0]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProposalRow)) == 0


def test_repeated_tool_loop_stops_at_reasoning_budget(world):
    _, _, graph = world
    model = ResearchModel(
        messages=iter([]),
        structured=[root()],
        steps=[call("get_purchase_order", {"name": "UNKNOWN"}, str(i)) for i in range(MAX_ROUNDS)],
    )
    result = graph(model).invoke({"text": "Investigate this"})
    assert result["rounds"] == MAX_ROUNDS
    assert "investigation limit" in result["result"][0]
    assert len(model.observed) == MAX_ROUNDS


def test_parallel_tool_batch_is_capped_before_more_tools_execute(world):
    _, _, graph = world
    batch = AIMessage(
        content="",
        tool_calls=[
            {"name": "get_purchase_order", "args": {"name": "UNKNOWN"}, "id": str(i)}
            for i in range(MAX_TOOL_CALLS + 3)
        ],
    )
    model = ResearchModel(messages=iter([]), structured=[root()], steps=[batch])
    result = graph(model).invoke({"text": "Investigate this"})
    assert result["tool_calls"] == MAX_TOOL_CALLS
    assert len(model.observed) == 1 and result["exhausted"]


def test_invalid_tool_arguments_return_to_model_for_correction(world):
    _, _, graph = world
    model = ResearchModel(
        messages=iter([]),
        structured=[root()],
        steps=[
            call("get_purchase_order", {}),
            call("check_invoices", {"invoice_name": "UNKNOWN"}),
            AIMessage(content="Done"),
        ],
    )
    result = graph(model).invoke({"text": "Check an invoice"})
    assert result["tool_calls"] == 2
    assert "ValidationError" in model.observed[1][-1].content


def test_compile_has_real_conditional_loop_and_purchase_branch(world):
    _, _, graph = world
    model = ResearchModel(messages=iter([]), structured=[root("clarify")])
    compiled = graph(model)
    edges = {(edge.source, edge.target) for edge in compiled.get_graph().edges}
    assert {
        ("reason", "tools"),
        ("tools", "reason"),
        ("interpret", "prepare"),
        ("finish", "__end__"),
    }.issubset(edges)
    assert compiled.invoke({"text": "Hello"})["result"][1] is None


def test_tool_cannot_drop_explicit_supplier_exclusions(world):
    vault, _, graph = world
    model = ResearchModel(
        messages=iter([]),
        structured=[
            root("compare", item="ITEM-A", excluded_suppliers=["Supplier X"], objective="cheapest"),
            {
                "explanation": "Y is the cheapest eligible historical option.",
                "options": [
                    {
                        "supplier": "Supplier Y",
                        "reason": "Lowest remaining historical price.",
                        "tradeoff": "Historical price only.",
                    }
                ],
            },
        ],
        steps=[
            call("compare_suppliers", {"item_code": "ITEM-A", "excluded_suppliers": []}),
            AIMessage(content="Done"),
        ],
    )
    text, filed = graph(model).invoke({"text": "Cheapest ITEM-A options, excluding Supplier X"})[
        "result"
    ]
    assert "Supplier Y" in text and "Supplier X" not in text and filed is None
    state = json.loads(vault.conversation("research"))
    assert state["excluded_suppliers"] == ["Supplier X"]
    assert state["objective"] == "cheapest"


def test_compiled_graph_does_not_leak_evidence_into_next_invocation(world):
    _, _, graph = world
    model = ResearchModel(
        messages=iter([]),
        structured=[root(), root()],
        steps=[
            call("check_invoices", {"invoice_name": "UNKNOWN"}),
            AIMessage(content="Done"),
            AIMessage(content="No evidence retrieved this time"),
        ],
    )
    compiled = graph(model)
    first = compiled.invoke({"text": "Check an unknown invoice"})
    second = compiled.invoke({"text": "Check another invoice"})
    assert "No invoice exceptions" in first["result"][0]
    assert "could not find supporting evidence" in second["result"][0]
