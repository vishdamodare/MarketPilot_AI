import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import (Dispute, DisputeResponse, FinancialLedger, Organization, PaypalCapture, Refund, RefundInvestigation, Shipment,
                        TrackingEvent, WebhookEvent, now)
from app.services import finance

D = Decimal


def pub():
    return TestClient(app, headers={"X-Requested-With": "marketpilot"})


# ---------------------------------------------------------------- authentication
def test_signup_login_me_and_bad_password():
    c = pub()
    r = c.post("/api/auth/signup", json={"name": "A", "email": "a@example.com", "password": "long-enough-pw", "org_name": "Org", "mode": "sandbox"})
    assert r.status_code == 200 and r.json()["memberships"][0]["role"] == "admin"
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    assert c.get("/api/auth/me").status_code == 200
    assert pub().post("/api/auth/login", json={"email": "a@example.com", "password": "wrong-password"}).status_code == 401
    assert pub().post("/api/auth/login", json={"email": "a@example.com", "password": "long-enough-pw"}).status_code == 200


def test_signup_validation_duplicates_and_csrf():
    c = pub()
    base = {"name": "A", "email": "b@example.com", "org_name": "Org", "mode": "sandbox"}
    assert c.post("/api/auth/signup", json={**base, "password": "short"}).status_code == 422
    assert c.post("/api/auth/signup", json={**base, "password": "long-enough-pw"}).status_code == 200
    assert pub().post("/api/auth/signup", json={**base, "password": "long-enough-pw"}).status_code == 409
    no_csrf = TestClient(app)  # no X-Requested-With
    assert no_csrf.post("/api/auth/login", json={"email": "b@example.com", "password": "long-enough-pw"}).status_code == 403


def test_unauthenticated_and_forgot_does_not_leak_accounts():
    assert TestClient(app).get("/api/auth/me").status_code == 401
    a = pub().post("/api/auth/forgot", json={"email": "nobody@example.com"}).json()
    assert a["ok"] and "If that account exists" in a["message"]


# ---------------------------------------------------------------- authorization / isolation
def test_role_permissions_enforced_in_backend(w):
    s = w.seller()
    w.db.commit()
    seller_c, fin, admin = w.client("seller", s.id), w.client("finance_manager"), w.client("admin")
    assert seller_c.get("/api/sellers").status_code == 403
    assert seller_c.post("/api/agent/ask", json={"question": "anything"}).status_code == 403
    assert seller_c.get("/api/audit").status_code == 403
    assert fin.patch(f"/api/sellers/{s.id}", json={"commission_pct": "5"}).status_code == 403  # commission change = admin only
    assert fin.post("/api/payouts/request", json={"seller_id": str(s.id)}).status_code == 403
    assert admin.patch(f"/api/sellers/{s.id}", json={"commission_pct": "5"}).status_code == 200
    assert admin.get("/api/portal").status_code == 403


def test_org_isolation_and_non_member_blocked(w):
    other = World_other(w)
    admin_a = w.client("admin")
    s_b = other.seller("Beta")
    other.db.commit()
    assert admin_a.get(f"/api/sellers/{s_b.id}").status_code == 404  # B's seller is invisible inside org A
    spoof = TestClient(app, headers={"X-Requested-With": "marketpilot", "X-Org-Id": str(other.org.id)}, cookies=admin_a.cookies)
    assert spoof.get("/api/sellers").status_code == 403  # A's user is not a member of B


def World_other(w):
    from tests.conftest import World
    return World()


def test_seller_portal_only_sees_own_data(w):
    s1, s2 = w.seller("One"), w.seller("Two")
    w.paid_order(s1, "100.00", cap="C1")
    w.paid_order(s2, "250.00", cap="C2")
    c = w.client("seller", s1.id)
    p = c.get("/api/portal").json()
    assert p["seller"]["business_name"] == "One" and len(p["transactions"]) == 1
    assert p["transactions"][0]["gross"] == "100.00" and p["balance"] == "90.00"
    assert "250" not in str(p) and "Two" not in str(p)


