import json
import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import Ctx, ctx
from ..config import get_settings
from ..db import get_db
from ..models import (AuditLog, IntegrationSetting, Order, Organization, PaypalCapture, PaypalOrder, Payout, Refund, SellerAllocation, Seller,
                      WebhookEvent, Commission, now)
from ..services import disputes as dsp
from ..services import finance, paypal
from ..services import refunds as rfd
from ..util import d, val

router = APIRouter(prefix="/api")
log = logging.getLogger("paypal-router")


def _setting(db, org_id) -> IntegrationSetting:
    s = db.query(IntegrationSetting).filter_by(org_id=org_id, provider="paypal").first()
    if not s:
        s = IntegrationSetting(org_id=org_id, provider="paypal")
        db.add(s)
        db.flush()
    return s


def _sandbox_only(c: Ctx):
    if c.org.mode != "sandbox":
        raise HTTPException(409, "This action calls PayPal and is only available in a Sandbox organization. Demo organizations use simulated flows.")


@router.get("/integrations/paypal/status")
def status(c: Ctx = Depends(ctx("dashboard"))):
    s, st = get_settings(), _setting(c.db, c.org.id)
    c.db.commit()
    return {"org_mode": c.org.mode, "configured": s.paypal_configured,
            "connected": bool(s.paypal_configured and st.last_sync_at and not st.last_error),
            "environment": "sandbox" if s.paypal_is_sandbox else "LIVE", "base_url": s.paypal_base_url,
            "last_sync_at": val(st.last_sync_at), "last_error": st.last_error,
            "webhook": {"webhook_id_configured": bool(s.paypal_webhook_id), "last_event_at": val(st.last_webhook_at), "url_path": f"/api/webhooks/paypal/{c.org.id}"},
            "multiparty_ready": bool(s.paypal_partner_id),
            "eligibility_notes": [
                "Multiparty payments / partner fees / seller onboarding need PayPal partner approval; without it orders are platform-collected and commissions are tracked internally.",
                "Availability of marketplace features differs by country (including India); confirm with PayPal before going live.",
                "Demo organizations never call PayPal; their records are labelled simulated."]}


@router.post("/integrations/paypal/test")
def test_connection(c: Ctx = Depends(ctx("integrations.manage"))):
    st = _setting(c.db, c.org.id)
    try:
        paypal.PayPalClient._token = None
        paypal.get_client()._auth()
        st.last_sync_at, st.last_error = now(), None
        ok = True
    except paypal.PayPalError as e:
        st.last_error, ok = str(e)[:300], False
    c.audit("integration.paypal_tested", details={"ok": ok})
    c.db.commit()
    return {"ok": ok, "error": st.last_error}


class OrderRef(BaseModel):
    order_id: uuid.UUID


@router.post("/paypal/orders")
def create_paypal_order(b: OrderRef, c: Ctx = Depends(ctx("orders.write"))):
    _sandbox_only(c)
    o = c.db.query(Order).filter_by(id=b.order_id, org_id=c.org.id).first()
    if not o or o.status != "created":
        raise HTTPException(409, "Order not found or already processed")
    allocs = c.db.query(SellerAllocation).filter_by(order_id=o.id).all()
    seller = c.db.get(Seller, allocs[0].seller_id) if len(allocs) == 1 else None  # multiparty orders here are single-seller
    fee = allocs[0].commission if seller else None
    try:
        r = paypal.get_client().create_order(amount=o.total, currency=o.currency, reference_id=o.number, request_id=f"mp-order-{o.id}",
                                             seller_merchant_id=seller.paypal_merchant_id if seller else None, platform_fee=fee)
    except paypal.PayPalError as e:
        _setting(c.db, c.org.id).last_error = str(e)[:300]
        c.db.commit()
        raise HTTPException(502, f"PayPal error: {e}")
    link = next((l["href"] for l in r.get("links", []) if l.get("rel") in ("approve", "payer-action")), None)
    c.db.add(PaypalOrder(org_id=c.org.id, order_id=o.id, paypal_order_id=r["id"], status=r.get("status", "CREATED"), approve_url=link))
    st = _setting(c.db, c.org.id)
    st.last_sync_at, st.last_error = now(), None
    multiparty = bool(seller and seller.paypal_merchant_id and get_settings().paypal_partner_id)
    c.audit("paypal.order_created", "order", o.id, {"paypal_order_id": r["id"], "multiparty": multiparty})
    c.db.commit()
    return {"paypal_order_id": r["id"], "approve_url": link, "mode": "multiparty" if multiparty else "platform_collected",
            "note": "Open approve_url with a PayPal sandbox buyer account, then call capture."}


@router.post("/paypal/orders/{paypal_order_id}/capture")
def capture(paypal_order_id: str, c: Ctx = Depends(ctx("orders.write"))):
    _sandbox_only(c)
    po = c.db.query(PaypalOrder).filter_by(org_id=c.org.id, paypal_order_id=paypal_order_id).first()
    o = c.db.get(Order, po.order_id) if po else None
    if not o:
        raise HTTPException(404, "Unknown PayPal order")
    try:
        r = paypal.get_client().capture_order(paypal_order_id, request_id=f"mp-cap-{paypal_order_id}")
    except paypal.PayPalError as e:
        raise HTTPException(502, f"PayPal capture failed: {e}")
    cap = r["purchase_units"][0]["payments"]["captures"][0]
    if Decimal(cap["amount"]["value"]) != o.total:
        raise HTTPException(409, f"PayPal captured {cap['amount']['value']} but the order total is {o.total}; investigate in MoneyMatch.")
    fee = Decimal(cap.get("seller_receivable_breakdown", {}).get("paypal_fee", {}).get("value", "0"))
    finance.record_payment(c.db, c.org, o, cap["id"], fee=fee, simulated=False, paypal_order_id=paypal_order_id, status=cap.get("status", "COMPLETED"), actor_id=c.user.id)
    po.status = r.get("status", "COMPLETED")
    st = _setting(c.db, c.org.id)
    st.last_sync_at, st.last_error = now(), None
    c.audit("paypal.captured", "order", o.id, {"capture_id": cap["id"]})
    c.db.commit()
    return {"capture_id": cap["id"], "status": cap.get("status"), "order_status": o.status}


