"""MoneyMatch. Every number comes from `rows()`; totals are the SUM of the traced rows, so any figure can be drilled
into its source records. The LLM never computes or invents totals; it only calls these functions.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import (Commission, Dispute, FinancialLedger, Order, PaypalCapture, Payout, ReconciliationRecord, ReconciliationRun,
                      Refund, Seller, SellerAllocation, SellerSettlement, now)
from .finance import commission_for, q

Z = Decimal("0")
OK_CAP = ("COMPLETED", "PARTIALLY_REFUNDED", "REFUNDED")
METRICS = ["payments", "refunds", "commissions", "fees", "seller_earnings", "pending_settlements", "payouts", "failed_payments", "pending_payments"]


def _win(qy, col, start, end):
    if start:
        qy = qy.filter(col >= start)
    if end:
        qy = qy.filter(col < end)
    return qy


def rows(db: Session, org_id, metric: str, start=None, end=None, seller_id=None) -> list[dict]:
    """[{ref_type, ref_id, amount, at, seller_id, order_id}] — the source records behind a metric."""
    out = []

    def add(t, obj, amount, at, sid=None, oid=None):
        out.append({"ref_type": t, "ref_id": str(obj), "amount": q(amount), "at": at.isoformat() if at else None,
                    "seller_id": str(sid) if sid else None, "order_id": str(oid) if oid else None})

    if metric == "payments":
        if seller_id:
            qy = db.query(SellerAllocation, PaypalCapture).join(PaypalCapture, PaypalCapture.order_id == SellerAllocation.order_id).filter(
                SellerAllocation.org_id == org_id, SellerAllocation.seller_id == seller_id, PaypalCapture.status.in_(OK_CAP))
            for a, c in _win(qy, PaypalCapture.created_at, start, end):
                add("capture", c.capture_id, a.gross, c.created_at, a.seller_id, c.order_id)
        else:
            for c in _win(db.query(PaypalCapture).filter(PaypalCapture.org_id == org_id, PaypalCapture.status.in_(OK_CAP)), PaypalCapture.created_at, start, end):
                add("capture", c.capture_id, c.amount, c.created_at, None, c.order_id)
    elif metric == "refunds":
        qy = db.query(Refund).filter(Refund.org_id == org_id, Refund.status == "completed")
        if seller_id:
            qy = qy.filter(Refund.seller_id == seller_id)
        for r in _win(qy, Refund.created_at, start, end):
            add("refund", r.id, r.amount, r.created_at, r.seller_id, r.order_id)
    elif metric in ("commissions", "fees", "seller_earnings"):
        types = {"commissions": ("commission", "commission_reversal"), "fees": ("fee",), "seller_earnings": ("seller_earning", "seller_clawback")}[metric]
        qy = db.query(FinancialLedger).filter(FinancialLedger.org_id == org_id, FinancialLedger.entry_type.in_(types))
        if metric == "fees":
            qy = qy.filter(FinancialLedger.account == "platform")  # fee is posted to cash AND platform; count once
        if seller_id:
            qy = qy.filter(FinancialLedger.seller_id == seller_id)
        for e in _win(qy, FinancialLedger.created_at, start, end):
            add("ledger:" + e.entry_type, e.id, e.amount, e.created_at, e.seller_id, e.order_id)
    elif metric == "pending_settlements":
        qy = db.query(SellerSettlement).filter_by(org_id=org_id, status="pending")
        if seller_id:
            qy = qy.filter_by(seller_id=seller_id)
        for s in qy:
            add("settlement", s.id, s.amount, s.created_at, s.seller_id)
    elif metric == "payouts":
        qy = db.query(Payout).filter_by(org_id=org_id, status="paid")
        if seller_id:
            qy = qy.filter_by(seller_id=seller_id)
        for p in _win(qy, Payout.created_at, start, end):
            add("payout", p.id, p.amount, p.created_at, p.seller_id)
    elif metric == "failed_payments":
        for o in _win(db.query(Order).filter_by(org_id=org_id, status="failed"), Order.created_at, start, end):
            add("order", o.id, o.total, o.created_at, None, o.id)
    elif metric == "pending_payments":
        for c in _win(db.query(PaypalCapture).filter_by(org_id=org_id, status="PENDING"), PaypalCapture.created_at, start, end):
            add("capture", c.capture_id, c.amount, c.created_at, None, c.order_id)
    return out


def summary(db: Session, org_id, start=None, end=None, seller_id=None) -> dict:
    m = {}
    for k in METRICS:
        r = rows(db, org_id, k, start, end, seller_id)
        m[k] = {"amount": str(sum((x["amount"] for x in r), Z)), "count": len(r)}
    return m


# --- reconciliation ----------------------------------------------------------
def run_reconciliation(db: Session, org, user_id=None) -> ReconciliationRun:
    found: list[tuple] = []  # (kind, severity, ref_type, ref_id, expected, actual, message)
    orders = db.query(Order).filter_by(org_id=org.id).all()
    caps = db.query(PaypalCapture).filter_by(org_id=org.id).all()
    cap_by_order = defaultdict(list)
    for c in caps:
        cap_by_order[c.order_id].append(c)
    order_ids = {o.id for o in orders}

    for o in orders:  # 1. paid order without payment
        if o.status in ("paid", "refunded") and not cap_by_order.get(o.id):
            found.append(("order_without_payment", "high", "order", o.number, o.total, None, f"Order {o.number} is marked {o.status} but has no PayPal capture"))
    for c in caps:  # 2. payment without order
        if c.order_id is None or c.order_id not in order_ids:
            found.append(("payment_without_order", "high", "capture", c.capture_id, None, c.amount, f"Capture {c.capture_id} ({c.amount} {c.currency}) has no matching marketplace order"))
    seen = defaultdict(list)
    for c in caps:  # 5. duplicates
        seen[c.capture_id].append(c)
    for cid, lst in seen.items():
        if len(lst) > 1:
            found.append(("duplicate_record", "medium", "capture", cid, None, None, f"Capture id {cid} stored {len(lst)} times"))
    for o in orders:  # 3/4. commission + allocation
        caps_o = cap_by_order.get(o.id, [])
        allocs = db.query(SellerAllocation).filter_by(order_id=o.id).all()
        if caps_o and q(sum((a.gross for a in allocs), Z)) != o.total:
            found.append(("allocation_mismatch", "high", "order", o.number, o.total, sum((a.gross for a in allocs), Z), f"Seller allocations for {o.number} do not sum to the order total"))
        for a in allocs:
            seller = db.get(Seller, a.seller_id)
            com = db.query(Commission).filter_by(order_id=o.id, seller_id=a.seller_id).first()
            stored = com.amount if com else None
            if stored is None or stored != a.commission or a.commission + a.net != a.gross:
                found.append(("allocation_mismatch", "high", "order", o.number, a.gross, a.commission + a.net, f"Allocation for {seller.business_name} on {o.number}: commission {a.commission} + net {a.net} != gross {a.gross}"))
            else:  # compare against the rate recorded ON the order: later rule changes only affect new orders
                exp = min(q(a.gross * com.rate_pct / 100) + com.fixed, q(a.gross))
                if exp != stored:
                    found.append(("incorrect_commission", "medium", "order", o.number, exp, stored, f"Commission on {o.number} for {seller.business_name} is {stored}; its recorded rate ({com.rate_pct}% + {com.fixed}) gives {exp}"))
    for r in db.query(Refund).filter_by(org_id=org.id, status="completed"):  # 6. unreconciled refunds
        led = db.query(FinancialLedger).filter_by(org_id=org.id, entry_type="refund", ref_id=str(r.id)).first()
        if not r.paypal_refund_id or not led:
            found.append(("unreconciled_refund", "high", "refund", str(r.id)[:8], r.amount, led.amount if led else None, f"Completed refund {r.amount} has {'no PayPal refund id' if not r.paypal_refund_id else 'no ledger entry'}"))
    for c in caps:
        done = sum((r.amount for r in db.query(Refund).filter_by(org_id=org.id, capture_id=c.capture_id, status="completed")), Z)
        if done > c.amount:
            found.append(("unreconciled_refund", "high", "capture", c.capture_id, c.amount, done, f"Refunds {done} exceed captured {c.amount}"))
    for p in db.query(Payout).filter(Payout.org_id == org.id, Payout.status.in_(("paid", "processing", "delayed"))):  # 7. payouts
        expected = sum((s.amount for s in db.query(SellerSettlement).filter_by(payout_id=p.id)), Z) + p.adjustment
        if q(expected) != p.amount:
            found.append(("payout_mismatch", "high", "payout", str(p.id)[:8], expected, p.amount, f"Payout {p.amount} differs from the {expected} its settlements (net of refund clawbacks) support"))
        if p.status == "delayed" or (p.status == "processing" and (now() - p.created_at.replace(tzinfo=p.created_at.tzinfo or now().tzinfo)).days >= 3):
            found.append(("payout_delayed", "medium", "payout", str(p.id)[:8], p.amount, None, f"Payout of {p.amount} is {p.status}"))

    run = ReconciliationRun(org_id=org.id, total_checked=len(orders) + len(caps), mismatches=len(found), run_by=user_id)
    db.add(run)
    db.flush()
    for k, sev, rt, rid, e, a, msg in found:
        db.add(ReconciliationRecord(org_id=org.id, run_id=run.id, kind=k, severity=sev, ref_type=rt, ref_id=str(rid),
                                    expected=q(e) if e is not None else None, actual=q(a) if a is not None else None, message=msg))
    return run


def latest_run(db: Session, org_id):
    run = db.query(ReconciliationRun).filter_by(org_id=org_id).order_by(ReconciliationRun.started_at.desc()).first()
    recs = db.query(ReconciliationRecord).filter_by(run_id=run.id).all() if run else []
    return run, recs


# --- reports -----------------------------------------------------------------
def report(db: Session, org, kind: str, seller_id=None) -> dict:
    end, days = now(), {"daily": 1, "weekly": 7, "monthly": 30}.get(kind)
    start = end - timedelta(days=days) if days else None
    if kind in ("daily", "weekly", "monthly"):
        s = summary(db, org.id, start, end)
        return {"title": f"{kind.title()} report", "columns": ["metric", "count", "amount"], "rows": [[k, v["count"], v["amount"]] for k, v in s.items()]}
    if kind == "commission":
        by = defaultdict(lambda: Z)
        for r in rows(db, org.id, "commissions"):
            by[r["seller_id"]] += r["amount"]
        names = {str(s.id): s.business_name for s in db.query(Seller).filter_by(org_id=org.id)}
        return {"title": "Commission report", "columns": ["seller", "net commission"], "rows": [[names.get(k, "(order-level)"), str(v)] for k, v in sorted(by.items(), key=lambda x: -x[1])]}
    if kind == "refunds":
        return {"title": "Refund report", "columns": ["refund", "order", "amount", "date"], "rows": [[r["ref_id"][:8], r["order_id"][:8] if r["order_id"] else "", str(r["amount"]), r["at"]] for r in rows(db, org.id, "refunds", seller_id=seller_id)]}
    if kind == "disputes":
        ds = db.query(Dispute).filter_by(org_id=org.id).order_by(Dispute.opened_at.desc()).all()
        return {"title": "Dispute report", "columns": ["dispute", "reason", "status", "amount", "due"], "rows": [[d.paypal_dispute_id, d.reason, d.status, str(d.amount), d.response_due_date.isoformat() if d.response_due_date else "unknown"] for d in ds]}
    if kind == "seller_statement" and seller_id:
        led = db.query(FinancialLedger).filter_by(org_id=org.id, seller_id=seller_id, account="seller").order_by(FinancialLedger.created_at).all()
        run = Z
        out = []
        for e in led:
            run += e.amount
            out.append([e.created_at.isoformat()[:19], e.entry_type, str(e.amount), str(q(run)), e.ref_id or ""])
        return {"title": "Seller statement", "columns": ["date", "type", "amount", "running balance", "reference"], "rows": out}
    raise ValueError("Unknown report kind")
