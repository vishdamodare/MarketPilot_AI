"""RefundGuard. Deterministic validation FIRST, anomaly rules SECOND, optional LLM narrative LAST.
The AI never approves, rejects or labels a customer fraudulent; every refund needs explicit human approval.
"""
import statistics
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import Ctx
from ..models import (MarketplaceTransaction, Order, OrderItem, PaypalCapture, Refund, RefundInvestigation, now)
from . import finance, llm, paypal

COMPLETED = ("completed",)
IN_FLIGHT = ("requested", "needs_review", "pending_approval", "escalated", "processing")


class RefundError(Exception):
    def __init__(self, message: str, checks: list | None = None, status_code: int = 422):
        super().__init__(message)
        self.checks, self.status_code = checks or [], status_code


def _sum(db: Session, org_id, order_id, statuses, exclude=None) -> Decimal:
    qy = db.query(func.coalesce(func.sum(Refund.amount), 0)).filter(Refund.org_id == org_id, Refund.order_id == order_id, Refund.status.in_(statuses))
    if exclude:
        qy = qy.filter(Refund.id != exclude)
    return finance.q(qy.scalar())


def validate(db: Session, org_id, order: Order | None, amount: Decimal, *, currency: str | None = None, exclude_refund_id=None) -> dict:
    """Hard checks (blocking) + anomaly flags (non-blocking). Pure read; safe to call repeatedly."""
    checks, flags = [], []

    def chk(name, ok, detail):
        checks.append({"name": name, "passed": bool(ok), "detail": detail})
        return ok

    cap = None
    if chk("original_order_exists", order is not None, "Order found" if order else "No such order in this organization"):
        cap = (db.query(PaypalCapture).filter_by(org_id=org_id, order_id=order.id).order_by(PaypalCapture.created_at).first())
    chk("original_payment_exists", cap is not None, f"Capture {cap.capture_id}" if cap else "No captured payment for this order")
    chk("payment_eligible", bool(cap) and cap.status in ("COMPLETED", "PARTIALLY_REFUNDED"), f"Payment status {cap.status}" if cap else "n/a")
    chk("amount_valid", amount > 0 and amount == finance.q(amount), f"Requested {amount}")
    done = pend = Decimal("0")
    if order and cap:
        done = _sum(db, org_id, order.id, COMPLETED, exclude_refund_id)
        pend = _sum(db, org_id, order.id, IN_FLIGHT, exclude_refund_id)
        chk("currency_matches", currency in (None, cap.currency), f"Payment currency {cap.currency}")
        chk("cumulative_within_refundable", done + amount <= cap.amount,
            f"Already refunded {done} + requested {amount} vs captured {cap.amount} (refundable {cap.amount - done})")
    blocking = [c for c in checks if not c["passed"]]

    if order and cap and not blocking:
        others = db.query(Refund).filter(Refund.org_id == org_id, Refund.order_id == order.id, Refund.status != "rejected")
        if exclude_refund_id:
            others = others.filter(Refund.id != exclude_refund_id)
        others = others.all()
        dup = [r for r in others if r.amount == amount]
        if dup:
            flags.append({"code": "potential_duplicate", "severity": "high",
                          "detail": f"{len(dup)} other refund(s) on this order for the identical amount {amount} (ids: {', '.join(str(r.id)[:8] for r in dup)})"})
        if len(others) >= 1 and not dup:
            flags.append({"code": "multiple_attempts", "severity": "medium", "detail": f"{len(others)} other refund record(s) already exist for this order"})
        if pend:
            flags.append({"code": "in_flight_refunds", "severity": "medium", "detail": f"{pend} already requested/pending on this order; total exposure {done + pend + amount} of {cap.amount}"})
        hist = [r[0] for r in db.query(Refund.amount).filter(Refund.org_id == org_id, Refund.status == "completed").all()]
        if len(hist) >= 5 and amount > 3 * statistics.median(hist):
            flags.append({"code": "unusually_large", "severity": "medium", "detail": f"{amount} is over 3x the median completed refund ({statistics.median(hist)})"})
        since = now() - timedelta(days=30)
        n = db.query(func.count(Refund.id)).filter(Refund.org_id == org_id, Refund.customer_id == order.customer_id, Refund.created_at >= since).scalar()
        if n >= 3:
            flags.append({"code": "suspicious_frequency", "severity": "medium", "detail": f"Customer {order.customer_id} has {n} refund requests in 30 days (pattern worth a human look; not evidence of wrongdoing)"})
        if cap.order_id != order.id:
            flags.append({"code": "inconsistent_reference", "severity": "high", "detail": "Capture is linked to a different order"})
    risk = "high" if any(f["severity"] == "high" for f in flags) else "medium" if flags else "low"
    return {"checks": checks, "blocking": blocking, "flags": flags, "risk": risk, "capture_id": cap.capture_id if cap else None,
            "refundable": str(cap.amount - done) if cap else None, "captured": str(cap.amount) if cap else None}


