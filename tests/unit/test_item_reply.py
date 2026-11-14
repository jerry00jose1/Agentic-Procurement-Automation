import json
from datetime import date
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from procurement_agent.approvals.models import Base
from procurement_agent.approvals.service import ApprovalVault
from procurement_agent.slack.item_reply import reply_to_text
from procurement_agent.slack.messages import button_labels, button_values

TODAY = date(2026, 11, 12)


class _ScriptedModel(GenericFakeChatModel):
    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        return self | RunnableLambda(lambda message: schema.model_validate_json(message.content))


def _vault() -> ApprovalVault:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return ApprovalVault(sessionmaker(bind=engine, expire_on_commit=False), set())


def _records() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    orders = [
        {
            "name": "PO-1",
            "supplier": "Good Co",
            "transaction_date": "2024-05-01",
            "company": "Northwind Components",
        },
        {
            "name": "PO-2",
            "supplier": "Late Co",
            "transaction_date": "2024-03-01",
            "company": "Northwind Components",
        },
    ]
    items = [
        {
            "name": "L1",
            "parent": "PO-1",
            "item_code": "ITEM-A",
            "qty": 8,
            "rate": 7,
            "warehouse": "Stores - NC",
        },
        {
            "name": "L2",
            "parent": "PO-2",
            "item_code": "ITEM-A",
            "qty": 12,
            "rate": 5,
            "warehouse": "Stores - NC",
        },
    ]
    return orders, items


def test_the_model_reply_is_the_decision_and_the_draft_is_the_recorded_order() -> None:
    orders, items = _records()
    model = _ScriptedModel(
        messages=iter(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "action": "purchase",
                            "item": "ITEM-A",
                            "supplier": "Good Co",
                            "qty": 8,
                            "required_by": "next Friday",
                        }
                    )
                ),
            ]
        )
    )
    vault = _vault()
    answer = reply_to_text(vault, "I need ITEM-A", orders, items, [], [], model, today=TODAY)
    assert "Prepared 8 units of ITEM-A" in answer.text
    assert "Good Co" in answer.text
    assert "ITEM-A · qty 8 · 7 EUR" in answer.text
    assert answer.blocks is not None
    assert answer.proposal_id is not None
    stored = vault.get(answer.proposal_id)
    assert stored is not None
    assert answer.proposal_id == stored.id
    assert button_values(answer.blocks) == [stored.id, stored.id]
    assert button_labels(answer.blocks) == ["Create draft PO", "Don't order"]


def test_a_reply_without_an_order_has_no_buttons() -> None:
    orders, items = _records()
    model = _ScriptedModel(
        messages=iter(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "action": "clarify",
                            "item": None,
                            "supplier": None,
                            "qty": None,
                            "required_by": None,
                        }
                    )
                )
            ]
        )
    )
    answer = reply_to_text(_vault(), "hello", orders, items, [], [], model, today=TODAY)
    assert answer.blocks is None
    assert answer.proposal_id is None
    assert "prepare one purchase" in answer.text
