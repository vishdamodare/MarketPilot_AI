# Deploying MarketPilot AI

Three pieces: **Postgres** (Neon) · **backend** (Render, Docker) · **frontend** (Vercel). The browser only talks to the frontend; Next proxies `/api/*` to the backend, so the session cookie is same-origin and needs no CORS.

> Status: the backend start path (clean install from `requirements.txt`, `alembic upgrade head` on an empty DB, boot with a `postgres://` URL, `/api/health`) was run outside Docker. **The Dockerfiles and `docker-compose.yml` have not been built** (Docker was not installed where they were written). Run `docker compose up --build` once before relying on them.

## 0. Try it locally first
`docker compose up --build` → http://localhost:3000 (Postgres + backend + frontend). PayPal keys are read from `backend/.env` if present.

## 1. Database (Neon)
Create a project and copy the connection string (`postgresql://…?sslmode=require`). The backend rewrites the scheme to `postgresql+psycopg://` itself. Migrations run automatically on every backend start (`alembic upgrade head`).

## 2. Backend (Render)
New → Blueprint → this repo (uses `render.yaml`), or New → Web Service → Docker, Dockerfile `backend/Dockerfile`, context `backend`. Set:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon string |
| `SECRET_KEY` | long random (Blueprint generates one) — the app refuses to start with `COOKIE_SECURE=true` and the dev default |
| `COOKIE_SECURE` | `true` |
| `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` | Sandbox app credentials |
| `PAYPAL_BASE_URL` | `https://api-m.sandbox.paypal.com` (leave on sandbox) |
| `PAYPAL_WEBHOOK_ID` | after step 4 |
| `OPENAI_API_KEY` | optional |

Check `https://<backend>/api/health` → `{"ok":true}`.

## 3. Frontend (Vercel)
Import the repo, **Root Directory `frontend`**. Add env var `BACKEND_URL=https://<backend>.onrender.com` **before the first build**: `next.config.ts` bakes the proxy target at build time, so changing it needs a redeploy. Install command: `npm install --legacy-peer-deps`.

## 4. PayPal webhook
developer.paypal.com → your Sandbox app → Webhooks → add `https://<backend>/api/webhooks/paypal/<org_id>` (the exact path is on the app's Integrations page after you sign up with the *PayPal Sandbox* workspace type). Subscribe to the `PAYMENT.CAPTURE.*`, `PAYMENT.REFUND.*`, `CUSTOMER.DISPUTE.*` and `PAYMENT.PAYOUTSBATCH.*` events. Copy the Webhook ID into `PAYPAL_WEBHOOK_ID` and redeploy. Then use the Webhook Simulator and confirm the event appears under Integrations → Recent webhook events.

## 5. Smoke test
Sign up (Demo workspace) → data seeds → Overview loads → Settings → Developers → create a key → `curl` the storefront API. Then a Sandbox workspace: Integrations → Test connection → create/approve/capture an order.

## Things that will bite
- **Free tiers sleep.** A sleeping Render service stops the in-process sync thread. Use a paid instance, or set `SYNC_INTERVAL_SECONDS=0` and call `python -m app.jobs` from a cron.
- **One backend worker/instance** (the Dockerfile runs one). The rate limiter is in memory; a Postgres lock already prevents overlapping sync cycles.
- **Sandbox only.** Do not point `PAYPAL_BASE_URL` at live PayPal until the unverified endpoints in `PAYPAL_INTEGRATION.md` (webhook verification, disputes, payouts) have been exercised.
- **Rotate secrets** that were ever pasted into chat or committed anywhere, including the PayPal client secret.
- No Redis/queue, no email delivery (notifications are in-app only).