def request_refund(c: Ctx, order_id: uuid.UUID, amount: Decimal, reason: str | None, idempotency_key: str) -> Refund:
    db = c.db
    existing = db.query(Refund).filter_by(org_id=c.org.id, idempotency_key=idempotency_key).first()
    if existing:
        return existing  # idempotent replay
    order = db.query(Order).filter_by(id=order_id, org_id=c.org.id).first()  # ids & seller/customer derived server-side
    v = validate(db, c.org.id, order, finance.q(amount))
    if v["blocking"]:
        raise RefundError("Refund failed validation: " + "; ".join(b["detail"] for b in v["blocking"]), v["checks"])
    seller = db.query(OrderItem.seller_id).filter_by(order_id=order.id).first()
    r = Refund(org_id=c.org.id, order_id=order.id, capture_id=v["capture_id"], seller_id=seller[0] if seller else None,
               customer_id=order.customer_id, amount=finance.q(amount), currency=order.currency, reason=reason,
               status="needs_review" if v["flags"] else "pending_approval", idempotency_key=idempotency_key,
               requested_by=c.user.id, is_simulated=c.org.mode == "demo")
    db.add(r)
    db.flush()
    c.audit("refund.requested", "refund", r.id, {"amount": str(r.amount), "risk": v["risk"]})
    if v["flags"]:
        investigate(db, c.org, r)
    return r


def investigate(db: Session, org, refund: Refund) -> RefundInvestigation:
    order = db.get(Order, refund.order_id)
    v = validate(db, org.id, order, refund.amount, exclude_refund_id=refund.id)
    if v["blocking"]:  # e.g. exceeds refundable amount: that is itself a high-risk finding
        v["flags"] = [{"code": "fails_validation", "severity": "high", "detail": b["detail"]} for b in v["blocking"]]
        v["risk"] = "high"
    history = [{"refund_id": str(r.id), "amount": str(r.amount), "status": r.status, "created_at": r.created_at.isoformat()}
               for r in db.query(Refund).filter(Refund.org_id == org.id, Refund.order_id == refund.order_id, Refund.id != refund.id).order_by(Refund.created_at)]
    reason = "; ".join(f["detail"] for f in v["flags"]) or "No anomalies detected by deterministic rules."
    action = ("Hold: verify with the customer/PSP whether this is a duplicate of the earlier refund before approving."
              if any(f["code"] == "potential_duplicate" for f in v["flags"]) else
              "Review the flagged items; approve if the refund is legitimate, otherwise reject the internal request." if v["flags"] else
              "Standard approval.")
    report = {"original_transaction_id": v["capture_id"], "order_id": str(order.id), "order_number": order.number,
              "seller_id": str(refund.seller_id), "customer_id": refund.customer_id,
              "original_amount": v["captured"], "previously_refunded": str(_sum(db, org.id, order.id, COMPLETED, refund.id)),
              "requested_amount": str(refund.amount), "refund_history": history, "checks": v["checks"], "flags": v["flags"],
              "reason": reason, "disclaimer": "Anomaly signals are for human review. They are not a finding of fraud."}
    report["ai_summary"] = llm.complete(
        "You summarise refund anomaly findings for a finance reviewer in <=60 words. Use ONLY the provided JSON. Never accuse the customer of fraud and never decide to approve or reject.",
        str(report)) or f"Risk {v['risk']}. {reason}"
    inv = db.query(RefundInvestigation).filter_by(refund_id=refund.id).first()
    if inv:
        inv.risk_level, inv.report, inv.recommended_action = v["risk"], report, action
    else:
        inv = RefundInvestigation(org_id=org.id, refund_id=refund.id, risk_level=v["risk"], report=report, recommended_action=action)
        db.add(inv)
    if v["risk"] != "low":
        finance.notify(db, org.id, f"Refund needs review ({v['risk']} risk)", f"Order {order.number}: {reason}"[:400], "warning" if v["risk"] == "medium" else "critical", "/refundguard")
    db.flush()
    return inv


