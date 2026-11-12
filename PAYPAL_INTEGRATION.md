# PayPal integration

All PayPal I/O is in `backend/app/services/paypal.py`: server-side OAuth (client-credentials, cached), `PayPal-Request-Id` on mutating calls, retry with backoff on 429/5xx (honours `Retry-After`), error bodies redacted of credentials, failures recorded in `integration_settings.last_error`.

**Execution status: nothing here has been run against live PayPal Sandbox** (no credentials were available). Verification below is against PayPal's published OpenAPI specs (`paypal/paypal-rest-api-specifications`) where reachable.

| Capability | Endpoint used | Spec check | Where |
|---|---|---|---|
| OAuth | `POST /v1/oauth2/token` (Basic, `grant_type=client_credentials`) | ✅ verified | `_auth` |
| Create order | `POST /v2/checkout/orders` + `PayPal-Request-Id` | ✅ verified; **ran live against Sandbox 2026-11-12** (request id is per attempt, so an expired order can be retried) | `create_order`, `POST /api/paypal/orders` |
| Capture order | `POST /v2/checkout/orders/{id}/capture` | ✅ **ran live against Sandbox 2026-11-12** (COMPLETED, fee read from response) | `capture_order` |
| Multiparty / partner fee | `payee.merchant_id`, `payment_instruction.platform_fees[]`, `PayPal-Auth-Assertion`, `PayPal-Partner-Attribution-Id` | ⚠️ headers' max length verified; field schemas unverified | `create_order` (only when `PAYPAL_PARTNER_ID` set) |
| Refund / get capture / get refund | `POST /v2/payments/captures/{id}/refund`, `GET …/refunds/{id}`, `GET …/captures/{id}` | ✅ refund **ran live against Sandbox 2026-11-12** (completed, ledger split correct); get capture/get refund not exercised | `refund_capture`, RefundGuard |
| Disputes list/get | `GET /v1/customer/disputes`, `/{id}` | ✅ verified | `list_disputes`, `get_dispute` |
| Provide evidence | `POST /v1/customer/disputes/{id}/provide-evidence` (multipart) | ✅ path/multipart; ⚠️ JSON `input` shape & `evidence_type` values unverified | `provide_evidence` |
| Accept claim | `POST /v1/customer/disputes/{id}/accept-claim` (multipart) | ✅ path/multipart; ⚠️ spec showed only a document part — our `note`/`accept_claim_reason` fields are unverified | `accept_claim` |
| Deadline field | `seller_response_due_date` | ⚠️ from a model summary of the spec, not verbatim; if absent the deadline is shown as *unknown*, never invented | `upsert_dispute` |
| Webhook verification | `POST /v1/notifications/verify-webhook-signature` | ⚠️ unverified; **fails closed** (no webhook id, API error or non-`SUCCESS` ⇒ rejected) | `verify_webhook` |
| Transaction Search | `GET /v1/reporting/transactions` (≤31-day window enforced) | ✅ path; params unverified | `search_transactions`, Integrations page |
| Payouts | `POST /v1/payments/payouts` (`sender_batch_header`, `items`; `sender_batch_id` dedupes 30 days) | ✅ verified | `create_payout`, SmartSplit approve |
| Partner referrals | `POST /v2/customer/partner-referrals` | ✅ path; body fields partly unverified | seller onboarding |

**Before relying on any ⚠️ row, run it once in Sandbox** (`PAYPAL_LIVE_TEST=1 pytest tests/test_paypal.py` covers OAuth, create/get order, list disputes) and compare with the current docs.

## Eligibility (important, especially for India)
- Multiparty payments, partner fees and seller onboarding require **PayPal partner approval** and are country-dependent; availability for Indian businesses must be confirmed with PayPal. Without `PAYPAL_PARTNER_ID` the app creates **platform-collected** orders and tracks commission internally (the UI says which mode each order used).
- Seller onboarding falls back to an explicit **simulation** (`is_simulated`, `SIM-MERCH-…` ids) when Partner Referrals is unavailable.
- Sandbox disputes cannot be created by this app; use PayPal's sandbox to raise one, then *Sync from PayPal*.
- Demo-mode records are never presented as PayPal payments (badges + `SIM-` ids; no PayPal calls).

## Webhooks
`POST /api/webhooks/paypal/{org_id}` (sandbox orgs only). Order: parse → **verify signature** → insert `webhook_events` (UNIQUE `event_id` ⇒ redelivery returns `duplicate_ignored`) → process in a savepoint → 200. Handled: `PAYMENT.CAPTURE.COMPLETED|PENDING|DENIED`, `PAYMENT.CAPTURE.REFUNDED`/`PAYMENT.REFUND.COMPLETED` (finalises a `processing` refund exactly once), `CUSTOMER.DISPUTE.*` (re-fetches the dispute), `PAYMENT.PAYOUTSBATCH.*`. Unverified events are rejected and never processed.

## Configuration
`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BASE_URL` (default sandbox), `PAYPAL_WEBHOOK_ID`; optional `PAYPAL_PARTNER_ID`, `PAYPAL_PARTNER_ATTRIBUTION_ID`. Never commit them.
