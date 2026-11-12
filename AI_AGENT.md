# AI agent

## Contract
The agent can **read** (permission-checked tools) and **draft**. It cannot move money, approve/reject refunds, submit or accept disputes, change commission, or run SQL. Those actions exist only as human-approved endpoints.

## Tools (`backend/app/services/agent.py`)
`get_marketplace_summary`, `get_transaction_details`, `get_seller_details`, `get_seller_earnings`, `get_refund_history`, `investigate_refund`, `get_open_disputes`, `get_dispute_details`, `collect_dispute_evidence`, `generate_dispute_response` (creates a **draft** only), `get_reconciliation_report`, `find_unmatched_transactions`, `get_delayed_payouts`, `get_financial_risk_summary`.

Each call: unknown tools rejected · arguments filtered to the declared schema · role must hold the tool's permission **and** `agent.use` (sellers cannot use the agent at all) · logged to `audit_logs` with `actor_type='ai'` (denials too).

## Grounding
All figures come from `reconcile.rows()/summary()` (SQL on real records; totals = sum of traceable rows) or the engines' deterministic code. In LLM mode the system prompt forbids computing/estimating totals and requires citing tool-returned references; tool outputs are the only numeric source. In deterministic mode, text is templated from tool output.

## Modes
- `OPENAI_API_KEY` set → OpenAI function calling loop (≤6 steps, temperature 0). **Not exercised in tests** (no key was available); on any LLM error it falls back to the deterministic investigator and says so.
- No key → deterministic investigator: keyword intent router → same tools → structured findings (`title, detail, action, refs`) + tool trace. The `/agent` UI shows which mode answered.

## Where else AI appears
Refund investigation summary (optional LLM sentence on top of deterministic findings; never decides). Dispute drafts are template-built from verified evidence — deliberately **not** LLM-written, to rule out invented tracking numbers, messages or invoices.

## Tested
`tests/test_api.py`: seller role denied + audited, unknown/SQL tools rejected, no tool name contains sql/exec, "Prepare all my open disputes" yields only `draft` responses with no approver, no refund status changes.
