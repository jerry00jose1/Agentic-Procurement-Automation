# Procurement assistant demo

A single-user Slack assistant that compares recorded suppliers, collects purchase details,
and creates an ERPNext **draft** only after an approval-button click.

## Run

Configure `.env` from `.env.example`, including OpenAI and Slack credentials, the Slack channel,
an approver's Slack user ID, and `APP_TIMEZONE` (default `Europe/Berlin`). With ERPNext seeded:

```sh
make up-app
make init-db
make slack
```

The listener also creates missing application tables on startup. Existing proposals are retained.
Conversation state lives in the new `buyer_conversations` table and survives listener restarts.

## Demo conversation

Use a real item code in your ERP, such as `S-66100` in the Mizan sample.
`ITEM-A` and `Supplier X` below are placeholders, and are also the synthetic test fixture names.

1. Post: **Which suppliers can provide ITEM-A? Compare price and delivery history.**
   The bot lists recorded suppliers, historical prices/dates, and delivery scores. No proposal
   or approval buttons are created.
2. **Reply in the same Slack thread:** **Prepare an order for 20 units from Supplier X,
   needed next Friday.** Use an actual supplier from the comparison. The item comes from
   conversation context. The bot shows an approval card with quantity, date, warehouse and
   estimated line total.
3. Reply: **Make that 30 units.** The card is updated with a new immutable proposal ID.
   Its previous approval ID is invalidated. Supplier, item and delivery date are preserved.
4. Click **Create draft PO**. The bot returns the draft name and an ERPNext link. It does not
   submit the order or send it to a supplier. **Don't order** rejects the proposal.

Start another thread with **Order some packaging**. The bot asks which item and how many units,
as well as any remaining required details. Answer in that thread to complete the request.
Item codes are matched exactly (case-insensitive); arbitrary product descriptions need clarification.

Normal messages use separate conversations per Slack thread and user. Repeated `/propose`
commands share a conversation per channel/user; replying to a command's bot message starts
a thread with a snapshot of that command's context. Continue in that thread afterward.

Required dates support `today`, `tomorrow`, weekdays, `next Friday`, and `YYYY-MM-DD`.
Weekdays resolve to the coming occurrence; `next Friday` on a Friday means seven days later.
The exact ISO date is shown before approval. Other date phrases trigger clarification.

## Verification

```sh
uv run pytest -p no:capture
uv run mypy src
uv run ruff check src tests scripts/slack_listen.py scripts/eval_demo.py
uv run python scripts/eval_demo.py --live
```

The live rehearsal uses the configured LLM and API credits, synthetic purchase records, a
temporary SQLite vault and a recording writer. **It never connects to Slack or ERPNext.**
It checks the three demo messages, question-only behavior, stale approvals, missing quantities,
and clarification follow-ups. Unit tests also exercise the ERP writer through mocked HTTP.
`-p no:capture` avoids a local Conda/readline crash observed with pytest's capture plugin.

## Boundaries

The model reads the request and, when the buyer asks it to choose, ranks the recorded sellers.
Python checks that the chosen supplier and price are in those records, validates the quantity
and date, and stages the draft. A named supplier is used directly. A shortlist does not create
a purchase until the buyer picks one. The model has no ERP write tool. Approval stays on the button.

Prices are historical estimates, not current quotations. Delivery scores describe recorded
receipt dates across the supplier's history, not current availability or complete delivery.
This demo does not implement general database Q&A, stock planning, tax calculations, or final
purchase-order submission. Monetary conversion and purchase-UOM negotiation are outside this
single-company demo's scope. Confirm commercial terms in ERPNext before submitting a draft.
