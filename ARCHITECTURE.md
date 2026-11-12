# Architecture

Modular monolith: one FastAPI service + one Next.js app + PostgreSQL.

```
Browser ──(same-origin /api/*, httpOnly cookie)──> Next.js (rewrites) ──> FastAPI
                                                                          ├─ routers/   thin HTTP + permission dependency
                                                                          ├─ services/  finance · refunds · disputes · reconcile · agent · demo · llm · paypal
                                                                          └─ models.py  SQLAlchemy ──> PostgreSQL
PayPal ──webhooks──> /api/webhooks/paypal/{org_id}   (signature verified, deduped by event id)
```

## Layers
- **routers/** only parse input, call `ctx("<permission>")` (session + CSRF header + org membership + role permission) and shape output.
- **services/finance.py** all money rules: Decimal commission, server-side order pricing, `record_payment` (idempotent on capture id), ledger posting, payouts.
- **services/refunds.py** validation → anomaly flags → investigation → approval (row lock + re-validation + idempotency key + PayPal's actual refund status).
- **services/disputes.py** evidence collection from DB only, contradiction checks, transparent score, draft generation, approve/submit state machine.
- **services/reconcile.py** `rows()` is the single source of every figure; totals are sums of those rows, so any number can be traced.
- **services/agent.py** fixed tool registry; each call permission-checked and audited with `actor_type='ai'`.
- **services/paypal.py** the only module that talks to PayPal.

## Tenancy & modes
Every domain table has `org_id`. An organization has a fixed `mode` (`demo` | `sandbox`), so demo rows and sandbox rows can never share an org. Demo actions never call PayPal; records carry `is_simulated`.

## Key decisions
- **Ledger:** append-only `financial_ledger`; balances are `SUM`s. Invariant `cash = seller liability + platform revenue` is tested.
- **Money:** `Numeric(18,2)` ↔ `Decimal`; money crosses the API as strings; floats only for chart rendering.
- **Sensitive actions** (refund execution, payouts, dispute submit/accept, commission change) require human approval; approver is stored and audited.
- **Idempotency:** refund idempotency key is unique per org and is forwarded to PayPal as `PayPal-Request-Id`; webhooks dedupe on PayPal event id (UNIQUE).

## Known simplifications (ponytail ceilings)
- No Redis/queue: webhooks and syncs run inline in the request. Move to a worker when volume or latency demands it.
- In-process rate limiter (per worker). Use Redis for multi-worker.
- Reconciliation and summaries sum rows in Python; push to SQL aggregates at scale.
- Dev server calls `create_all`; production should use `alembic upgrade head` (initial migration included and verified on a clean DB).
