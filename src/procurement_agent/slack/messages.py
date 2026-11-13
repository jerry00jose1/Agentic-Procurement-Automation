"""The approval card. Button values are the proposal id only."""

from __future__ import annotations

from typing import Any

from procurement_agent.approvals.service import StoredProposal


def order_card(
    decision: str,
    stored: StoredProposal,
    *,
    notice: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """The model's decision, then the exact draft the button will create."""
    body = decision.strip()
    if notice:
        body = f"{notice}\n\n{body}" if body else notice
    draft = _draft(stored)
    text = f"{body}\n\n{draft}" if body else draft
    blocks: list[dict[str, Any]] = []
    if body:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": body}})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": draft}})
    if stored.status == "proposed":
        blocks.append(_actions(stored.id))
    return text, blocks


def proposal_message(
    stored: StoredProposal,
    *,
    late_supplier: str,
) -> tuple[str, list[dict[str, Any]]]:
    line = stored.payload.items[0]
    text = (
        f"Replace {late_supplier} with {stored.payload.supplier}: "
        f"{line.item_code}, qty {line.qty:g}, rate {line.rate:g} {stored.payload.currency}."
    )
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}}
    ]
    if stored.status == "proposed":
        blocks.append(_actions(stored.id))
    return text, blocks


def _draft(stored: StoredProposal) -> str:
    line = stored.payload.items[0]
    return "\n".join(
        [
            "*Draft purchase order*",
            "Create draft PO writes this into ERPNext as a draft. It is not submitted.",
            stored.payload.supplier,
            f"{line.item_code} · qty {line.qty:g} · {line.rate:g} {stored.payload.currency}",
            "The price is that supplier's latest order.",
        ]
    )


def _actions(proposal_id: str) -> dict[str, Any]:
    return {
        "type": "actions",
        "block_id": "proposal_actions",
        "elements": [
            _button("Create draft PO", "approve_proposal", proposal_id, "primary"),
            _button("Don't order", "reject_proposal", proposal_id, "danger"),
        ],
    }


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
