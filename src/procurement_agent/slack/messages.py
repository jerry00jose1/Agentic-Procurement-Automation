"""The approval card. Button values are the proposal id only."""

from __future__ import annotations

from typing import Any

from procurement_agent.approvals.service import StoredProposal


def order_card(
    decision: str,
    stored: StoredProposal,
    *,
    notice: str | None = None,
    submit_limit: float = 5000,
) -> tuple[str, list[dict[str, Any]]]:
    """The model's decision, then the exact order the button will file."""
    body = decision.strip()
    if notice:
        body = f"{notice}\n\n{body}" if body else notice
    draft = _draft(stored, submit_limit)
    text = f"{body}\n\n{draft}" if body else draft
    blocks: list[dict[str, Any]] = []
    if body:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": body}})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": draft}})
    actions = _actions(stored, submit_limit)
    if actions is not None:
        blocks.append(actions)
    return text, blocks


def _draft(stored: StoredProposal, submit_limit: float) -> str:
    line = stored.payload.items[0]
    total = stored.payload.line_total()
    currency = stored.payload.currency
    within = total <= submit_limit
    if stored.status == "drafted":
        outcome = (
            f"Draft {stored.erp_document_name} is over {submit_limit:,.0f} {currency}. "
            "A different approver has to submit it."
        )
    elif within:
        outcome = (
            f"This total is within {submit_limit:,.0f} {currency}. "
            "Place order submits it in ERPNext."
        )
    else:
        outcome = (
            f"This total is over {submit_limit:,.0f} {currency}. "
            "Create draft PO saves a draft until a second approver submits it."
        )
    return "\n".join(
        [
            "*Purchase order*",
            outcome,
            stored.payload.supplier,
            f"{line.item_code} · qty {line.qty:g} · {line.rate:g} {currency}",
            f"Required by: {stored.payload.schedule_date.isoformat()}",
            f"Company: {stored.payload.company} · Warehouse: {stored.payload.warehouse}",
            f"Estimated line total: {total:,.2f} {currency} (before taxes and charges)",
            "The price is that supplier's latest order, not a current quotation.",
        ]
    )


def _actions(stored: StoredProposal, submit_limit: float) -> dict[str, Any] | None:
    if stored.status == "drafted":
        buttons = [_button("Submit order", "submit_proposal", stored.id, "primary")]
    elif stored.status == "proposed":
        label = "Place order" if stored.payload.line_total() <= submit_limit else "Create draft PO"
        buttons = [
            _button(label, "approve_proposal", stored.id, "primary"),
            _button("Don't order", "reject_proposal", stored.id, "danger"),
        ]
    else:
        return None
    return {"type": "actions", "block_id": "proposal_actions", "elements": buttons}


def button_values(blocks: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for block in blocks:
        for element in block.get("elements") or []:
            value = element.get("value")
            if isinstance(value, str):
                values.append(value)
    return values


def button_labels(blocks: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for block in blocks:
        for element in block.get("elements") or []:
            text = element.get("text") or {}
            label = text.get("text") if isinstance(text, dict) else None
            if isinstance(label, str):
                labels.append(label)
    return labels


def _button(label: str, action_id: str, proposal_id: str, style: str) -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "action_id": action_id,
        "style": style,
        "value": proposal_id,
    }
