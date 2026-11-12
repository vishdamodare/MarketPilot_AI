"""Demo scenario "Marketplace Financial Crisis". Everything here is SIMULATED (is_simulated=True, ids prefixed SIM-)
and lives only in an organization whose mode is 'demo'. It is never written into a sandbox org.

Planted problems: 25 sellers, 120 orders, 5 disputes, 3 potential duplicate refunds, 4 unreconciled records,
2 payout mismatches (+1 delayed payout shown as a separate 'payout_delayed' record).
"""
import random
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..auth import hash_password
from ..db import Base
from ..models import (Commission, CustomerMessage, Dispute, FinancialLedger, MarketplaceTransaction, Order, OrganizationMember,
                      PaypalCapture, Payout, Product, Refund, Seller, SellerAllocation, SellerPaypalAccount, SellerSettlement,
                      Shipment, TrackingEvent, User, now, IntegrationSetting)
from . import finance, reconcile, refunds
from .finance import q

NAMES = ["Aurora Home", "Bluebird Books", "Cedar & Stone", "Delta Gadgets", "Evergreen Tea", "Fable Toys", "Granite Tools", "Harbor Apparel",
         "Indigo Prints", "Juniper Candles", "Kite Sports", "Lumen Lighting", "Maple Pantry", "Nimbus Audio", "Orchid Beauty", "Pine & Co",
         "Quartz Jewelry", "Raven Leather", "Saffron Spices", "Tidewater Outdoors", "Umber Ceramics", "Velvet Textiles", "Willow Pets",
         "Xylo Music", "Yarrow Garden"]
CITIES = ["12 Oak St, Austin TX", "88 Pine Rd, Denver CO", "5 Lake Ave, Chicago IL", "310 Bay St, Seattle WA", "47 Hill Dr, Boston MA"]


def reset_demo(db: Session, org):
    assert org.mode == "demo", "reset only allowed on demo orgs"
    seller_members = db.query(OrganizationMember).filter_by(org_id=org.id, role="seller").all()
    uids = [m.user_id for m in seller_members]
    for m in seller_members:
        db.delete(m)
    db.flush()
    for t in reversed(Base.metadata.sorted_tables):
        if "org_id" in t.c and t.name not in ("organization_members", "integration_settings"):
            db.execute(t.delete().where(t.c.org_id == org.id))
    for u in db.query(User).filter(User.id.in_(uids)):
        db.delete(u)
    db.flush()


