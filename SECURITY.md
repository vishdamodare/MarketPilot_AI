# Security

| Control | Implementation |
|---|---|
| Auth | bcrypt hashes (72-byte input cap), min 10-char passwords, JWT in **httpOnly, SameSite=Lax** cookie (`COOKIE_SECURE=true` behind HTTPS) |
| CSRF | every mutating request needs header `X-Requested-With: marketpilot` (cross-site forms/fetch can't set it without a CORS preflight) |
| AuthZ | `ctx(permission)` dependency on every protected route: session → org membership (`X-Org-Id`) → role permission. Role matrix in `auth.py`. Frontend hiding is cosmetic only |
| Tenant isolation | `org_id` on every table and every query; tested (cross-org 404, non-member 403) |
| Seller isolation | seller members are pinned to their own `seller_id`; portal queries filter by it; sellers have no access to admin/agent endpoints (tested) |
| Money safety | `Decimal` end-to-end; order totals priced server-side from products; refund ids/seller/customer derived server-side; DB row locks + re-validation + unique idempotency keys |
| Approvals | refunds, payouts, dispute submit/accept, commission changes: explicit endpoint, permission, approver recorded, audit-logged; AI cannot call them |
| Webhooks | PayPal signature verification, **fails closed**; dedupe on event id; unverified events stored nowhere and never processed |
| Secrets | PayPal/OpenAI keys only in backend env; adapter redacts them from logged errors; nothing secret reaches the browser |
| Injection/XSS | SQLAlchemy parameterised queries; Pydantic validation; React escapes output; the agent renderer HTML-escapes before bolding; CSV export neutralises `= + - @` formula cells |
| Rate limiting | in-process limiter on login/signup/forgot (use Redis for multi-worker) |
| Privacy | generic error for bad login; forgot-password gives the same reply for unknown emails; reset tokens expire in 15 min and die when the password changes |
| Headers | `nosniff`, `X-Frame-Options: DENY`, `no-referrer`, `no-store` |
| Audit | append-only `audit_logs` incl. every AI tool call/denial; ledger is append-only |

## Known gaps
- Password-reset email is not sent (link is logged server-side in dev); no MFA; no account lockout beyond rate limiting.
- No dependency/SAST scan or external pen-test has been run.
- Single-process rate limiter; no CSP header configured on the Next app.
- The demo seller login password (`demo-seller-pass`) is a documented demo credential on demo orgs only.
- `PAYPAL_BASE_URL` can be pointed at live; the UI shows a red LIVE badge, but there is no extra guard — keep it on sandbox until reviewed.