# ---------------------------------------------------------------- sellers & commission
def test_seller_onboarding_simulated_is_labelled(demo_world):
    c = demo_world.client("admin")
    sid = c.post("/api/sellers", json={"business_name": "New Co", "email": "new@example.com"}).json()["id"]
    assert c.get(f"/api/sellers/{sid}").json()["seller"]["status"] == "pending"
    r = c.post(f"/api/sellers/{sid}/onboard").json()
    assert r["simulated"] is True
    det = c.get(f"/api/sellers/{sid}").json()
    assert det["seller"]["status"] == "active" and det["paypal_accounts"][0]["is_simulated"] is True


def test_commission_calculation_decimal_exact(w):
    org = w.org
    s = w.seller()
    assert finance.commission_for(org, s, D("100.00")) == (D("10.00"), D("0.00"), D("10.00"))
    s.commission_pct = D("12.50")
    assert finance.commission_for(org, s, D("99.99"))[2] == D("12.50")  # 12.49875 -> half-up
    s.commission_pct, s.commission_fixed = D("2.9"), D("0.30")
    assert finance.commission_for(org, s, D("10.00"))[2] == D("0.59")
    assert finance.commission_for(org, s, D("0.20"))[2] == D("0.20")  # never exceeds gross


def test_order_is_priced_server_side_and_split_per_seller(w):
    s1, s2 = w.seller("A", D("20")), w.seller("B")
    p1, p2 = w.product(s1, "50.00"), w.product(s2, "30.00")
    w.db.commit()
    c = w.client("admin")
    r = c.post("/api/orders", json={"customer_id": "c1", "customer_email": "c@example.com", "total": "0.01",  # client-supplied total is ignored
                                    "lines": [{"product_id": str(p1.id), "qty": 2}, {"product_id": str(p2.id), "qty": 1}]})
    assert r.status_code == 200
    body = r.json()
    assert body["order"]["total"] == "130.00"
    al = {a["seller"]: a for a in body["allocations"]}
    assert (al["A"]["gross"], al["A"]["commission"], al["A"]["net"]) == ("100.00", "20.00", "80.00")
    assert (al["B"]["gross"], al["B"]["commission"], al["B"]["net"]) == ("30.00", "3.00", "27.00")
    # inactive seller cannot receive orders
    s3 = w.seller("Sleepy", status="suspended")
    p3 = w.product(s3)
    w.db.commit()
    assert c.post("/api/orders", json={"customer_id": "c", "customer_email": "c@example.com", "lines": [{"product_id": str(p3.id), "qty": 1}]}).status_code == 422


# ---------------------------------------------------------------- payment state + ledger
def test_payment_recording_is_idempotent_and_ledger_balances(w):
    o = w.paid_order(price="100.00", cap="CAP-X")
    finance.record_payment(w.db, w.org, o, "CAP-X", simulated=False)  # same capture again
    w.db.commit()
    assert w.db.query(PaypalCapture).filter_by(capture_id="CAP-X").count() == 1
    assert o.status == "paid"
    inv = w.client("admin").get("/api/moneymatch/ledger").json()["invariant"]["cash_minus_seller_minus_platform"]
    assert inv == "0.00"
    s = w.client("admin").get("/api/moneymatch/ledger").json()["totals"]
    assert s["seller_liability"] == "90.00" and s["cash"] == "96.80"  # 100 - 3.20 PayPal fee


def test_failed_payment_does_not_post_ledger(w):
    s = w.seller()
    p = w.product(s)
    o = finance.create_order(w.db, w.org, "ORD-F", "c", "c@example.com", [(p.id, 1)])
    finance.record_payment(w.db, w.org, o, "CAP-D", simulated=False, status="DECLINED")
    w.db.commit()
    assert o.status == "failed" and w.db.query(FinancialLedger).count() == 0


# ---------------------------------------------------------------- RefundGuard
def rk():
    return "key-" + uuid.uuid4().hex


