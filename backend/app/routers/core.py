import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func

from ..auth import Ctx, ctx
from ..models import (AuditLog, Commission, Dispute, MarketplaceTransaction, Notification, Order, OrderItem, PaypalCapture, Payout, Product, Refund,
                      Seller, SellerAllocation, SellerOnboarding, SellerPaypalAccount, SellerSettlement, Shipment, now)
from ..services import agent, finance, paypal, reconcile
from ..util import d, val

router = APIRouter(prefix="/api")
Z = Decimal("0")


def _day_series(rows_):
    by = defaultdict(lambda: Z)
    for r in rows_:
        by[r["at"][:10]] += r["amount"]
    return by


@router.get("/overview")
def overview(c: Ctx = Depends(ctx("dashboard"))):
    db, oid = c.db, c.org.id
    start = now() - timedelta(days=30)
    s = reconcile.summary(db, oid, start, None)
    run, recs = reconcile.latest_run(db, oid)
    checked = run.total_checked if run else 0
    pay, com = _day_series(reconcile.rows(db, oid, "payments", now() - timedelta(days=14))), _day_series(reconcile.rows(db, oid, "commissions", now() - timedelta(days=14)))
    days = [(now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
    risk = agent.get_financial_risk_summary(c)["findings"]
    c.db.commit()  # persists the reconciliation run if the risk summary had to create one
    return {"mode": c.org.mode, "currency": c.org.currency, "summary": s,
            "open_disputes": db.query(func.count(Dispute.id)).filter(Dispute.org_id == oid, Dispute.status.in_(("open", "waiting_merchant", "under_review"))).scalar(),
            "pending_refunds": db.query(func.count(Refund.id)).filter(Refund.org_id == oid, Refund.status.in_(("needs_review", "pending_approval", "escalated"))).scalar(),
            "reconciliation": {"checked": checked, "mismatches": run.mismatches if run else 0, "health_pct": round(100 * (1 - (run.mismatches / checked)), 1) if run and checked else None,
                               "last_run": val(run.started_at) if run else None},
            "chart": [{"day": x[5:], "payments": float(pay[x]), "commission": float(com[x])} for x in days],  # float only for chart rendering
            "alerts": risk,
            "activity": [{"action": a.action, "actor": a.actor_type, "at": val(a.created_at), "entity": a.entity, "details": a.details} for a in db.query(AuditLog).filter_by(org_id=oid).order_by(AuditLog.created_at.desc()).limit(10)]}


# ---------- sellers ----------
class SellerIn(BaseModel):
    business_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    commission_pct: Decimal | None = Field(None, ge=0, le=100)
    commission_fixed: Decimal | None = Field(None, ge=0)
    paypal_merchant_id: str | None = Field(None, max_length=64)


class SellerPatch(BaseModel):
    business_name: str | None = None
    status: str | None = Field(None, pattern="^(pending|active|suspended)$")
    commission_pct: Decimal | None = Field(None, ge=0, le=100)
    commission_fixed: Decimal | None = Field(None, ge=0)
    paypal_merchant_id: str | None = Field(None, max_length=64)


def _seller_row(c, s):
    return {**d(s), "balance": str(finance.seller_balance(c.db, c.org.id, s.id)),
            "effective_commission_pct": str(s.commission_pct if s.commission_pct is not None else c.org.default_commission_pct)}


@router.get("/sellers")
def sellers(c: Ctx = Depends(ctx("sellers.read"))):
    rows = [_seller_row(c, s) for s in c.db.query(Seller).filter_by(org_id=c.org.id).order_by(Seller.business_name)]
    cnt = defaultdict(int)
    for r in rows:
        cnt[r["status"]] += 1
    return {"total": len(rows), "active": cnt["active"], "pending": cnt["pending"], "suspended": cnt["suspended"], "sellers": rows}


@router.post("/sellers")
def add_seller(b: SellerIn, c: Ctx = Depends(ctx("sellers.write"))):
    if (b.commission_pct is not None or b.commission_fixed is not None) and not c.can("commission.write"):
        raise HTTPException(403, "Commission overrides need commission.write")
    s = Seller(org_id=c.org.id, **b.model_dump(), status="pending", onboarding_status="not_started", payment_connected=False)
    c.db.add(s)
    c.db.flush()
    c.audit("seller.created", "seller", s.id)
    c.db.commit()
    return _seller_row(c, s)


@router.get("/sellers/{sid}")
def seller_detail(sid: uuid.UUID, c: Ctx = Depends(ctx("sellers.read"))):
    s = c.db.query(Seller).filter_by(id=sid, org_id=c.org.id).first()
    if not s:
        raise HTTPException(404, "Seller not found")
    return {"seller": _seller_row(c, s), "summary": reconcile.summary(c.db, c.org.id, None, None, s.id),
            "onboarding": [d(o) for o in c.db.query(SellerOnboarding).filter_by(seller_id=s.id)],
            "paypal_accounts": [d(a) for a in c.db.query(SellerPaypalAccount).filter_by(seller_id=s.id)],
            "transactions": [d(a) | {"order_number": c.db.get(Order, a.order_id).number} for a in c.db.query(SellerAllocation).filter_by(seller_id=s.id).order_by(SellerAllocation.order_id).limit(50)],
            "payouts": [d(p) for p in c.db.query(Payout).filter_by(seller_id=s.id).order_by(Payout.created_at.desc())]}


@router.patch("/sellers/{sid}")
def patch_seller(sid: uuid.UUID, b: SellerPatch, c: Ctx = Depends(ctx("sellers.write"))):
    s = c.db.query(Seller).filter_by(id=sid, org_id=c.org.id).first()
    if not s:
        raise HTTPException(404, "Seller not found")
    ch = b.model_dump(exclude_unset=True)
    if ("commission_pct" in ch or "commission_fixed" in ch) and not c.can("commission.write"):
        raise HTTPException(403, "Commission changes need commission.write")
    for k, v in ch.items():
        setattr(s, k, v)
    if "paypal_merchant_id" in ch:
        s.payment_connected = bool(ch["paypal_merchant_id"])
    c.audit("seller.updated", "seller", s.id, {k: str(v) for k, v in ch.items()})  # sensitive: commission changes are audited
    c.db.commit()
    return _seller_row(c, s)


@router.post("/sellers/{sid}/onboard")
def onboard(sid: uuid.UUID, simulate: bool = False, c: Ctx = Depends(ctx("sellers.write"))):
    """Real: PayPal Partner Referrals (needs approved PayPal partner credentials). Otherwise an honestly-labelled simulation."""
    s = c.db.query(Seller).filter_by(id=sid, org_id=c.org.id).first()
    if not s:
        raise HTTPException(404, "Seller not found")
    tid = f"mp-{s.id.hex[:16]}"
    if simulate or c.org.mode == "demo":
        mid = f"SIM-MERCH-{uuid.uuid4().hex[:6].upper()}"
        c.db.add(SellerOnboarding(org_id=c.org.id, seller_id=s.id, tracking_id=tid, status="completed", is_simulated=True))
        c.db.add(SellerPaypalAccount(org_id=c.org.id, seller_id=s.id, merchant_id=mid, is_simulated=True))
        s.paypal_merchant_id, s.onboarding_status, s.payment_connected, s.status = mid, "completed", True, "active"
        c.audit("seller.onboarding_simulated", "seller", s.id)
        c.db.commit()
        return {"simulated": True, "message": "SIMULATED onboarding. No PayPal account was created."}
    try:
        r = paypal.get_client().create_partner_referral(tid, "http://localhost:3000/sellers")
    except paypal.PayPalError as e:
        raise HTTPException(502, f"PayPal Partner Referrals unavailable: {e}. This needs PayPal partner approval; retry with simulate=true for a labelled simulation.")
    link = next((l["href"] for l in r.get("links", []) if l.get("rel") == "action_url"), None)
    c.db.add(SellerOnboarding(org_id=c.org.id, seller_id=s.id, tracking_id=tid, status="started", action_url=link))
    s.onboarding_status = "in_progress"
    c.audit("seller.onboarding_started", "seller", s.id)
    c.db.commit()
    return {"simulated": False, "action_url": link}


# ---------- products & orders ----------
@router.get("/products")
def products(c: Ctx = Depends(ctx("orders.write"))):
    return [d(p) | {"seller": c.db.get(Seller, p.seller_id).business_name} for p in c.db.query(Product).filter_by(org_id=c.org.id)]


class ProductIn(BaseModel):
    seller_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


@router.post("/products")
def add_product(b: ProductIn, c: Ctx = Depends(ctx("sellers.write"))):
    if not c.db.query(Seller).filter_by(id=b.seller_id, org_id=c.org.id).first():
        raise HTTPException(404, "Seller not found")
    p = Product(org_id=c.org.id, **b.model_dump())
    c.db.add(p)
    c.db.commit()
    return d(p)


class Line(BaseModel):
    product_id: uuid.UUID
    qty: int = Field(1, ge=1, le=1000)


class OrderIn(BaseModel):
    customer_id: str = Field(max_length=64)
    customer_email: EmailStr
    address: str | None = Field(None, max_length=255)
    lines: list[Line] = Field(min_length=1, max_length=50)


def next_number(c: Ctx) -> str:
    n = c.db.query(func.count(Order.id)).filter_by(org_id=c.org.id).scalar() or 0
    return f"ORD-{2000 + n + 1}"


@router.post("/orders")
def create_order(b: OrderIn, c: Ctx = Depends(ctx("orders.write"))):
    try:
        o = finance.create_order(c.db, c.org, next_number(c), b.customer_id, b.customer_email, [(l.product_id, l.qty) for l in b.lines], b.address)
    except ValueError as e:
        c.db.rollback()
        raise HTTPException(422, str(e))
    c.audit("order.created", "order", o.id, {"total": str(o.total)})
    c.db.commit()
    return order_detail(o.id, c)


@router.get("/orders")
def orders(c: Ctx = Depends(ctx("transactions.read"))):
    out = []
    for o in c.db.query(Order).filter_by(org_id=c.org.id).order_by(Order.created_at.desc()).limit(300):
        cap = c.db.query(PaypalCapture).filter_by(order_id=o.id).first()
        out.append(d(o) | {"capture_id": cap.capture_id if cap else None, "simulated": cap.is_simulated if cap else None})
    return out


@router.get("/orders/{oid}")
def order_detail(oid: uuid.UUID, c: Ctx = Depends(ctx("transactions.read"))):
    o = c.db.query(Order).filter_by(id=oid, org_id=c.org.id).first()
    if not o:
        raise HTTPException(404, "Order not found")
    names = {s.id: s.business_name for s in c.db.query(Seller).filter_by(org_id=c.org.id)}
    return {"order": d(o), "items": [d(i) | {"seller": names.get(i.seller_id)} for i in c.db.query(OrderItem).filter_by(order_id=o.id)],
            "allocations": [d(a) | {"seller": names.get(a.seller_id)} for a in c.db.query(SellerAllocation).filter_by(order_id=o.id)],
            "commissions": [d(x) for x in c.db.query(Commission).filter_by(order_id=o.id)],
            "captures": [d(x) for x in c.db.query(PaypalCapture).filter_by(order_id=o.id)],
            "refunds": [d(r) for r in c.db.query(Refund).filter_by(order_id=o.id)],
            "shipments": [d(s) for s in c.db.query(Shipment).filter_by(order_id=o.id)]}


@router.post("/orders/{oid}/simulate-payment")
def simulate_payment(oid: uuid.UUID, c: Ctx = Depends(ctx("orders.write"))):
    if c.org.mode != "demo":
        raise HTTPException(409, "Simulated payments are only available in Demo mode. Use the PayPal Sandbox flow instead.")
    o = c.db.query(Order).filter_by(id=oid, org_id=c.org.id).first()
    if not o or o.status != "created":
        raise HTTPException(409, "Order not found or already processed")
    finance.record_payment(c.db, c.org, o, f"SIM-CAP-{uuid.uuid4().hex[:8].upper()}", fee=finance.q(o.total * Decimal("0.029") + Decimal("0.30")), simulated=True, actor_id=c.user.id)
    c.audit("payment.simulated", "order", o.id)
    c.db.commit()
    return order_detail(o.id, c)


@router.get("/transactions")
def transactions(type: str | None = None, c: Ctx = Depends(ctx("transactions.read"))):
    qy = c.db.query(MarketplaceTransaction).filter_by(org_id=c.org.id)
    if type:
        qy = qy.filter_by(type=type)
    return [d(t) for t in qy.order_by(MarketplaceTransaction.created_at.desc()).limit(300)]


# ---------- SmartSplit ----------
@router.get("/smartsplit")
def smartsplit(c: Ctx = Depends(ctx("sellers.read"))):
    names = {s.id: s.business_name for s in c.db.query(Seller).filter_by(org_id=c.org.id)}
    st = c.db.query(SellerSettlement.status, func.count(), func.coalesce(func.sum(SellerSettlement.amount), 0)).filter_by(org_id=c.org.id).group_by(SellerSettlement.status).all()
    return {"default_commission_pct": str(c.org.default_commission_pct),
            "settlements": {s: {"count": n, "amount": str(finance.q(a))} for s, n, a in st},
            "allocations": [d(a) | {"seller": names.get(a.seller_id), "order_number": c.db.get(Order, a.order_id).number} for a in c.db.query(SellerAllocation).filter_by(org_id=c.org.id).order_by(SellerAllocation.id).limit(100)],
            "payouts": [d(p) | {"seller": names.get(p.seller_id)} for p in c.db.query(Payout).filter_by(org_id=c.org.id).order_by(Payout.created_at.desc()).limit(100)]}


class PayoutReq(BaseModel):
    seller_id: uuid.UUID


@router.post("/payouts/request")
def request_payout(b: PayoutReq, c: Ctx = Depends(ctx("payouts.approve"))):
    s = c.db.query(Seller).filter_by(id=b.seller_id, org_id=c.org.id).first()
    if not s:
        raise HTTPException(404, "Seller not found")
    p = finance.create_payout_request(c.db, c.org, s, c.user.id)
    if not p:
        raise HTTPException(409, "Nothing payable: no pending settlements or non-positive balance")
    c.audit("payout.requested", "payout", p.id, {"amount": str(p.amount)})
    c.db.commit()
    return d(p)


@router.post("/payouts/{pid}/approve")
def approve_payout(pid: uuid.UUID, c: Ctx = Depends(ctx("payouts.approve"))):
    """Sensitive: money leaves the platform only through this explicit human approval."""
    p = c.db.query(Payout).filter_by(id=pid, org_id=c.org.id).with_for_update().first()
    if not p or p.status != "pending_approval":
        raise HTTPException(409, "Payout not found or not awaiting approval")
    s = c.db.get(Seller, p.seller_id)
    c.audit("payout.approved", "payout", p.id, {"amount": str(p.amount), "seller": s.business_name})
    if c.org.mode == "demo":
        finance.mark_payout_paid(c.db, c.org, p, f"SIM-PO-{uuid.uuid4().hex[:8].upper()}", c.user.id)
    else:
        try:
            r = paypal.get_client().create_payout(f"mp-{p.id.hex}", s.email, p.amount, p.currency, "Marketplace payout")
        except paypal.PayPalError as e:
            p.status = "failed"
            c.db.commit()
            raise HTTPException(502, f"PayPal Payouts failed: {e}")
        p.reference, p.approved_by, p.status = r.get("batch_header", {}).get("payout_batch_id"), c.user.id, "processing"  # settled when PayPal reports SUCCESS (webhook)
    c.db.commit()
    return d(p)


# ---------- seller portal (strictly scoped to the member's own seller_id) ----------
@router.get("/portal")
def portal(c: Ctx = Depends(ctx("portal.read"))):
    sid = c.seller_scope
    if not sid:
        raise HTTPException(403, "Not a seller account")
    s = c.db.get(Seller, sid)
    allocs = c.db.query(SellerAllocation).filter_by(seller_id=sid).order_by(SellerAllocation.id).limit(100).all()
    return {"seller": {"business_name": s.business_name, "status": s.status, "commission_pct": str(s.commission_pct if s.commission_pct is not None else c.org.default_commission_pct)},
            "balance": str(finance.seller_balance(c.db, c.org.id, sid)), "summary": reconcile.summary(c.db, c.org.id, None, None, sid), "currency": c.org.currency,
            "transactions": [{"order_number": c.db.get(Order, a.order_id).number, "gross": str(a.gross), "commission": str(a.commission), "net": str(a.net)} for a in allocs],
            "payouts": [{"amount": str(p.amount), "status": p.status, "reference": p.reference, "at": val(p.created_at)} for p in c.db.query(Payout).filter_by(seller_id=sid).order_by(Payout.created_at.desc())],
            "refunds": [{"amount": str(r.amount), "status": r.status, "at": val(r.created_at)} for r in c.db.query(Refund).filter_by(seller_id=sid).order_by(Refund.created_at.desc())],
            "disputes": [{"id": x.paypal_dispute_id, "reason": x.reason, "status": x.status, "amount": str(x.amount), "due": val(x.response_due_date)} for x in c.db.query(Dispute).filter_by(seller_id=sid)]}


# ---------- notifications / audit ----------
@router.get("/notifications")
def notifications(c: Ctx = Depends(ctx("notifications.read"))):
    return [d(n) for n in c.db.query(Notification).filter_by(org_id=c.org.id).order_by(Notification.created_at.desc()).limit(100)]


@router.post("/notifications/read-all")
def read_all(c: Ctx = Depends(ctx("notifications.read"))):
    c.db.query(Notification).filter_by(org_id=c.org.id, read=False).update({"read": True})
    c.db.commit()
    return {"ok": True}


@router.get("/audit")
def audit(c: Ctx = Depends(ctx("audit.read"))):
    return [d(a) for a in c.db.query(AuditLog).filter_by(org_id=c.org.id).order_by(AuditLog.created_at.desc()).limit(200)]


# ---------- notification preferences ----------
PREF_KEYS = ("refund_flags", "dispute_deadlines", "recon_mismatches", "payout_issues")


@router.get("/settings/notifications")
def get_prefs(c: Ctx = Depends(ctx("settings.manage"))):
    from ..models import IntegrationSetting
    s = c.db.query(IntegrationSetting).filter_by(org_id=c.org.id).first()
    return {k: (s.notification_prefs or {}).get(k, True) if s else True for k in PREF_KEYS}


@router.put("/settings/notifications")
def put_prefs(b: dict[str, bool], c: Ctx = Depends(ctx("settings.manage"))):
    from ..models import IntegrationSetting
    s = c.db.query(IntegrationSetting).filter_by(org_id=c.org.id).first() or IntegrationSetting(org_id=c.org.id, provider="paypal")
    s.notification_prefs = {k: bool(b.get(k, True)) for k in PREF_KEYS}  # unknown keys are dropped
    c.db.add(s)
    c.audit("settings.notifications_updated", details=s.notification_prefs)
    c.db.commit()
    return s.notification_prefs
