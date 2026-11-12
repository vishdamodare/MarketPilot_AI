# MarketPilot AI — The AI Financial Operating System for Marketplaces

One platform for seller payments, refund-leakage detection, dispute investigation and reconciliation on top of PayPal, with an AI agent that investigates your **real records** and can only *draft*, never move money.

| Module | What it does |
|---|---|
| **SmartSplit** | Sellers, per-seller commission rules, order allocations, settlements and payouts (payment ≠ commission ≠ settlement ≠ payout, each its own record) |
| **RefundGuard** | Deterministic refund validation, duplicate/anomaly signals, investigation reports, human approval |
| **DisputePilot** | Verified-evidence collection, completeness score (not a win probability), draft response, approve → submit |
| **MoneyMatch** | Reconciliation, mismatch detection, append-only ledger, drill-down totals, CSV reports |
| **Storefront API** | Key-authenticated order/shipment/message ingestion so a real store can feed the platform ([STOREFRONT_API.md](STOREFRONT_API.md)) |
| **Scheduled sync** | Dispute pull, refund polling, reconciliation and deadline alerts every 15 min (thread, cron or manual) |
| **AI Agent** | Audited read tools across all four engines; LLM tool-calling when `OPENAI_API_KEY` is set, deterministic investigator otherwise |

## Honest status
- **Working and tested:** everything above in **Demo mode** (simulated data, never calls PayPal), the PayPal adapter (mock-transport tests), webhook verification/dedupe logic, RBAC, ledger, reconciliation.
- **Not verified against live PayPal:** no sandbox credentials were available when this was built, so Sandbox flows (order create/capture, refunds, disputes sync, payouts, webhooks, transaction search) are implemented but **have not been executed against PayPal**. Several endpoint shapes are flagged "unverified" in [PAYPAL_INTEGRATION.md](PAYPAL_INTEGRATION.md). Two live tests exist and are skipped until credentials are provided.
- **Multiparty / partner fees / seller onboarding** need PayPal partner approval and are not available in every country (including India); without it orders are platform-collected and commission is tracked internally. Onboarding falls back to a clearly labelled simulation.
- **Not built:** Redis/queue workers (webhooks run inline; the scheduled sync is an in-process thread with a Postgres advisory lock, see STOREFRONT_API.md), email delivery (stored in-app only), Docker files, shadcn CLI components (shadcn-style primitives are hand-written in `frontend/components/ui.tsx`), LLM path is untested without an API key.

## Stack
Next.js 16 (App Router) · React 19 · TypeScript · Tailwind 4 · TanStack Query · React Hook Form + Zod · Recharts · Lucide — FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL · bcrypt + JWT cookie · httpx · OpenAI (optional).

## Setup
Prereqs: Python 3.12 (via [`uv`](https://docs.astral.sh/uv/)), Node 20+, PostgreSQL.

```bash
# database
createdb marketpilot && createdb marketpilot_test

# backend
cd backend
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python fastapi "uvicorn[standard]" "sqlalchemy>=2" alembic "psycopg[binary]" \
  pydantic-settings "pydantic[email]" bcrypt pyjwt httpx openai pytest python-multipart
cp ../.env.example .env            # edit values
.venv/bin/alembic upgrade head     # or let the dev server create tables on startup
.venv/bin/uvicorn app.main:app --port 8010

# frontend (new terminal)
cd frontend
npm install --legacy-peer-deps
echo "BACKEND_URL=http://localhost:8010" > .env.local
npm run dev        # http://localhost:3000
```

## Environment variables
See [`.env.example`](.env.example). Key ones: `DATABASE_URL`, `SECRET_KEY`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BASE_URL` (sandbox default), `PAYPAL_WEBHOOK_ID`, `OPENAI_API_KEY` (optional). PayPal secrets exist only in the backend environment.

## Run the demo (no PayPal needed)
Sign up choosing **Demo** → the "Marketplace Financial Crisis" is seeded (25 sellers, 120 orders, 5 disputes, 3 duplicate refund requests, 4 unreconciled records, 2 payout mismatches + 1 delayed payout). Reset any time: Settings → Organization → *Reset demo data*. Walkthrough: [DEMO_GUIDE.md](DEMO_GUIDE.md).

## Connect PayPal Sandbox
1. Create a Sandbox REST app at developer.paypal.com; put the client id/secret in `backend/.env`.
2. Create a webhook pointing at `https://<public-url>/api/webhooks/paypal/<org_id>` (path shown on the Integrations page) and set `PAYPAL_WEBHOOK_ID`.
3. Sign up with **PayPal Sandbox** workspace type → Integrations → *Test connection*.
4. Transactions → New order → *Create PayPal Sandbox order* → approve with a sandbox buyer → *Capture*.

## Tests
```bash
cd backend && .venv/bin/python -m pytest tests -q         # needs the marketpilot_test Postgres DB
cd frontend && npm test && npx tsc --noEmit && npm run build
# live sandbox (optional): PAYPAL_LIVE_TEST=1 PAYPAL_CLIENT_ID=… PAYPAL_CLIENT_SECRET=… pytest tests/test_paypal.py
```

More: [STOREFRONT_API.md](STOREFRONT_API.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) · [PAYPAL_INTEGRATION.md](PAYPAL_INTEGRATION.md) · [AI_AGENT.md](AI_AGENT.md) · [SECURITY.md](SECURITY.md) · [DEMO_GUIDE.md](DEMO_GUIDE.md)