def seed_demo(db: Session, org, actor_id) -> dict:
    reset_demo(db, org)
    rnd, t0 = random.Random(42), now()
    org.currency, org.default_commission_pct = "USD", Decimal("10.00")

    sellers, products = [], {}
    for i, n in enumerate(NAMES):
        st = "active" if i < 22 else "pending" if i < 24 else "suspended"
        s = Seller(org_id=org.id, business_name=n, email=f"sim-{n.split()[0].lower()}@example.com", status=st,
                   commission_pct=Decimal(rnd.choice(["8.00", "12.50", "15.00"])) if i % 6 == 0 else None,
                   paypal_merchant_id=f"SIM-MERCH-{1000 + i}" if st == "active" else None,
                   onboarding_status="completed" if st == "active" else "in_progress" if st == "pending" else "suspended", payment_connected=st == "active")
        db.add(s)
        db.flush()
        sellers.append(s)
        if st == "active":
            db.add(SellerPaypalAccount(org_id=org.id, seller_id=s.id, merchant_id=s.paypal_merchant_id, is_simulated=True))
            products[s.id] = [Product(org_id=org.id, seller_id=s.id, name=f"{n.split()[0]} {w}", price=Decimal(rnd.choice(["19.99", "34.50", "59.00", "89.90", "129.00"])))
                              for w in ("Classic", "Deluxe", "Starter")]
            db.add_all(products[s.id])
    db.flush()
    active = [s for s in sellers if s.status == "active"]

    orders, caps = [], {}
    for n in range(120):
        picks = rnd.sample(active, rnd.choice([1, 1, 1, 2]))
        lines = [(rnd.choice(products[s.id]).id, rnd.choice([1, 1, 2])) for s in picks]
        o = finance.create_order(db, org, f"ORD-{1001 + n}", f"CUST-{rnd.randint(100, 160)}", f"sim-customer{n}@example.com", lines, rnd.choice(CITIES))
        at = t0 - timedelta(days=rnd.uniform(0.2, 45))
        if n < 6:  # failed payments
            o.status = "failed"
            db.add(MarketplaceTransaction(org_id=org.id, order_id=o.id, type="payment", amount=o.total, currency=o.currency, status="failed", is_simulated=True))
        else:
            caps[o.id] = finance.record_payment(db, org, o, f"SIM-CAP-{10000 + n}", fee=q(o.total * Decimal("0.029") + Decimal("0.30")), simulated=True, paypal_order_id=f"SIM-PPO-{n}")
        orders.append((o, at))
    db.flush()
    for o, at in orders:
        _backdate_order(db, org.id, o, at)

    paid = [o for o, _ in orders[6:]]
    # fulfilment records
    ship = {}
    for o in paid[:100]:
        sh = Shipment(org_id=org.id, order_id=o.id, carrier=rnd.choice(["UPS", "FedEx", "USPS"]), tracking_number=f"SIM{rnd.randint(10**9, 10**10)}", status="delivered", address=o.shipping_address)
        db.add(sh)
        db.flush()
        ship[o.id] = sh
        base = o.created_at
        for k, (st, desc, d) in enumerate([("shipped", "Label created, picked up", 1), ("in_transit", "In transit", 3), ("delivered", "Delivered, left at front door", 5)]):
            db.add(TrackingEvent(org_id=org.id, shipment_id=sh.id, status=st, description=desc, occurred_at=base + timedelta(days=d)))
        db.add(CustomerMessage(org_id=org.id, order_id=o.id, direction="outbound", body="Your order has shipped.", sent_at=base + timedelta(days=1)))

    # --- legitimate refunds (completed, ledger-posted) ---
    for k, o in enumerate(paid[10:18]):
        r = Refund(org_id=org.id, order_id=o.id, capture_id=caps[o.id].capture_id, seller_id=db.query(SellerAllocation).filter_by(order_id=o.id).first().seller_id, customer_id=o.customer_id,
                   amount=q(o.total / 2), currency=o.currency, status="pending_approval", reason="Customer return (simulated)", idempotency_key=f"SIM-REFKEY-{k}", is_simulated=True,
                   requested_by=actor_id, approved_by=actor_id, created_at=t0 - timedelta(days=rnd.uniform(0.5, 20)))
        db.add(r)
        db.flush()
        refunds._finalize(db, org, r, f"SIM-REF-{9000 + k}", actor_id)
        _backdate_refund(db, org.id, r)

    # --- 3 potential duplicate refund requests ---
    dup_orders = paid[20:23]
    for k, o in enumerate(dup_orders):
        sid = db.query(SellerAllocation).filter_by(order_id=o.id).first().seller_id
        amt = q(o.total * Decimal("0.4"))  # leaves room so the 2nd request passes hard validation but is flagged as a duplicate
        first = Refund(org_id=org.id, order_id=o.id, capture_id=caps[o.id].capture_id, seller_id=sid, customer_id=o.customer_id, amount=amt, currency=o.currency,
                       status="pending_approval", reason="Item damaged (simulated)", idempotency_key=f"SIM-DUP-A{k}", is_simulated=True, requested_by=actor_id, approved_by=actor_id,
                       created_at=t0 - timedelta(days=3, hours=k))
        db.add(first)
        db.flush()
        refunds._finalize(db, org, first, f"SIM-REF-{9100 + k}", actor_id)
        _backdate_refund(db, org.id, first)
        second = Refund(org_id=org.id, order_id=o.id, capture_id=caps[o.id].capture_id, seller_id=sid, customer_id=o.customer_id, amount=amt, currency=o.currency, status="needs_review",
                        reason="Item damaged (simulated, resubmitted)", idempotency_key=f"SIM-DUP-B{k}", is_simulated=True, requested_by=actor_id, created_at=t0 - timedelta(hours=6 + k))
        db.add(second)
        db.flush()
        refunds.investigate(db, org, second)

    # --- 5 disputes ---
    def dispute(i, o, reason, status, hours, amount=None, link=True, **kw):
        d = Dispute(org_id=org.id, paypal_dispute_id=f"PP-SIM-D-{5000 + i}", order_id=o.id if link else None, seller_id=db.query(SellerAllocation).filter_by(order_id=o.id).first().seller_id,
                    capture_id=caps[o.id].capture_id, reason=reason, status=status, amount=amount or o.total, currency=o.currency,
                    response_due_date=t0 + timedelta(hours=hours), opened_at=t0 - timedelta(days=2), is_simulated=True, raw={"simulated": True})
        db.add(d)
        return d

    d_orders = paid[30:35]
    dispute(1, d_orders[0], "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "waiting_merchant", 24 * 9)           # full evidence
    o2 = d_orders[1]
    db.query(TrackingEvent).filter(TrackingEvent.shipment_id == ship[o2.id].id, TrackingEvent.status == "delivered").delete()
    ship[o2.id].status = "in_transit"
    dispute(2, o2, "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", "waiting_merchant", 30)                        # missing delivery confirmation, urgent
    o3 = d_orders[2]
    for k, (dr, body) in enumerate([("inbound", "The item looks different from the photos."), ("outbound", "Sorry to hear that; the listing matches the product SKU."), ("inbound", "I still want my money back.")]):
        db.add(CustomerMessage(org_id=org.id, order_id=o3.id, direction=dr, body=body, sent_at=o3.created_at + timedelta(days=6, hours=k)))
    dispute(3, o3, "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED", "open", 24 * 7)                            # sufficient
    dispute(4, d_orders[3], "UNAUTHORISED", "waiting_merchant", 72, link=False)                           # order not linked -> needs order info
    o5 = d_orders[4]
    r5 = Refund(org_id=org.id, order_id=o5.id, capture_id=caps[o5.id].capture_id, seller_id=db.query(SellerAllocation).filter_by(order_id=o5.id).first().seller_id, customer_id=o5.customer_id,
                amount=o5.total, currency=o5.currency, status="pending_approval", reason="Full refund (simulated)", idempotency_key="SIM-D5-REF", is_simulated=True, requested_by=actor_id, approved_by=actor_id)
    db.add(r5)
    db.flush()
    refunds._finalize(db, org, r5, "SIM-REF-9200", actor_id)
    dispute(5, o5, "CREDIT_NOT_PROCESSED", "open", 24 * 6)                                               # already refunded -> accept

    # --- 4 unreconciled records ---
    u1 = finance.create_order(db, org, "ORD-9001", "CUST-900", "sim-ghost@example.com", [(products[active[0].id][0].id, 1)], CITIES[0])
    u1.status = "paid"                                                                                   # (1) paid order, no capture
    db.add(PaypalCapture(org_id=org.id, order_id=None, capture_id="SIM-CAP-ORPHAN-1", status="COMPLETED", amount=Decimal("212.40"), currency="USD", is_simulated=True))  # (2) orphan payment
    r_un = Refund(org_id=org.id, order_id=paid[40].id, capture_id=caps[paid[40].id].capture_id, seller_id=active[1].id, customer_id=paid[40].customer_id, amount=Decimal("15.00"), currency="USD",
                  status="completed", idempotency_key="SIM-UNREC-1", is_simulated=True, paypal_refund_id=None, reason="Completed without ledger/PayPal reference (simulated)")
    db.add(r_un)                                                                                         # (3) completed refund with no PayPal id and no ledger entry
    com = db.query(Commission).filter_by(order_id=paid[50].id).first()
    com.amount += Decimal("7.35")                                                                        # (4) tampered commission

    # --- payouts: 6 correct, 2 mismatched, 1 delayed ---
    for idx, s in enumerate(active[2:11]):
        p = finance.create_payout_request(db, org, s, actor_id)
        if not p:
            continue
        p.created_at = t0 - timedelta(days=4)
        if idx == 5:
            p.status = "delayed"
            continue
        if idx in (6, 7):
            p.amount = q(p.amount - Decimal("40.00"))  # mismatched vs settlements it covers
        finance.mark_payout_paid(db, org, p, f"SIM-PO-{7000 + idx}", actor_id)
        p.is_simulated = True
    db.flush()

    # --- seller portal login for the demo ---
    email, pw = f"seller+{org.id.hex[:6]}@example.com", "demo-seller-pass"
    su = User(email=email, name=f"{active[0].business_name} (demo seller)", password_hash=hash_password(pw))
    db.add(su)
    db.flush()
    db.add(OrganizationMember(org_id=org.id, user_id=su.id, role="seller", seller_id=active[0].id))
    finance.notify(db, org.id, "DEMO MODE: simulated data loaded", "All records are simulated and were never sent to PayPal.", "info")
    if not db.query(IntegrationSetting).filter_by(org_id=org.id).first():
        db.add(IntegrationSetting(org_id=org.id, provider="paypal"))
    run = reconcile.run_reconciliation(db, org, actor_id)
    return {"sellers": len(sellers), "orders": len(orders), "disputes": 5, "duplicate_refunds": 3, "mismatches": run.mismatches,
            "seller_login": {"email": email, "password": pw}}


def _backdate_order(db, org_id, order: Order, at):
    order.created_at = at
    for M in (PaypalCapture, MarketplaceTransaction, FinancialLedger):
        db.execute(update(M).where(M.org_id == org_id, M.order_id == order.id).values(created_at=at))
    db.execute(update(SellerSettlement).where(SellerSettlement.allocation_id.in_(db.query(SellerAllocation.id).filter_by(order_id=order.id).scalar_subquery())).values(created_at=at))


def _backdate_refund(db, org_id, r: Refund):
    for M, col in ((FinancialLedger, FinancialLedger.ref_id), (MarketplaceTransaction, MarketplaceTransaction.external_ref)):
        db.execute(update(M).where(M.org_id == org_id, col == (str(r.id) if M is FinancialLedger else r.paypal_refund_id)).values(created_at=r.created_at))
