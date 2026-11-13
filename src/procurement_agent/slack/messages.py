"""The approval card. Button values are the proposal id only."""

from __future__ import annotations

from typing import Any

from procurement_agent.agent.item_request import MIN_SCORED_LINES, ConsideredSupplier, ItemPurchase
from procurement_agent.approvals.service import StoredProposal


def purchase_message(
    stored: StoredProposal,
    purchase: ItemPurchase,
    *,
    notice: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    decision = _decision(purchase)
    if notice:
        decision = f"{notice}\n\n{decision}"
    draft = _draft(stored, purchase)
    text = f"{decision}\n\n{draft}"
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": decision}},
        {"type": "section", "text": {"type": "mrkdwn", "text": draft}},
    ]
    if stored.status == "proposed":
        blocks.append(
            {
                "type": "actions",
                "block_id": "proposal_actions",
                "elements": [
                    _button("Create draft PO", "approve_proposal", stored.id, "primary"),
                    _button("Don't order", "reject_proposal", stored.id, "danger"),
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
        {"type": "section", "text": {"type": "mrkdwn", "text": text}}
    ]
    if stored.status == "proposed":
        blocks.append(
            {
                "type": "actions",
                "block_id": "proposal_actions",
                "elements": [
                    _button("Create draft PO", "approve_proposal", stored.id, "primary"),
                    _button("Don't order", "reject_proposal", stored.id, "danger"),
                ],
            }
        )
    return text, blocks


def _decision(purchase: ItemPurchase) -> str:
    lines = [_asked(purchase), "", _comparison(purchase)]
    note = _price_note(purchase)
    if note:
        lines.append(note)
    return "\n".join(lines)


def _asked(purchase: ItemPurchase) -> str:
    qty = purchase.order.items[0].qty
    if purchase.qty_from_buyer:
        sentence = f"You asked for {qty:g} of {purchase.item_code}"
    else:
        sentence = f"You asked for {purchase.item_code}"
    if purchase.named_supplier:
        sentence += f" from {purchase.named_supplier}"
    if purchase.excluded:
        sentence += f", not from {_join([*purchase.excluded])}"
    return sentence + "."


def _comparison(purchase: ItemPurchase) -> str:
    rows = purchase.considered
    chosen = next(row for row in rows if row.supplier == purchase.supplier)
    count = len(rows)
    noun = "supplier has" if count == 1 else "suppliers have"
    lines = [f"{count} {noun} sold {purchase.item_code}."]
    if count == 1:
        lines.append(_only_supplier(chosen))
        return "\n".join(lines)
    excluded = [row for row in rows if row.excluded]
    if excluded:
        lines.append(f"Excluded: {_names(excluded)}.")
    ruled_out = [
        row
        for row in rows
        if not row.eligible and not row.excluded and row.supplier != chosen.supplier
    ]
    contenders = [row for row in rows if row.eligible and not row.excluded]
    if ruled_out:
        lines.append(_ruled_out(ruled_out))
        perfect = [row for row in ruled_out if (row.on_time_rate or 0) >= 1]
        if perfect:
            verb = "has" if len(perfect) == 1 else "have"
            lines.append(f"{_names(perfect)} {verb} a perfect short record, and that does not win.")
    lines.append(_winner(purchase, chosen, contenders))
    return "\n".join(lines)


def _only_supplier(chosen: ConsideredSupplier) -> str:
    if chosen.scored_lines < MIN_SCORED_LINES:
        return f"{chosen.supplier} is the only supplier on record. There are no scored deliveries."
    return f"{chosen.supplier} is the only supplier on record, {_record(chosen)}."


def _ruled_out(rows: list[ConsideredSupplier]) -> str:
    if len(rows) == 1:
        return (
            f"{rows[0].supplier} is out, with fewer than {MIN_SCORED_LINES} scored deliveries."
        )
    return (
        f"{_names(rows)} are out. Each has fewer than {MIN_SCORED_LINES} scored deliveries."
    )


def _winner(
    purchase: ItemPurchase,
    chosen: ConsideredSupplier,
    contenders: list[ConsideredSupplier],
) -> str:
    if purchase.objective == "named_supplier":
        return _named_winner(chosen, contenders)
    if not contenders:
        return (
            f"Nobody has {MIN_SCORED_LINES} scored deliveries. "
            f"{chosen.supplier} is the most recent seller."
        )
    if purchase.objective == "lowest_price":
        return (
            f"Of the suppliers with at least {MIN_SCORED_LINES} deliveries, "
            f"{chosen.supplier} has the lowest latest price, "
            f"{chosen.rate:g} {purchase.order.currency}."
        )
    if len(contenders) == 1:
        return (
            f"{chosen.supplier} is the only supplier with at least {MIN_SCORED_LINES} deliveries, "
            f"{_record(chosen)}."
        )
    return f"{_race(contenders)} {chosen.supplier} wins."


def _named_winner(chosen: ConsideredSupplier, contenders: list[ConsideredSupplier]) -> str:
    normal = max(
        contenders,
        key=lambda row: (row.on_time_rate or 0, row.scored_lines),
        default=None,
    )
    if normal is not None and normal.supplier != chosen.supplier:
        return (
            f"The {MIN_SCORED_LINES}-delivery rule would have chosen {normal.supplier}, "
            f"{_record(normal)}. You asked for {chosen.supplier} anyway."
        )
    if not chosen.eligible:
        return (
            f"The {MIN_SCORED_LINES}-delivery rule would have left {chosen.supplier} out "
            f"({_record(chosen)}). You asked for them anyway."
        )
    return f"You asked for {chosen.supplier}, {_record(chosen)}."


def _race(contenders: list[ConsideredSupplier]) -> str:
    ordered = sorted(contenders, key=lambda row: (-(row.on_time_rate or 0), row.supplier))
    bits = [f"{row.supplier} was {_record(row)}" for row in ordered]
    if len(bits) == 2:
        return f"{bits[0]}, and {bits[1]}."
    return "; ".join(bits) + "."


def _price_note(purchase: ItemPurchase) -> str | None:
    if purchase.objective != "on_time":
        return None
    chosen = next(row for row in purchase.considered if row.supplier == purchase.supplier)
    cheaper = [
        row
        for row in purchase.considered
        if row.eligible
        and not row.excluded
        and row.supplier != chosen.supplier
        and row.rate < chosen.rate
    ]
    if not cheaper:
        return None
    best = min(cheaper, key=lambda row: (row.rate, row.supplier))
    delta = _money(chosen.rate - best.rate)
    return (
        f"{best.supplier} is {delta} {purchase.order.currency} cheaper, "
        "and price was not the rule."
    )


def _draft(stored: StoredProposal, purchase: ItemPurchase) -> str:
    line = stored.payload.items[0]
    if purchase.qty_from_buyer:
        source = (
            f"Quantity {line.qty:g} is the quantity you asked for. "
            f"The price is {stored.payload.supplier}'s latest order."
        )
    else:
        source = f"Quantity and price are {stored.payload.supplier}'s latest order."
    return "\n".join(
        [
            "*Draft purchase order*",
            "Create draft PO writes this into ERPNext as a draft. It is not submitted.",
            stored.payload.supplier,
            f"{line.item_code} · qty {line.qty:g} · {line.rate:g} {stored.payload.currency}",
            source,
        ]
    )


def _record(row: ConsideredSupplier) -> str:
    if row.on_time_rate is None or row.scored_lines == 0:
        return "no scored deliveries"
    noun = "delivery" if row.scored_lines == 1 else "deliveries"
    return f"on time for {row.on_time_rate:.0%} of {row.scored_lines} {noun}"


def _names(rows: list[ConsideredSupplier]) -> str:
    return _join([row.supplier for row in rows])


def _join(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + ", and " + names[-1]


def _money(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text


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