@router.get("/paypal/transactions")
def transaction_search(days: int = 7, c: Ctx = Depends(ctx("reconcile.run"))):
    """PayPal Transaction Search, cross-checked against our captures. Shows PayPal transactions we have no record of."""
    _sandbox_only(c)
    days = max(1, min(days, 31))  # PayPal limits the search window
    try:
        tx = paypal.get_client().search_transactions(now() - timedelta(days=days), now())
    except paypal.PayPalError as e:
        raise HTTPException(502, f"PayPal Transaction Search failed: {e}")
    known = {x[0] for x in c.db.query(PaypalCapture.capture_id).filter_by(org_id=c.org.id)}
    rows = [t.get("transaction_info", {}) for t in tx]
    return {"count": len(rows), "unmatched": [{"id": t.get("transaction_id"), "amount": t.get("transaction_amount"), "status": t.get("transaction_status")} for t in rows if t.get("transaction_id") not in known]}


# ---------- webhooks ----------
def verify_event(headers: dict, event: dict) -> bool:  # separate function so tests can substitute it
    return paypal.get_client().verify_webhook(headers, event)


def process_event(db: Session, org: Organization, ev: dict):
    t, res = ev.get("event_type", ""), ev.get("resource", {})
    if t in ("PAYMENT.CAPTURE.COMPLETED", "PAYMENT.CAPTURE.PENDING", "PAYMENT.CAPTURE.DENIED"):
        cap = db.query(PaypalCapture).filter_by(org_id=org.id, capture_id=res.get("id")).first()
        status = {"COMPLETED": "COMPLETED", "PENDING": "PENDING", "DENIED": "DECLINED"}[t.rsplit(".", 1)[1]]
        if cap:
            cap.status = status
        else:  # capture we did not initiate (e.g. buyer approved on another device): link through the PayPal order
            po = db.query(PaypalOrder).filter_by(org_id=org.id, paypal_order_id=res.get("supplementary_data", {}).get("related_ids", {}).get("order_id")).first()
            o = db.get(Order, po.order_id) if po else None
            if o and Decimal(res["amount"]["value"]) == o.total:
                finance.record_payment(db, org, o, res["id"], simulated=False, status=status, paypal_order_id=po.paypal_order_id)
    elif t in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.REFUND.COMPLETED"):
        r = db.query(Refund).filter_by(org_id=org.id, paypal_refund_id=res.get("id")).first()
        if r and r.status == "processing":
            rfd._finalize(db, org, r, res["id"], None)  # idempotent
    elif t.startswith("CUSTOMER.DISPUTE."):
        did = res.get("dispute_id")
        if did:
            dsp.upsert_dispute(db, org, paypal.get_client().get_dispute(did))
    elif t.startswith("PAYMENT.PAYOUTSBATCH."):
        bid = res.get("batch_header", {}).get("payout_batch_id")
        p = db.query(Payout).filter_by(org_id=org.id, reference=bid).first()
        if p and p.status == "processing":
            if t.endswith("SUCCESS"):
                finance.mark_payout_paid(db, org, p, bid, None)
            elif t.endswith("DENIED"):
                p.status = "failed"


@router.post("/webhooks/paypal/{org_id}")
async def webhook(org_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    org = db.get(Organization, org_id)
    if not org or org.mode != "sandbox":
        raise HTTPException(404, "Unknown webhook target")
    raw = await request.body()
    try:
        ev = json.loads(raw)
        eid = ev["id"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Malformed event")
    if not verify_event(dict(request.headers), ev):  # authenticity first; unverified events are never processed
        db.add(AuditLog(org_id=org.id, actor_type="webhook", action="webhook.rejected_unverified", details={"event_id": str(eid)[:64]}))
        db.commit()
        raise HTTPException(400, "Webhook signature verification failed")
    row = WebhookEvent(org_id=org.id, event_id=eid, event_type=ev.get("event_type", "?"), verified=True, payload=ev)
    db.add(row)
    try:
        db.flush()  # UNIQUE(event_id) is the dedupe: a redelivery fails here
    except IntegrityError:
        db.rollback()
        return {"status": "duplicate_ignored"}
    try:
        with db.begin_nested():
            process_event(db, org, ev)
        row.processed_at = now()
    except Exception as e:  # keep the event for inspection, tell PayPal we got it (it retries on non-2xx)
        row.error = f"{type(e).__name__}: {str(e)[:200]}"
        log.exception("webhook processing failed")
    _setting(db, org.id).last_webhook_at = now()
    db.add(AuditLog(org_id=org.id, actor_type="webhook", action="webhook.received", entity="webhook_event", entity_id=eid, details={"type": row.event_type, "error": row.error}))
    db.commit()
    return {"status": "ok"}


@router.get("/webhooks/events")
def events(c: Ctx = Depends(ctx("integrations.manage"))):
    return [d(w, "payload") for w in c.db.query(WebhookEvent).filter_by(org_id=c.org.id).order_by(WebhookEvent.received_at.desc()).limit(50)]
