"""The chat model used by the Slack buyer."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from procurement_agent.config import Settings


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
