"""The approval card. Button values are the proposal id only."""

from __future__ import annotations

from typing import Any

from procurement_agent.approvals.service import StoredProposal


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
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        }
    ]
    if stored.status == "proposed":
        blocks.append(
            {
                "type": "actions",
                "block_id": "proposal_actions",
                "elements": [
                    _button("Approve", "approve_proposal", stored.id, "primary"),
                    _button("Reject", "reject_proposal", stored.id, "danger"),
                ],
            }
        )
    return text, blocks


def button_values(blocks: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for block in blocks:
        for element in block.get("elements") or []:
            value = element.get("value")
            if isinstance(value, str):
                values.append(value)
    return values


def _button(label: str, action_id: str, proposal_id: str, style: str) -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": label},
        "action_id": action_id,
        "style": style,
        "value": proposal_id,
    }
