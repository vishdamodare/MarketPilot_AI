# Storefront API & scheduled sync

## 1. Get a key
Dashboard → Settings → **Developers** → *Create key* (admin only). The key (`mpk_…`) is shown **once**; only its SHA-256 hash is stored. Revoke any time. Keys are per organization, so a key can only ever touch its own org's data.

All calls: `Authorization: Bearer mpk_…` · JSON · rate limit 120 req/min/key. Session cookies are **not** accepted here.
Base path: `/api/store/v1` (through the Next proxy or directly on the backend).

## 2. Endpoints
| Method & path | Purpose |
|---|---|
| `GET /products` | Active sellers' products: `id, name, price, currency, seller` |
| `POST /orders` | Create an order. Body: `external_id` (your order id), `customer_id`, `customer_email`, `address?`, `lines:[{product_id, qty}]`. **Prices and seller split are computed server-side**; any price you send is ignored. **Idempotent:** re-sending the same `external_id` returns the original order with `"replayed": true` (HTTP 200; first creation is 201). 422 for unknown/foreign products or suspended sellers |
| `GET /orders/{id or external_id}` | Status, allocations (gross/commission/net per seller), payment, refunds |
| `POST /orders/{ref}/paypal-order` | **Sandbox orgs:** create the PayPal order; returns `approve_url` for your buyer |
| `POST /orders/{ref}/capture` | Body `{paypal_order_id}`; captures after buyer approval and records the payment + ledger |
| `POST /orders/{ref}/simulate-payment` | **Demo orgs only:** records a clearly-labelled simulated payment |
| `POST /orders/{ref}/shipments` | Body `{carrier, tracking_number, status: in_transit\|delivered\|exception, address?, events:[{status, description, occurred_at}]}`. Upsert by tracking number; events de-duplicated |
| `POST /orders/{ref}/messages` | Body `{direction: inbound\|outbound, body, sent_at}`; de-duplicated |

Shipments, tracking events and messages are exactly what **DisputePilot** reads as evidence. Push the courier's real events (a `delivered` event is what counts as delivery confirmation); the app never fabricates any.

## 3. Example
```bash
curl -s https://HOST/api/store/v1/products -H "Authorization: Bearer $KEY"
curl -s -X POST https://HOST/api/store/v1/orders -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"external_id":"SHOP-1001","customer_id":"c1","customer_email":"buyer@example.com","address":"1 Main St",
       "lines":[{"product_id":"<id>","qty":2}]}'
curl -s -X POST https://HOST/api/store/v1/orders/SHOP-1001/paypal-order -H "Authorization: Bearer $KEY"   # sandbox org
# buyer approves approve_url in PayPal, then:
curl -s -X POST https://HOST/api/store/v1/orders/SHOP-1001/capture -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d '{"paypal_order_id":"<id>"}'
curl -s -X POST https://HOST/api/store/v1/orders/SHOP-1001/shipments -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"carrier":"UPS","tracking_number":"1Z999","status":"delivered","events":[{"status":"delivered","description":"Left at door","occurred_at":"2026-11-10T09:30:00Z"}]}'
```
Errors: `401` missing/invalid/revoked key · `404` unknown order · `409` wrong org mode or already processed · `422` validation · `429` rate limit · `502` PayPal error (message included).

## 4. Scheduled sync
One cycle per organization (each in its own transaction): ① sandbox orgs with PayPal credentials: pull disputes and poll refunds left `processing` (catches missed webhooks; ledger posted exactly once) ② run reconciliation and notify if mismatches grew ③ alert on disputes that are due within 48 h or overdue (de-duplicated for 24 h) ④ prune reconciliation history to the last 50 runs. Respects Settings → Notification preferences. Each cycle is written to the audit log (`actor=system`).

Triggers:
- **In-process thread** every `SYNC_INTERVAL_SECONDS` (default 900; `0` disables).
- **Cron / external scheduler:** `cd backend && .venv/bin/python -m app.jobs` (e.g. `*/15 * * * *`).
- **Manually:** Integrations → *Run sync now* (`POST /api/jobs/run`, admin). Status: `GET /api/jobs/status`.

A Postgres advisory lock guarantees only one cycle runs at a time across workers/cron.

## Limits (honest)
- Thread + advisory lock, not a queue: no per-job retries or horizontal scaling. Move to Celery/RQ/Arq when needed.
- Payout batches in `processing` are not polled (only webhooks finalise them); refunds are.
- The storefront API has no webhook *to* your store yet (poll `GET /orders/{ref}` for status).
- Sandbox endpoints (`paypal-order`, `capture`) carry the same "unverified against live PayPal" caveats as in PAYPAL_INTEGRATION.md.