def _finalize(db: Session, org, refund: Refund, paypal_refund_id: str, actor_id):
    """Idempotent: ledger is posted exactly once, when the refund first reaches completed."""
    if refund.status == "completed":
        return
    order = db.get(Order, refund.order_id)
    refund.status, refund.paypal_refund_id = "completed", paypal_refund_id
    finance.post_refund_ledger(db, org, order, refund.amount, refund.id, actor_id)
    db.add(MarketplaceTransaction(org_id=org.id, order_id=order.id, seller_id=refund.seller_id, type="refund", amount=refund.amount,
                                  currency=refund.currency, status="completed", external_ref=paypal_refund_id, is_simulated=refund.is_simulated))
    cap = db.query(PaypalCapture).filter_by(org_id=org.id, capture_id=refund.capture_id).first()
    total = _sum(db, org.id, order.id, COMPLETED)
    if cap:
        cap.status = "REFUNDED" if total >= cap.amount else "PARTIALLY_REFUNDED"
    order.status = "refunded" if cap and total >= cap.amount else order.status


def approve_refund(c: Ctx, refund_id: uuid.UUID) -> Refund:
    db = c.db
    # row lock serialises concurrent approvals for the same order (no-op on SQLite)
    r = db.query(Refund).filter_by(id=refund_id, org_id=c.org.id).with_for_update().first()
    if not r:
        raise RefundError("Refund not found", status_code=404)
    if r.status not in ("needs_review", "pending_approval", "escalated"):
        raise RefundError(f"Refund is '{r.status}' and cannot be approved", status_code=409)
    db.query(Order).filter_by(id=r.order_id).with_for_update().first()
    order = db.get(Order, r.order_id)
    v = validate(db, c.org.id, order, r.amount, exclude_refund_id=r.id)  # re-validate at execution time
    if v["blocking"]:
        raise RefundError("Re-validation failed: " + "; ".join(b["detail"] for b in v["blocking"]), v["checks"], 409)
    r.approved_by = c.user.id
    c.audit("refund.approved", "refund", r.id, {"amount": str(r.amount), "simulated": r.is_simulated})
    if c.org.mode == "demo":
        _finalize(db, c.org, r, f"SIM-REF-{uuid.uuid4().hex[:10].upper()}", c.user.id)
        return r
    pp = paypal.get_client()
    try:
        res = pp.refund_capture(r.capture_id, r.amount, r.currency, request_id=r.idempotency_key, note=r.reason)
        # PayPal is the source of truth: fetch the refund's real status instead of trusting our own assumption
        actual = pp.get_refund(res["id"]).get("status", res.get("status"))
    except paypal.PayPalError as e:
        r.status = "failed"
        c.audit("refund.failed", "refund", r.id, {"error": str(e)[:200]})
        raise RefundError(f"PayPal refund failed: {e}", status_code=502)
    if actual == "COMPLETED":
        _finalize(db, c.org, r, res["id"], c.user.id)
    elif actual in ("PENDING",):
        r.status, r.paypal_refund_id = "processing", res["id"]
    else:
        r.status, r.paypal_refund_id = "failed", res["id"]
    return r


def reject_refund(c: Ctx, refund_id: uuid.UUID, note: str | None) -> Refund:
    r = c.db.query(Refund).filter_by(id=refund_id, org_id=c.org.id).with_for_update().first()
    if not r:
        raise RefundError("Refund not found", status_code=404)
    if r.status not in ("needs_review", "pending_approval", "escalated"):
        raise RefundError(f"Refund is '{r.status}' and cannot be rejected", status_code=409)
    r.status = "rejected"  # rejects the INTERNAL request only; nothing is sent to PayPal
    inv = c.db.query(RefundInvestigation).filter_by(refund_id=r.id).first()
    if inv:
        inv.status = "closed"
    c.audit("refund.rejected", "refund", r.id, {"note": note})
    return r


def escalate_refund(c: Ctx, refund_id: uuid.UUID) -> Refund:
    r = c.db.query(Refund).filter_by(id=refund_id, org_id=c.org.id).first()
    if not r or r.status not in ("needs_review", "pending_approval"):
        raise RefundError("Refund cannot be escalated", status_code=409)
    r.status = "escalated"
    inv = c.db.query(RefundInvestigation).filter_by(refund_id=r.id).first() or investigate(c.db, c.org, r)
    inv.status = "escalated"
    c.audit("refund.escalated", "refund", r.id)
    return r
