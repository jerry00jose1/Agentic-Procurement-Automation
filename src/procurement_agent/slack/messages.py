"""The approval card. Button values are the proposal id only."""

from __future__ import annotations

from typing import Any

from procurement_agent.agent.item_request import ConsideredSupplier
from procurement_agent.approvals.service import StoredProposal


def purchase_message(
    stored: StoredProposal,
    considered: tuple[ConsideredSupplier, ...],
) -> tuple[str, list[dict[str, Any]]]:
    text = _purchase_text(stored, considered)
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}}
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


def _purchase_text(
    stored: StoredProposal,
    considered: tuple[ConsideredSupplier, ...],
) -> str:
    line = stored.payload.items[0]
    chosen = stored.payload.supplier
    count = len(considered)
    noun = "supplier has" if count == 1 else "suppliers have"
    lines = [
        (
            f"Buy {line.item_code} from {chosen}: "
            f"qty {line.qty:g}, rate {line.rate:g} {stored.payload.currency}."
        ),
        "",
        f"{count} {noun} sold {line.item_code}.",
        _why(chosen, considered),
        "The quantity and price are that supplier's latest order.",
        "",
    ]
    for row in considered:
        lines.append(f"• {_supplier_line(row, chosen)}")
    return "\n".join(lines)


def _why(chosen: str, considered: tuple[ConsideredSupplier, ...]) -> str:
    winner = next(row for row in considered if row.supplier == chosen)
    if winner.eligible and winner.on_time_rate is not None:
        return (
            f"{chosen} is on time for {winner.on_time_rate:.0%} of "
            f"{winner.scored_lines} deliveries, the best record with at least 5."
        )
    if len(considered) == 1:
        return f"{chosen} is the only supplier on record for this item."
    return f"Nobody has 5 scored deliveries. {chosen} is the most recent seller."


def _supplier_line(row: ConsideredSupplier, chosen: str) -> str:
    if row.scored_lines == 0 or row.on_time_rate is None:
        record = "no scored deliveries"
    else:
        record = f"on time {row.on_time_rate:.0%} of {row.scored_lines}"
    if row.supplier == chosen:
        note = "chosen"
    elif row.eligible:
        note = "lower on-time rate"
    else:
        note = "fewer than 5 deliveries"
    return f"{row.supplier}: {record}, latest rate {row.rate:g} ({note})"


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
