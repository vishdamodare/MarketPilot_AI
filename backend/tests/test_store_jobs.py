import hashlib
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import jobs
from app.config import get_settings
from app.main import app
from app.models import (ApiKey, AuditLog, Dispute, FinancialLedger, IntegrationSetting, Notification, Order, Refund, ReconciliationRun,
                        TrackingEvent, now)
from app.services import disputes as dsp
from app.services import paypal


def store(key=None):
    return TestClient(app, headers={"Authorization": f"Bearer {key}"} if key else {})


def mk_key(w, name="shop"):
    c = w.client("admin")
    r = c.post("/api/settings/api-keys", json={"name": name})
    assert r.status_code == 200, r.text
    return c, r.json()


# ---------------------------------------------------------------- API keys
def test_key_is_shown_once_and_only_the_hash_is_stored(w):
    c, k = mk_key(w)
    assert k["key"].startswith("mpk_") and "key_hash" not in k
    row = w.db.query(ApiKey).one()
    assert row.key_hash == hashlib.sha256(k["key"].encode()).hexdigest() and k["key"] not in (row.key_hash, row.prefix)
    listing = c.get("/api/settings/api-keys").json()
    assert listing[0]["prefix"] == k["key"][:8] and "key" not in listing[0] and "key_hash" not in listing[0]
    assert w.db.query(AuditLog).filter_by(action="api_key.created").count() == 1


def test_only_admins_manage_keys_and_revocation_works(w):
    assert w.client("finance_manager").post("/api/settings/api-keys", json={"name": "x"}).status_code == 403
    c, k = mk_key(w)
    assert store(k["key"]).get("/api/store/v1/products").status_code == 200
    assert c.delete(f"/api/settings/api-keys/{k['id']}").status_code == 200
    assert store(k["key"]).get("/api/store/v1/products").status_code == 401


def test_store_api_requires_a_valid_bearer_key_and_ignores_session_cookies(w):
    assert store().get("/api/store/v1/products").status_code == 401
    assert store("mpk_nope").get("/api/store/v1/products").status_code == 401
    cookie_only = w.client("admin")  # a logged-in admin session is NOT an API key
    cookie_only.headers.pop("Authorization", None)
    assert cookie_only.get("/api/store/v1/products").status_code == 401


# ---------------------------------------------------------------- orders
def test_order_creation_is_server_priced_and_idempotent(w):
    s = w.seller(pct=Decimal("20"))
    p = w.product(s, "50.00")
    w.db.commit()
    _, k = mk_key(w)
    sc = store(k["key"])
    body = {"external_id": "SHOP-1001", "customer_id": "c1", "customer_email": "buyer@example.com", "total": "0.01", "price": "0.01",
            "lines": [{"product_id": str(p.id), "qty": 2, "price": "0.01"}]}
    r1 = sc.post("/api/store/v1/orders", json=body)
    assert r1.status_code == 201 and r1.json()["total"] == "100.00" and r1.json()["replayed"] is False  # client-sent prices ignored
    assert r1.json()["allocations"][0]["commission"] == "20.00"
    r2 = sc.post("/api/store/v1/orders", json=body)
    assert r2.status_code == 200 and r2.json()["replayed"] is True and r2.json()["id"] == r1.json()["id"]
    assert w.db.query(Order).count() == 1
    assert sc.get("/api/store/v1/orders/SHOP-1001").json()["id"] == r1.json()["id"]  # lookup by your own id


def test_order_validation_and_tenant_isolation(w):
    from tests.conftest import World
    other = World()
    foreign = other.product(other.seller("Foreign"))
    own_suspended = w.product(w.seller("Sleepy", status="suspended"))
    own = w.product(w.seller("Active"))
    other.db.commit(); w.db.commit()
    _, k = mk_key(w)
    sc = store(k["key"])
    mk = lambda pid, eid: sc.post("/api/store/v1/orders", json={"external_id": eid, "customer_id": "c", "customer_email": "b@example.com", "lines": [{"product_id": str(pid), "qty": 1}]})
    assert mk(foreign.id, "A").status_code == 422        # other org's product
    assert mk(own_suspended.id, "B").status_code == 422  # suspended seller
    assert sc.post("/api/store/v1/orders", json={"external_id": "C", "customer_id": "c", "customer_email": "bad", "lines": []}).status_code == 422
    ok = mk(own.id, "D").json()
    _, k2 = mk_key(other, "other-shop")
    assert store(k2["key"]).get(f"/api/store/v1/orders/{ok['id']}").status_code == 404  # another org's key cannot read it
    names = [p["name"] for p in sc.get("/api/store/v1/products").json()]
    assert len(names) == 1  # only active sellers' products, only this org


