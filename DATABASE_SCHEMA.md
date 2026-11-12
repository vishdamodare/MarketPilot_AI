# Database schema (PostgreSQL, 35 tables)

Defined in `backend/app/models.py`; migration `backend/alembic/versions/*_initial_schema.py` (verified to apply on an empty database). UUID primary keys, FKs everywhere, `Numeric(18,2)` money, ISO-4217 `currency`, UTC timestamps, `org_id` on every domain table.

| Area | Tables |
|---|---|
| Identity & access | `users`, `organizations` (mode, currency, default commission), `organization_members` (role, seller_id), `roles`, `permissions` |
| Sellers | `sellers`, `seller_paypal_accounts`, `seller_onboarding`, `products` |
| Orders & payments | `orders`, `order_items`, `marketplace_transactions`, `paypal_orders`, `paypal_captures` |
| Split & settlement | `commissions`, `seller_allocations`, `seller_settlements`, `payouts` (`adjustment` = refund clawbacks netted) |
| Refunds | `refunds` (UNIQUE `org_id, idempotency_key`), `refund_investigations` |
| Disputes | `disputes` (UNIQUE `org_id, paypal_dispute_id`; `response_due_date` NULL when PayPal gave none), `dispute_evidence`, `dispute_responses` |
| Fulfilment | `shipments`, `tracking_events`, `customer_messages` |
| Reconciliation | `reconciliation_runs`, `reconciliation_records`, `financial_ledger` |
| Platform | `webhook_events` (UNIQUE `event_id`), `ai_investigations`, `ai_recommendations`, `notifications`, `audit_logs`, `integration_settings` |

## Ledger
`financial_ledger(entry_type, account ∈ cash|seller|platform, signed amount, ref_type/ref_id, actor_id)` — append-only (no UPDATE/DELETE in code).

| Event | Entries |
|---|---|
| Capture | `payment` cash +gross · `fee` cash −fee & platform −fee · per seller: `commission` platform +c, `seller_earning` seller +net |
| Refund | `refund` cash −amt · per seller: `seller_clawback` seller −(share−rev), `commission_reversal` platform −rev |
| Payout | `payout` cash −amt and seller −amt |

Seller balance = `SUM(amount) WHERE account='seller' AND seller_id=…`.

## Notes
- `roles`/`permissions` are seeded at startup from the code-defined matrix in `auth.py` (single source of truth for checks).
- Indexes: FK columns, `created_at` on high-volume tables, `(org_id, seller_id)` on the ledger, unique constraints listed above.
