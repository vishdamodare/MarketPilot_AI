# Demo guide (3 minutes)

**Setup:** run backend + frontend (see README), open `/signup?mode=demo`, create an account → onboarding → *Go to dashboard*. Everything is labelled **DEMO MODE / simulated**. Reset anytime: Settings → Organization → *Reset demo data*.

## Script
1. **Overview** — payment volume, commission, pending payouts, open disputes (5), reconciliation health, AI alerts (duplicate refunds, urgent dispute, payout mismatches, unreconciled records).
2. **Integrations** — demo banner: PayPal is never called. (With credentials in a Sandbox workspace this shows connected/sandbox, last sync, webhook status, last error.)
3. **Transactions → New order** — pick a product, create; open the order: seller allocation + commission computed server-side; *Record simulated payment* (clearly simulated). In a Sandbox workspace this becomes *Create PayPal Sandbox order → approve → Capture*.
4. **RefundGuard** — open ORD-1027 (or ORD-1029): potential duplicate refund, validation checks, history, "not a finding of fraud". *Approve refund…* needs a confirmation; or Reject / Escalate.
5. **DisputePilot** — PP-SIM-D-5002 (deadline < 30 h, missing delivery confirmation): checklist with MISSING items, completeness score with breakdown, recommendation. *Generate draft* → edit → *Approve* → *Submit* (simulated). PP-SIM-D-5004 (order not linked) and PP-SIM-D-5005 (already refunded → accept) show the other paths.
6. **MoneyMatch** — *Run reconciliation*: order without payment, orphan capture, completed refund missing PayPal/ledger reference, tampered commission, 2 payout mismatches, 1 delayed payout. *Totals & drill-down → View sources* shows the rows behind any number. Ledger tab shows the balanced invariant. Reports → CSV.
7. **AI Agent** — ask **"What is putting my marketplace money at risk?"** → findings with references and an investigation trace. Also try "Prepare all my open disputes" (drafts only), "Show me sellers whose payouts are delayed", "How much did refunds cost us this week?".
8. **Seller portal** — sign out; log in with the seller credentials shown after *Reset demo data* (`seller+<id>@example.com` / `demo-seller-pass`): only that seller's earnings, payouts, refunds, disputes.

## Planted scenario ("Marketplace Financial Crisis")
25 sellers (22 active, 2 pending, 1 suspended) · 120 orders (+1 planted ghost order) · 5 disputes · 3 duplicate-refund requests · 4 unreconciled records · 2 payout mismatches (+1 delayed payout). Deterministic (`random.Random(42)`).

## Truthfulness notes for judges
Demo data is simulated and never sent to PayPal. Sandbox flows are implemented but not yet exercised against live PayPal (see PAYPAL_INTEGRATION.md).
