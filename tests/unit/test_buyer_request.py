from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from procurement_agent.agent.buyer_request import (
    OpenAIRequestReader,
    RequestReadError,
    accept_buyer_request,
    mentioned_item_code,
)

SUPPLIERS = {"Jebel Enterprises LLC 120", "Oasis Industries LLC 107"}
CODES = {"S-66100", "S-66100-B"}


class _Says:
    def __init__(self, content: str) -> None:
        self._content = content
        self.messages: list[object] = []

    def invoke(self, messages: list[object]) -> AIMessage:
        self.messages = messages
        return AIMessage(content=self._content)


def test_named_text_finds_the_longest_item_code() -> None:
    assert mentioned_item_code("I need S-66100-B please", CODES) == "S-66100-B"
    assert mentioned_item_code("hello", CODES) is None


def test_a_short_supplier_name_resolves_to_the_record() -> None:
    raw = (
        '{"item_code":"S-66100","objective":"on_time","exclude_suppliers":["Jebel"],'
        '"named_supplier":null,"qty":null}'
    )
    request = accept_buyer_request(raw, "not from Jebel", CODES, SUPPLIERS)
    assert request.item_code == "S-66100"
    assert request.exclude == ("Jebel Enterprises LLC 120",)
    assert request.objective == "on_time"
    assert request.qty is None


def test_the_item_code_in_the_sentence_wins_over_the_model() -> None:
    raw = (
        '{"item_code":"S-66100","objective":"lowest_price","exclude_suppliers":[],'
        '"named_supplier":null,"qty":10}'
    )
    request = accept_buyer_request(
        raw,
        "I need 10 of S-66100-B, the cheaper one",
        CODES,
        SUPPLIERS,
    )
    assert request.item_code == "S-66100-B"
    assert request.objective == "lowest_price"
    assert request.qty == 10


def test_an_unknown_supplier_is_refused() -> None:
    raw = (
        '{"item_code":"S-66100","objective":"on_time","exclude_suppliers":["Acme"],'
        '"named_supplier":null,"qty":null}'
    )
    try:
        accept_buyer_request(raw, "not from Acme", CODES, SUPPLIERS)
    except RequestReadError as exc:
        assert "Acme" in exc.message
    else:
        raise AssertionError("expected a refusal")


def test_the_reader_sends_the_catalog_and_the_sentence() -> None:
    model = _Says(
        '{"item_code": null, "objective": "named_supplier", "exclude_suppliers": [], '
        '"named_supplier": "Oasis", "qty": null}'
    )
    reader = OpenAIRequestReader(model)
    request = reader.read("use Oasis for S-66100", CODES, SUPPLIERS)
    assert request.named_supplier == "Oasis Industries LLC 107"
    assert request.objective == "named_supplier"
    assert request.item_code == "S-66100"
    assert isinstance(model.messages[0], SystemMessage)
    assert isinstance(model.messages[1], HumanMessage)
    human = str(model.messages[1].content)
    assert "Jebel Enterprises LLC 120" in human
    assert "use Oasis for S-66100" in human