def test_refund_validation_blocks_over_refund_and_cumulative(demo_world):
    w = demo_world  # demo org: approval executes a simulated refund (a sandbox org would call PayPal)
    o = w.paid_order(price="100.00")
    c = w.client("admin")
    r = c.post("/api/refunds", json={"order_id": str(o.id), "amount": "100.01", "idempotency_key": rk()})
    assert r.status_code == 422 and any(not x["passed"] for x in r.json()["detail"]["checks"])
    first = c.post("/api/refunds", json={"order_id": str(o.id), "amount": "60.00", "idempotency_key": rk()}).json()
    assert c.post(f"/api/refunds/{first['id']}/approve").status_code == 200
    assert c.post("/api/refunds", json={"order_id": str(o.id), "amount": "50.00", "idempotency_key": rk()}).status_code == 422  # 60 + 50 > 100
    assert c.post("/api/refunds", json={"order_id": str(o.id), "amount": "40.00", "idempotency_key": rk()}).status_code == 200  # exactly the remainder
    assert c.post("/api/refunds", json={"order_id": str(uuid.uuid4()), "amount": "1.00", "idempotency_key": rk()}).status_code == 422


def test_duplicate_refund_is_flagged_not_rejected_and_investigated(w):
    o = w.paid_order(price="100.00")
    c = w.client("admin")
    a = c.post("/api/refunds", json={"order_id": str(o.id), "amount": "30.00", "idempotency_key": rk()}).json()
    b = c.post("/api/refunds", json={"order_id": str(o.id), "amount": "30.00", "idempotency_key": rk()}).json()
    assert a["status"] == "pending_approval"
    assert b["status"] == "needs_review"  # flagged for a human, NOT auto-rejected
    inv = c.get(f"/api/refunds/{b['id']}").json()["investigation"]
    assert inv["risk_level"] == "high"
    assert any(f["code"] == "potential_duplicate" for f in inv["report"]["flags"])
    assert "not a finding of fraud" in inv["report"]["disclaimer"].lower()
    for k in ("original_transaction_id", "order_id", "seller_id", "customer_id", "original_amount", "previously_refunded", "requested_amount", "refund_history", "reason"):
        assert k in inv["report"]
    assert inv["recommended_action"]


def test_refund_idempotency_key_replay_creates_one_refund(w):
    o = w.paid_order()
    c = w.client("admin")
    key = rk()
    r1 = c.post("/api/refunds", json={"order_id": str(o.id), "amount": "10.00", "idempotency_key": key}).json()
    r2 = c.post("/api/refunds", json={"order_id": str(o.id), "amount": "10.00", "idempotency_key": key}).json()
    assert r1["id"] == r2["id"] and w.db.query(Refund).count() == 1


def test_refund_approval_posts_ledger_once_and_blocks_double_approval(demo_world):
    w = demo_world
    o = w.paid_order(price="100.00")
    c = w.client("admin")
    r = c.post("/api/refunds", json={"order_id": str(o.id), "amount": "40.00", "idempotency_key": rk()}).json()
    ok = c.post(f"/api/refunds/{r['id']}/approve")
    assert ok.status_code == 200 and ok.json()["status"] == "completed" and ok.json()["paypal_refund_id"].startswith("SIM-REF")
    assert c.post(f"/api/refunds/{r['id']}/approve").status_code == 409
    w.db.expire_all()
    rows = w.db.query(FinancialLedger).filter_by(ref_type="refund").all()
    assert sorted(x.entry_type for x in rows) == ["commission_reversal", "refund", "seller_clawback"]
    assert {x.entry_type: x.amount for x in rows} == {"refund": D("-40.00"), "commission_reversal": D("-4.00"), "seller_clawback": D("-36.00")}
    assert c.get("/api/moneymatch/ledger").json()["invariant"]["cash_minus_seller_minus_platform"] == "0.00"
    assert w.db.get(PaypalCapture, w.db.query(PaypalCapture).first().id).status == "PARTIALLY_REFUNDED"


def test_refund_approval_requires_permission_and_reject_is_internal_only(w):
    s = w.seller()
    o = w.paid_order(s)
    admin, seller_c = w.client("admin"), w.client("seller", s.id)
    r = admin.post("/api/refunds", json={"order_id": str(o.id), "amount": "5.00", "idempotency_key": rk()}).json()
    assert seller_c.post(f"/api/refunds/{r['id']}/approve").status_code == 403
    assert w.client("finance_manager").post(f"/api/refunds/{r['id']}/reject", json={"note": "not valid"}).json()["status"] == "rejected"
    assert w.db.query(FinancialLedger).filter_by(ref_type="refund").count() == 0


