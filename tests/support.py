"""Single-tool research driver for existing component regression tests.

The graph-specific tests independently script multi-step, result-dependent tool calls.
"""

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableLambda


def component_research_model() -> Runnable[Any, AIMessage]:
    def respond(messages: list[Any]) -> AIMessage:
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content="Evidence collected.")
        request = next(
            json.loads(message.content) for message in messages if isinstance(message, HumanMessage)
        )
        intent = request["intent"]
        action = intent["action"]
        if action in {"compare", "explain"}:
            name = "compare_suppliers"
            args = {
                "item_code": intent["item"] or "",
                "objective": intent["objective"],
                "preferences": intent["preferences"],
                "count": intent["option_count"],
                "excluded_suppliers": intent["excluded_suppliers"],
                "explain_previous": action == "explain",
            }
        elif action == "policy":
            name, args = "search_policy", {"query": request["request"]}
        else:
            name, args = "check_invoices", {}
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "read-1"}])

    return RunnableLambda(respond)
