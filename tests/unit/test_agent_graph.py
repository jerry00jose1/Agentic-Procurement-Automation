from datetime import date
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from procurement_agent.agent.case import ReplacementCase
from procurement_agent.agent.graph import build_proposal_agent, final_text, proposal_request
from procurement_agent.agent.tools import make_proposal_tools
from procurement_agent.approvals.models import Base
from procurement_agent.approvals.orders import DraftPurchaseOrder, PurchaseOrderLine
from procurement_agent.approvals.service import ApprovalVault


def _vault() -> ApprovalVault:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    return ApprovalVault(sessions, {"U-APPROVER"})


def _case() -> ReplacementCase:
    order = DraftPurchaseOrder(
        supplier="Good Co",
        company="Northwind Components",
        warehouse="Stores - NC",
        transaction_date=date(2026, 11, 12),
        schedule_date=date(2026, 11, 26),
        items=[PurchaseOrderLine(item_code="ITEM-A", qty=12, rate=7)],
    )
    return ReplacementCase(
        case_id="replacement:LINE-5",
        late_supplier="Late Co",
        late_lines=5,
        scored_lines=5,
        on_time_rate=0.0,
        avg_late_days=4.0,
        alternative_supplier="Good Co",
        alternative_on_time_rate=1.0,
        source_order="PO-5",
        source_line="LINE-5",
        order=order,
    )


def test_tools_can_only_find_and_stage() -> None:
    case = _case()
    tools = make_proposal_tools(case, _vault())
    assert [tool.name for tool in tools] == ["find_replacement_case", "stage_replacement"]


def test_unknown_case_does_not_file_a_proposal() -> None:
    case = _case()
    vault = _vault()
    stage = make_proposal_tools(case, vault)[1]
    result = stage.invoke({"case_id": "replacement:made-up"})
    assert "Unknown case_id" in result
    assert vault.get_by_key(case.case_id) is None


class _ScriptedModel(GenericFakeChatModel):
    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        del tools, tool_choice, kwargs
        return self


def test_agent_files_the_prepared_order_and_stops_there() -> None:
    case = _case()
    vault = _vault()
    model = _ScriptedModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "find_replacement_case",
                            "args": {},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "stage_replacement",
                            "args": {"case_id": case.case_id},
                            "id": "call-2",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="Filed the replacement for the buyer."),
            ]
        )
    )
    agent = build_proposal_agent(model, vault, case)
    result = agent.invoke(proposal_request())
    stored = vault.get_by_key(case.case_id)
    assert stored is not None
    assert stored.status == "proposed"
    assert stored.payload.supplier == "Good Co"
    assert stored.payload.items[0].qty == 12
    assert stored.payload.items[0].rate == 7
    assert stored.erp_document_name is None
    assert final_text(result) == "Filed the replacement for the buyer."

    again = vault.stage(case.case_id, case.order)
    assert again.id == stored.id
