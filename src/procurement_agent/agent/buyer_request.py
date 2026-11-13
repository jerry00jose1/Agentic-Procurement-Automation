"""Read a buyer's sentence into a request. Prices are not part of it."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

Objective = Literal["on_time", "lowest_price", "named_supplier"]

_SYSTEM = (
    "You turn one purchasing message into JSON. "
    "You do not choose a price or a quantity the buyer did not state.\n"
    "Use only item codes and supplier names from the lists in the user message.\n"
    "objective is on_time unless the buyer asks for a cheaper or lowest price, "
    "or names the supplier to buy from.\n"
    "When they say not from a supplier, put that supplier's exact name in exclude_suppliers.\n"
    "When they tell you to use a specific supplier, set named_supplier to that exact name "
    "and objective to named_supplier.\n"
    "qty is the number they asked to buy, or null to keep the last order quantity.\n"
    "item_code is null when they did not ask for a listed item.\n"
    "Reply with JSON only, in this shape:\n"
    '{"item_code": string or null, "objective": "on_time" or "lowest_price" or "named_supplier", '
    '"exclude_suppliers": [string], "named_supplier": string or null, "qty": number or null}\n'
)


class RequestReadError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class BuyerRequest:
    item_code: str | None
    objective: Objective
    exclude: tuple[str, ...]
    named_supplier: str | None
    qty: float | None


def default_request(item_code: str) -> BuyerRequest:
    return BuyerRequest(item_code, "on_time", (), None, None)


def mentioned_item_code(text: str, known_codes: set[str]) -> str | None:
    """Return the longest known item code that appears in the message."""
    folded = text.casefold()
    found: str | None = None
    for code in known_codes:
        if code and code.casefold() in folded and (found is None or len(code) > len(found)):
            found = code
    return found


class _Completes(Protocol):
    def invoke(self, messages: list[BaseMessage]) -> BaseMessage:
        """Return the model message for this prompt."""


class OpenAIRequestReader:
    """Ask the configured model to read one Slack sentence."""

    def __init__(self, model: _Completes) -> None:
        self._model = model

    def read(self, text: str, item_codes: set[str], suppliers: set[str]) -> BuyerRequest:
        listing = _catalog(item_codes, suppliers)
        try:
            message = self._model.invoke(
                [
                    SystemMessage(content=_SYSTEM),
                    HumanMessage(content=f"{listing}\n\nMessage:\n{text}"),
                ]
            )
        except RequestReadError:
            raise
        except Exception as exc:
            raise RequestReadError("I could not read that request.") from exc
        return accept_buyer_request(_content_text(message), text, item_codes, suppliers)


def accept_buyer_request(
    raw: str,
    text: str,
    item_codes: set[str],
    suppliers: set[str],
) -> BuyerRequest:
    """Validate the model's JSON against the item codes and supplier names."""
    payload = _payload(raw)
    objective = _objective(payload.get("objective"))
    exclude = _exclude(payload.get("exclude_suppliers"), suppliers)
    named = _named(payload.get("named_supplier"), suppliers)
    if named is not None:
        objective = "named_supplier"
    elif objective == "named_supplier":
        raise RequestReadError("I could not tell which supplier you want.")
    if named is not None and named in exclude:
        raise RequestReadError(f"You asked for {named} and also excluded them.")
    item_code = _item_code(payload.get("item_code"), item_codes)
    spoken = mentioned_item_code(text, item_codes)
    if spoken is not None:
        item_code = spoken
    return BuyerRequest(item_code, objective, tuple(exclude), named, _qty(payload.get("qty")))


def _objective(raw: object) -> Objective:
    if raw == "on_time" or raw == "lowest_price" or raw == "named_supplier":
        return raw
    raise RequestReadError("I could not read that request.")


def _catalog(item_codes: set[str], suppliers: set[str]) -> str:
    items = "\n".join(sorted(code for code in item_codes if code))
    names = "\n".join(sorted(name for name in suppliers if name))
    return f"Item codes:\n{items}\nSuppliers:\n{names}"


def _payload(raw: str) -> dict[str, object]:
    try:
        payload = json.loads(_json_text(raw))
    except json.JSONDecodeError as exc:
        raise RequestReadError("I could not read that request.") from exc
    if not isinstance(payload, dict):
        raise RequestReadError("I could not read that request.")
    return payload


def _json_text(raw: str) -> str:
    stripped = raw.strip()
    if not stripped.startswith("```"):
        return stripped
    stripped = stripped.split("\n", 1)[-1]
    fence = stripped.rfind("```")
    if fence >= 0:
        stripped = stripped[:fence]
    return stripped.strip()


def _exclude(raw: object, suppliers: set[str]) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RequestReadError("I could not read that request.")
    names: list[str] = []
    for label in raw:
        if not isinstance(label, str) or not label.strip():
            continue
        resolved = resolve_supplier(label, suppliers)
        if resolved not in names:
            names.append(resolved)
    return names


def _named(raw: object, suppliers: set[str]) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    return resolve_supplier(raw, suppliers)


def _item_code(raw: object, item_codes: set[str]) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    folded = raw.casefold()
    matches = [code for code in item_codes if code.casefold() == folded]
    if len(matches) == 1:
        return matches[0]
    return None


def _qty(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise RequestReadError("I could not read that quantity.")
    qty = float(raw)
    if qty <= 0:
        raise RequestReadError("The quantity has to be greater than zero.")
    return qty


def resolve_supplier(label: str, suppliers: set[str]) -> str:
    folded = label.casefold().strip()
    exact = [name for name in suppliers if name.casefold() == folded]
    if len(exact) == 1:
        return exact[0]
    partial = [name for name in suppliers if folded in name.casefold()]
    if len(partial) == 1:
        return partial[0]
    if not exact and not partial:
        raise RequestReadError(f"I could not find a supplier matching {label}.")
    raise RequestReadError(f"I could not tell which supplier {label} means.")


def _content_text(message: BaseMessage) -> str:
    if not isinstance(message, AIMessage):
        raise RequestReadError("I could not read that request.")
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