def test_simulate_payment_only_in_demo_orgs_and_sandbox_paypal_only_in_sandbox(demo_world, w):
    for world, mode in ((demo_world, "demo"), (w, "sandbox")):
        p = world.product(world.seller("S" + mode))
        world.db.commit()
        _, k = mk_key(world)
        sc = store(k["key"])
        o = sc.post("/api/store/v1/orders", json={"external_id": "X1", "customer_id": "c", "customer_email": "b@example.com", "lines": [{"product_id": str(p.id), "qty": 1}]}).json()
        sim = sc.post(f"/api/store/v1/orders/{o['id']}/simulate-payment")
        pp = sc.post(f"/api/store/v1/orders/{o['id']}/paypal-order")
        if mode == "demo":
            assert sim.status_code == 200 and sim.json()["status"] == "paid" and sim.json()["payment"]["simulated"] is True
            assert pp.status_code == 409  # demo orgs never call PayPal
            assert sc.post(f"/api/store/v1/orders/{o['id']}/simulate-payment").status_code == 409  # not twice
        else:
            assert sim.status_code == 409
            assert pp.status_code == 502 and "not configured" in pp.text.lower()  # clear error, no credentials


def test_capture_rejects_a_paypal_order_from_a_different_order(w, monkeypatch):
    s = w.seller()
    p = w.product(s)
    w.db.commit()
    _, k = mk_key(w)
    sc = store(k["key"])
    o = sc.post("/api/store/v1/orders", json={"external_id": "Z", "customer_id": "c", "customer_email": "b@example.com", "lines": [{"product_id": str(p.id), "qty": 1}]}).json()
    assert sc.post(f"/api/store/v1/orders/{o['id']}/capture", json={"paypal_order_id": "PP-NOT-MINE"}).status_code == 404


# ---------------------------------------------------------------- shipments & messages become real dispute evidence
def test_pushed_shipment_and_messages_feed_dispute_evidence_idempotently(w):
    from app.models import Dispute
    s = w.seller()
    p = w.product(s)
    w.db.commit()
    _, k = mk_key(w)
    sc = store(k["key"])
    o = sc.post("/api/store/v1/orders", json={"external_id": "S1", "customer_id": "c", "customer_email": "b@example.com", "address": "1 Main St", "lines": [{"product_id": str(p.id), "qty": 1}]}).json()
    order = w.db.get(Order, uuid.UUID(o["id"]))
    from app.services import finance
    finance.record_payment(w.db, w.org, order, "CAP-S1", simulated=False)
    order.created_at = now() - timedelta(days=10)
    w.db.commit()
    ship = {"carrier": "UPS", "tracking_number": "1Z999", "status": "delivered", "address": "1 Main St",
            "events": [{"status": "shipped", "description": "Picked up", "occurred_at": (now() - timedelta(days=8)).isoformat()},
                       {"status": "delivered", "description": "Left at door", "occurred_at": (now() - timedelta(days=5)).isoformat()}]}
    assert sc.post(f"/api/store/v1/orders/{o['id']}/shipments", json=ship).json()["events_added"] == 2
    assert sc.post(f"/api/store/v1/orders/{o['id']}/shipments", json=ship).json()["events_added"] == 0  # replay adds nothing
    msg = {"direction": "inbound", "body": "Where is my order?", "sent_at": (now() - timedelta(days=6)).isoformat()}
    assert sc.post(f"/api/store/v1/orders/{o['id']}/messages", json=msg).json()["stored"] is True
    assert sc.post(f"/api/store/v1/orders/{o['id']}/messages", json=msg).json()["stored"] is False
    assert w.db.query(TrackingEvent).count() == 2
    d = Dispute(org_id=w.org.id, paypal_dispute_id="PP-S1", order_id=order.id, capture_id="CAP-S1", reason="MERCHANDISE_OR_SERVICE_NOT_RECEIVED", status="waiting_merchant",
                amount=order.total, currency="USD", opened_at=now() - timedelta(days=1), response_due_date=now() + timedelta(days=5))
    w.db.add(d)
    w.db.commit()
    a = w.client("admin").get(f"/api/disputes/{d.id}").json()["analysis"]
    assert a["missing_required"] == [] and a["recommendation"] == "contest"  # evidence came from the storefront push, not invention
    assert sc.post(f"/api/store/v1/orders/{o['id']}/shipments", json={**ship, "status": "teleported"}).status_code == 422


# ---------------------------------------------------------------- scheduled sync
def _dispute(w, hours, status="waiting_merchant", pid="PP-J1"):
    o = w.paid_order(cap="CAP-" + pid)
    d = Dispute(org_id=w.org.id, paypal_dispute_id=pid, order_id=o.id, capture_id="CAP-1", reason="OTHER", status=status, amount=o.total, currency="USD",
                opened_at=now(), response_due_date=now() + timedelta(hours=hours), is_simulated=True)
    w.db.add(d)
    w.db.commit()
    return d