def test_sandbox_refund_calls_paypal_and_uses_actual_status(w, monkeypatch):
    from app.services import paypal
    calls = []

    class Fake:
        def refund_capture(self, cap, amt, cur, request_id, note=None):
            calls.append((cap, str(amt), request_id))
            return {"id": "REF-1", "status": "PENDING"}

        def get_refund(self, rid):
            return {"id": rid, "status": "PENDING"}  # PayPal says still pending -> we must NOT post to the ledger yet

    monkeypatch.setattr(paypal, "get_client", lambda: Fake())
    o = w.paid_order(cap="CAP-REAL")
    c = w.client("admin")
    r = c.post("/api/refunds", json={"order_id": str(o.id), "amount": "10.00", "idempotency_key": "idem-key-0001"}).json()
    out = c.post(f"/api/refunds/{r['id']}/approve").json()
    assert calls == [("CAP-REAL", "10.00", "idem-key-0001")]  # idempotency key forwarded as PayPal-Request-Id
    assert out["status"] == "processing" and w.db.query(FinancialLedger).filter_by(ref_type="refund").count() == 0


# ---------------------------------------------------------------- DisputePilot
def mk_dispute(w, order, reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED", due_hours=72, simulated=False):
    d = Dispute(org_id=w.org.id, paypal_dispute_id=f"PP-{uuid.uuid4().hex[:6]}", order_id=order.id, seller_id=None, capture_id="CAP-1", reason=reason,
                status="waiting_merchant", amount=order.total, currency="USD", opened_at=now() - timedelta(days=1), is_simulated=simulated,
                response_due_date=now() + timedelta(hours=due_hours) if due_hours is not None else None)
    w.db.add(d)
    w.db.commit()
    return d


def test_dispute_missing_evidence_is_reported_never_invented(w):
    o = w.paid_order()
    d = mk_dispute(w, o)
    c = w.client("admin")
    det = c.get(f"/api/disputes/{d.id}").json()
    a = det["analysis"]
    assert "Delivery confirmation" in a["missing_required"] and "Courier tracking" in a["missing_required"]
    assert a["recommendation"] != "contest" and a["score"] < 70
    assert "NOT a probability of winning" in a["score_note"]
    ev = {e["kind"]: e for e in a["evidence"]}
    assert ev["invoice"]["available"] is False and ev["tracking"]["available"] is False
    body = c.post(f"/api/disputes/{d.id}/generate").json()["response"]["body"]
    assert "NOT AVAILABLE" in body and "SIM" not in body and "1Z" not in body  # nothing fabricated
    assert det["dispute"]["response_due_date"] is not None


def test_dispute_with_full_evidence_scores_high_and_deadline_unknown_is_not_fabricated(w):
    o = w.paid_order()
    o.created_at = now() - timedelta(days=10)  # delivery must predate the dispute, otherwise the engine (correctly) flags a contradiction
    sh = Shipment(org_id=w.org.id, order_id=o.id, carrier="UPS", tracking_number="TRK123", status="delivered", address="1 Main St")
    w.db.add(sh)
    w.db.flush()
    for st, dd in (("shipped", 1), ("delivered", 3)):
        w.db.add(TrackingEvent(org_id=w.org.id, shipment_id=sh.id, status=st, description=st, occurred_at=o.created_at + timedelta(days=dd)))
    d = mk_dispute(w, o, due_hours=None)
    a = w.client("admin").get(f"/api/disputes/{d.id}").json()
    assert a["analysis"]["missing_required"] == [] and a["analysis"]["recommendation"] == "contest" and a["analysis"]["score"] >= 70
    assert a["analysis"]["deadline"]["level"] == "unknown" and a["dispute"]["response_due_date"] is None


def test_dispute_submission_needs_explicit_approval_and_edits_void_it(demo_world):
    w = demo_world
    o = w.paid_order()
    d = mk_dispute(w, o, simulated=True)
    c = w.client("admin")
    r = c.post(f"/api/disputes/{d.id}/generate").json()["response"]
    url = f"/api/disputes/{d.id}/responses/{r['id']}"
    assert c.post(url + "/submit").status_code == 403  # draft cannot be submitted
    assert c.post(url + "/approve").json()["response"]["status"] == "approved"
    edited = c.put(url, json={"body": "Edited by the merchant, version two."}).json()["response"]
    assert edited["status"] == "draft" and edited["approved_by"] is None  # edit voids approval
    assert c.post(url + "/submit").status_code == 403
    c.post(url + "/approve")
    done = c.post(url + "/submit").json()
    assert done["response"]["status"] == "submitted" and done["response"]["result"]["simulated"] is True
    assert c.post(url + "/submit").status_code == 403  # already submitted
    assert w.client("finance_manager").post(url + "/approve").status_code == 403  # finance cannot approve submissions


# ---------------------------------------------------------------- reconciliation (seeded scenario)
@pytest.fixture
def seeded():
    from tests.conftest import World
    w = World(mode="demo")
    c = w.client("admin")
    r = c.post("/api/demo/seed")
    assert r.status_code == 200, r.text
    yield w, c, r.json()
    w.db.close()


def test_demo_scenario_matches_spec_and_reconciliation_finds_planted_problems(seeded):
    w, c, res = seeded
    assert res["sellers"] == 25 and res["orders"] >= 120
    run = c.get("/api/moneymatch/latest").json()
    kinds = [r["kind"] for r in run["records"]]
    assert kinds.count("order_without_payment") == 1 and kinds.count("payment_without_order") == 1
    assert kinds.count("unreconciled_refund") == 1 and kinds.count("allocation_mismatch") == 1  # the 4 unreconciled records
    assert kinds.count("payout_mismatch") == 2 and kinds.count("payout_delayed") == 1
    dups = [i for i in w.db.query(RefundInvestigation).all() if any(f["code"] == "potential_duplicate" for f in i.report["flags"])]
    assert len(dups) == 3
    assert w.db.query(Dispute).count() == 5 and all(d.is_simulated for d in w.db.query(Dispute))
    assert c.get("/api/moneymatch/ledger").json()["invariant"]["cash_minus_seller_minus_platform"] == "0.00"


def test_every_summary_number_is_the_sum_of_traceable_rows(seeded):
    w, c, _ = seeded
    s = c.get("/api/moneymatch/summary?days=60").json()["metrics"]
    for m in ("payments", "refunds", "commissions", "pending_settlements", "payouts"):
        t = c.get(f"/api/moneymatch/trace?metric={m}&days=60").json()
        assert t["total"] == s[m]["amount"] and len(t["rows"]) == s[m]["count"], m


def test_demo_reset_restores_scenario_and_demo_never_touches_other_orgs(seeded):
    w, c, _ = seeded
    sandbox = World_other(w)
    sandbox.seller("Real Seller")
    sandbox.db.commit()
    before = c.get("/api/sellers").json()["total"]
    c.post("/api/demo/seed")
    assert c.get("/api/sellers").json()["total"] == before == 25
    assert sandbox.db.query(FinancialLedger).filter_by(org_id=sandbox.org.id).count() == 0
    assert sandbox.client("admin").post("/api/demo/seed").status_code == 409  # demo data can never be loaded into a sandbox org


def test_csv_report_blocks_formula_injection(w):
    s = w.seller("=HYPERLINK(\"http://evil\")")
    w.paid_order(s)
    r = w.client("admin").get("/api/moneymatch/reports/commission?format=csv")
    assert r.status_code == 200 and "'=HYPERLINK" in r.text


# ---------------------------------------------------------------- webhooks
def event(eid="WH-1", typ="PAYMENT.CAPTURE.DENIED", cap="CAP-1"):
    return {"id": eid, "event_type": typ, "resource": {"id": cap}}


def test_unverified_webhook_is_rejected_and_not_processed(w, monkeypatch):
    from app.routers import paypal as pr
    monkeypatch.setattr(pr, "verify_event", lambda h, e: False)
    w.paid_order(cap="CAP-1")
    r = pub().post(f"/api/webhooks/paypal/{w.org.id}", json=event())
    assert r.status_code == 400
    assert w.db.query(WebhookEvent).count() == 0
    assert w.db.query(PaypalCapture).first().status == "COMPLETED"


def test_webhook_fails_closed_without_webhook_id(w):
    w.paid_order(cap="CAP-1")
    assert pub().post(f"/api/webhooks/paypal/{w.org.id}", json=event()).status_code == 400  # PAYPAL_WEBHOOK_ID unset -> cannot verify -> reject


def test_verified_webhook_is_processed_once_even_if_delivered_twice(w, monkeypatch):
    from app.routers import paypal as pr
    monkeypatch.setattr(pr, "verify_event", lambda h, e: True)
    w.paid_order(cap="CAP-1")
    c = pub()
    assert c.post(f"/api/webhooks/paypal/{w.org.id}", json=event()).json()["status"] == "ok"
    assert c.post(f"/api/webhooks/paypal/{w.org.id}", json=event()).json()["status"] == "duplicate_ignored"
    w.db.expire_all()
    assert w.db.query(WebhookEvent).count() == 1
    assert w.db.query(PaypalCapture).first().status == "DECLINED"


def test_webhook_for_demo_org_is_refused(demo_world):
    assert pub().post(f"/api/webhooks/paypal/{demo_world.org.id}", json=event()).status_code == 404


# ---------------------------------------------------------------- AI agent permissions & safety
def test_agent_tool_authorization_and_audit(w):
    from app.auth import Ctx
    from app.models import AuditLog, OrganizationMember, User
    from app.services import agent
    s = w.seller()
    w.client("seller", s.id)
    u = w.db.query(User).first()
    m = w.db.query(OrganizationMember).filter_by(user_id=u.id).first()
    ctx_seller = Ctx(w.db, u, w.org, m)
    res = agent.run_tool(ctx_seller, "get_marketplace_summary", {})
    assert "Not authorized" in res["data"]["error"]
    assert w.db.query(AuditLog).filter_by(action="agent.tool_denied", actor_type="ai").count() == 1
    assert "Unknown tool" in agent.run_tool(ctx_seller, "run_sql", {"query": "drop table users"})["data"]["error"]  # no arbitrary SQL tool exists
    assert not any("sql" in t.lower() or "exec" in t.lower() for t in agent.TOOLS)


def test_agent_answers_from_records_and_only_drafts_never_submits(seeded):
    w, c, _ = seeded
    risk = c.post("/api/agent/ask", json={"question": "What is putting my marketplace money at risk?"}).json()
    assert risk["mode"] == "deterministic" and len(risk["findings"]) >= 3
    assert any(f["refs"] for f in risk["findings"]) and "potential duplicate" in risk["answer"]
    n_refunds_before = w.db.query(Refund).filter_by(status="completed").count()
    prep = c.post("/api/agent/ask", json={"question": "Prepare all my open disputes"}).json()
    assert "NOT submitted" in prep["answer"]
    w.db.expire_all()
    resp = w.db.query(DisputeResponse).all()
    assert resp and all(r.status == "draft" and r.approved_by is None for r in resp)
    assert w.db.query(Refund).filter_by(status="completed").count() == n_refunds_before  # agent moved no money
    assert {t["tool"] for t in prep["tool_calls"]} <= set(__import__("app.services.agent", fromlist=["TOOLS"]).TOOLS)


def test_changing_default_commission_does_not_flag_historical_orders(w):
    w.paid_order(price="100.00", cap="CAP-A")
    c = w.client("admin")
    assert c.patch("/api/org", json={"default_commission_pct": "15"}).status_code == 200
    kinds = [r["kind"] for r in c.post("/api/moneymatch/run").json()["records"]]
    assert "incorrect_commission" not in kinds and "allocation_mismatch" not in kinds


def test_agent_routes_order_reference_to_transaction_details(w):
    o = w.paid_order(cap="CAP-Z")
    r = w.client("admin").post("/api/agent/ask", json={"question": f"Why is there a mismatch in {o.number}?"}).json()
    assert [t["tool"] for t in r["tool_calls"]] == ["get_transaction_details"] and o.number in r["answer"]
