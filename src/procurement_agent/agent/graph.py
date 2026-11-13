"""One LangGraph agent. Its tools can read a prepared case and file that case."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI

from procurement_agent.agent.case import ReplacementCase
from procurement_agent.agent.tools import make_proposal_tools
from procurement_agent.approvals.service import ApprovalVault
from procurement_agent.config import Settings

SYSTEM_PROMPT = """You prepare one replacement purchase order for a buyer.
Call find_replacement_case first.
Then call stage_replacement with that exact case_id.
The supplier, item, quantity, and price in the case are fixed.
You cannot approve the proposal, and you cannot create a purchase order in ERPNext.
After the proposal is filed, tell the buyer who was late, who should replace them,
the item, the quantity, the price, and the proposal id.
"""


def build_proposal_agent(
    model: BaseChatModel,
    vault: ApprovalVault,
    case: ReplacementCase,
) -> Any:
    tools = make_proposal_tools(case, vault)
    return create_agent(model, tools, system_prompt=SYSTEM_PROMPT)


def openai_chat_model(settings: Settings) -> ChatOpenAI:
    if settings.llm_provider != "openai":
        raise RuntimeError(f"LLM_PROVIDER is {settings.llm_provider}. This agent uses OpenAI.")
    key = settings.openai_api_key
    if key is None or not key.get_secret_value().strip():
        raise RuntimeError("OPENAI_API_KEY is empty.")
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=key,
        temperature=0,
        timeout=30,
        max_retries=1,
    )


def proposal_request() -> dict[str, list[HumanMessage]]:
    return {
        "messages": [
            HumanMessage(
                content="Prepare one replacement purchase order for the worst late supplier."
            )
        ]
    }


def final_text(result: dict[str, object]) -> str:
    messages = result.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        if message.tool_calls:
            continue
        text = _content_text(message)
        if text:
            return text
    return ""


def _content_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()