def test_sync_alerts_on_deadlines_once_and_respects_preferences(demo_world):
    w = demo_world
    _dispute(w, 20)
    r1 = jobs.sync_org(w.db, w.org)
    r2 = jobs.sync_org(w.db, w.org)  # same day: no duplicate alert
    w.db.commit()
    assert r1["deadline_alerts"] == 1 and r2["deadline_alerts"] == 0
    assert w.db.query(Notification).filter(Notification.title.like("%due within 48 hours%")).count() == 1
    assert w.db.query(AuditLog).filter_by(action="job.sync_completed", actor_type="system").count() == 2
    _dispute(w, -5, pid="PP-J2")
    w.client("admin").put("/api/settings/notifications", json={"dispute_deadlines": False})
    assert jobs.sync_org(w.db, w.org)["deadline_alerts"] == 0  # preference off -> silent


def test_sync_runs_reconciliation_alerts_on_growth_and_prunes_history(demo_world):
    w = demo_world
    w.paid_order(cap="CAP-OK")
    jobs.sync_org(w.db, w.org)
    w.db.commit()
    from app.models import PaypalCapture
    w.db.add(PaypalCapture(org_id=w.org.id, order_id=None, capture_id="CAP-ORPHAN", status="COMPLETED", amount=Decimal("9.99"), currency="USD", is_simulated=True))
    w.db.commit()
    out = jobs.sync_org(w.db, w.org)
    w.db.commit()
    assert out["mismatches"] == 1
    assert w.db.query(Notification).filter(Notification.title.like("Reconciliation found 1 new%")).count() == 1
    for _ in range(jobs.KEEP_RUNS + 3):
        jobs.sync_org(w.db, w.org)
    w.db.commit()
    assert w.db.query(ReconciliationRun).count() == jobs.KEEP_RUNS


class FakePP:
    def __init__(self, refund_status="COMPLETED", fail=False):
        self.refund_status, self.fail = refund_status, fail

    def list_disputes(self, page_size=50):
        if self.fail:
            raise paypal.PayPalError("PayPal is down", 503)
        return []

    def get_refund(self, rid):
        return {"id": rid, "status": self.refund_status}


@pytest.fixture
def configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "paypal_client_id", "cid")
    monkeypatch.setattr(s, "paypal_client_secret", "sec")


def test_sync_polls_processing_refunds_and_finalises_them_exactly_once(w, configured, monkeypatch):
    monkeypatch.setattr(paypal, "get_client", lambda: FakePP("COMPLETED"))
    o = w.paid_order(cap="CAP-R")
    r = Refund(org_id=w.org.id, order_id=o.id, capture_id="CAP-R", amount=Decimal("10.00"), currency="USD", status="processing", idempotency_key="k-poll-1", paypal_refund_id="REF-1", customer_id="c")
    w.db.add(r)
    w.db.commit()
    out = jobs.sync_org(w.db, w.org)
    jobs.sync_org(w.db, w.org)  # second cycle must not post again
    w.db.commit()
    assert out["refunds_updated"] == 1 and out["disputes_synced"] == 0
    w.db.refresh(r)
    assert r.status == "completed"
    assert w.db.query(FinancialLedger).filter_by(ref_type="refund", entry_type="refund").count() == 1
    assert w.db.query(IntegrationSetting).filter_by(org_id=w.org.id).one().last_error is None


def test_sync_survives_paypal_outage_records_error_and_still_reconciles(w, configured, monkeypatch):
    monkeypatch.setattr(paypal, "get_client", lambda: FakePP(fail=True))
    w.paid_order()
    out = jobs.sync_org(w.db, w.org)
    w.db.commit()
    assert "PayPal is down" in out["paypal_error"] and "mismatches" in out
    assert "PayPal is down" in w.db.query(IntegrationSetting).filter_by(org_id=w.org.id).one().last_error


def test_sandbox_without_credentials_skips_paypal_cleanly(w):
    assert "skipped" in jobs.sync_org(w.db, w.org)["paypal"]


def test_run_cycle_covers_every_org_and_isolates_failures(w, demo_world, monkeypatch):
    real = jobs.sync_org

    def flaky(db, org):
        if org.mode == "sandbox":
            raise RuntimeError("boom")
        return real(db, org)

    w.db.commit(); demo_world.db.commit()
    monkeypatch.setattr(jobs, "sync_org", flaky)
    res = jobs.run_cycle()
    assert {r.get("error") for r in res} >= {"RuntimeError"} and any("mismatches" in r for r in res)  # the demo org still synced


def test_jobs_endpoints_permissions_and_status(w):
    s = w.seller()
    w.db.commit()
    admin, fin, seller = w.client("admin"), w.client("finance_manager"), w.client("seller", s.id)
    assert admin.get("/api/jobs/status").json()["last_run"] is None
    assert fin.post("/api/jobs/run").status_code == 403 and seller.post("/api/jobs/run").status_code == 403
    assert "mismatches" in admin.post("/api/jobs/run").json()
    st = fin.get("/api/jobs/status").json()
    assert st["last_run"]["result"]["org"] == "Test Mkt" and st["scheduler_enabled"] is False  # tests run with the thread disabled
