"""DisputePilot. Evidence is gathered ONLY from connected database records. Missing evidence is reported as missing;
nothing is ever invented. The completeness score is NOT a probability of winning.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from ..auth import Ctx
from ..models import (CustomerMessage, Dispute, DisputeEvidence, DisputeResponse, Order, OrderItem, PaypalCapture, Refund,
                      Seller, Shipment, TrackingEvent, now)
from . import finance, llm, paypal

KINDS = ["order", "invoice", "payment", "shipping_info", "tracking", "delivery_confirmation",
         "customer_communication", "refund_history", "seller_info"]
REQUIRED = {
    "MERCHANDISE_OR_SERVICE_NOT_RECEIVED": ["order", "payment", "shipping_info", "tracking", "delivery_confirmation"],
    "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED": ["order", "payment", "customer_communication"],
    "UNAUTHORISED": ["order", "payment", "delivery_confirmation"],
    "CREDIT_NOT_PROCESSED": ["order", "payment", "refund_history"],
    "DUPLICATE_TRANSACTION": ["order", "payment", "refund_history"],
    "INCORRECT_AMOUNT": ["order", "payment"],
}
DEFAULT_REQUIRED = ["order", "payment", "customer_communication"]
RELEVANT_EXTRA = ["customer_communication", "refund_history", "seller_info"]
LABEL = {"order": "Order record", "invoice": "Invoice", "payment": "Payment record", "shipping_info": "Shipping information",
         "tracking": "Courier tracking", "delivery_confirmation": "Delivery confirmation", "customer_communication": "Customer communication",
         "refund_history": "Refund history", "seller_info": "Seller information"}


def _aware(d):
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def urgency(d: Dispute) -> dict:
    due = _aware(d.response_due_date)
    if d.status in ("resolved", "lost"):
        return {"level": "closed", "hours_left": None}
    if due is None:
        return {"level": "unknown", "hours_left": None, "note": "PayPal did not provide a response deadline for this dispute."}
    h = (due - now()).total_seconds() / 3600
    return {"level": "overdue" if h < 0 else "critical" if h < 48 else "soon" if h < 24 * 5 else "normal", "hours_left": round(h, 1)}


def collect_evidence(db: Session, org, d: Dispute) -> list[dict]:
    """Return one item per evidence kind: {kind, available, source, detail, data}. Reads connected records only."""
    ev = {k: {"kind": k, "label": LABEL[k], "available": False, "source": None, "detail": "Not available from any connected source.", "data": None} for k in KINDS}

    def have(k, source, detail, data=None):
        ev[k].update(available=True, source=source + (" (simulated demo record)" if d.is_simulated else ""), detail=detail, data=data)

    ev["invoice"]["detail"] = "No invoice source is connected to MarketPilot; none will be fabricated."
    order = db.get(Order, d.order_id) if d.order_id else None
    if order:
        items = db.query(OrderItem).filter_by(order_id=order.id).all()
        have("order", "orders", f"Order {order.number} placed {order.created_at:%Y-%m-%d}, total {order.total} {order.currency}, items: " + ", ".join(i.name for i in items),
             {"number": order.number, "total": str(order.total), "created_at": order.created_at.isoformat(), "address": order.shipping_address})
        cap = db.query(PaypalCapture).filter_by(org_id=org.id, order_id=order.id).first()
        if cap:
            have("payment", "paypal_captures", f"PayPal capture {cap.capture_id}: {cap.amount} {cap.currency}, status {cap.status}",
                 {"capture_id": cap.capture_id, "amount": str(cap.amount), "created_at": cap.created_at.isoformat()})
        sh = db.query(Shipment).filter_by(order_id=order.id).first()
        if sh:
            have("shipping_info", "shipments", f"{sh.carrier}, tracking {sh.tracking_number}, shipped to {sh.address or 'address not recorded'}, status {sh.status}",
                 {"carrier": sh.carrier, "tracking_number": sh.tracking_number, "address": sh.address, "status": sh.status})
            evs = db.query(TrackingEvent).filter_by(shipment_id=sh.id).order_by(TrackingEvent.occurred_at).all()
            if evs:
                have("tracking", "tracking_events", f"{len(evs)} tracking event(s), latest: {evs[-1].description} ({evs[-1].occurred_at:%Y-%m-%d})",
                     [{"status": e.status, "description": e.description, "at": e.occurred_at.isoformat()} for e in evs])
            dl = [e for e in evs if e.status == "delivered"]
            if dl:
                have("delivery_confirmation", "tracking_events", f"Courier recorded delivery on {dl[-1].occurred_at:%Y-%m-%d %H:%M} UTC: {dl[-1].description}",
                     {"at": dl[-1].occurred_at.isoformat()})
        msgs = db.query(CustomerMessage).filter_by(order_id=order.id).order_by(CustomerMessage.sent_at).all()
        if msgs:
            have("customer_communication", "customer_messages", f"{len(msgs)} message(s) between {msgs[0].sent_at:%Y-%m-%d} and {msgs[-1].sent_at:%Y-%m-%d}",
                 [{"direction": m.direction, "body": m.body, "at": m.sent_at.isoformat()} for m in msgs])
        refs = db.query(Refund).filter_by(org_id=org.id, order_id=order.id).all()
        have("refund_history", "refunds", (f"{len(refs)} refund record(s): " + ", ".join(f"{r.amount} {r.status}" for r in refs)) if refs else "No refunds recorded for this order (verified: none exist).",
             [{"amount": str(r.amount), "status": r.status} for r in refs])
    if d.seller_id:
        s = db.get(Seller, d.seller_id)
        if s:
            have("seller_info", "sellers", f"{s.business_name} ({s.email}), status {s.status}")
    return ev


def contradictions(db: Session, d: Dispute, ev: dict) -> list[str]:
    out = []
    order = db.get(Order, d.order_id) if d.order_id else None
    cap = ev["payment"]["data"]
    if cap and d.amount > Decimal(cap["amount"]):
        out.append(f"Disputed amount {d.amount} exceeds captured payment {cap['amount']}")
    if cap and order and order.total != Decimal(cap["amount"]):
        out.append(f"Order total {order.total} differs from captured payment {cap['amount']}")
    sh = ev["shipping_info"]["data"]
    if sh and order and order.shipping_address and sh["address"] and sh["address"] != order.shipping_address:
        out.append("Shipment address differs from the order's shipping address")
    if sh and sh["status"] == "delivered" and not ev["delivery_confirmation"]["available"]:
        out.append("Shipment is marked delivered but no delivery tracking event exists")
    dc = ev["delivery_confirmation"]["data"]
    if dc and datetime.fromisoformat(dc["at"]) > _aware(d.opened_at):
        out.append("Delivery was recorded AFTER the dispute was opened")
    return out


def analyze(db: Session, org, d: Dispute) -> dict:
    ev = collect_evidence(db, org, d)
    req = REQUIRED.get(d.reason, DEFAULT_REQUIRED)
    relevant = list(dict.fromkeys(req + RELEVANT_EXTRA))
    have_n = lambda ks: sum(1 for k in ks if ev[k]["available"])
    cons = contradictions(db, d, ev)
    parts = {
        "availability": {"max": 40, "score": round(40 * have_n(KINDS) / len(KINDS), 1), "basis": f"{have_n(KINDS)}/{len(KINDS)} evidence types available"},
        "relevance": {"max": 20, "score": round(20 * have_n(relevant) / len(relevant), 1), "basis": f"{have_n(relevant)}/{len(relevant)} evidence types relevant to '{d.reason}' available"},
        "consistency": {"max": 20, "score": max(0, 20 - 7 * len(cons)), "basis": f"{len(cons)} contradiction(s); -7 each"},
        "required_documents": {"max": 20, "score": round(20 * have_n(req) / len(req), 1), "basis": f"{have_n(req)}/{len(req)} required documents available"},
    }
    score = round(sum(p["score"] for p in parts.values()), 1)
    missing = [LABEL[k] for k in req if not ev[k]["available"]]
    refunded = sum((Decimal(r["amount"]) for r in (ev["refund_history"]["data"] or []) if r["status"] == "completed"), Decimal("0"))
    if refunded >= d.amount:
        rec, why = "accept", f"The customer has already received refunds of {refunded} covering the disputed {d.amount}; contesting risks paying twice."
    elif not missing and not cons and score >= 70:
        rec, why = "contest", "All required documents for this dispute reason are present and consistent."
    elif cons:
        rec, why = "review", "Evidence contradicts itself; resolve these before submitting: " + "; ".join(cons)
    elif score < 40:
        rec, why = "consider_accept", f"Required evidence is largely missing ({', '.join(missing)}). Gather it before the deadline or consider accepting."
    else:
        rec, why = "gather_evidence", f"Missing required evidence: {', '.join(missing) or 'none'}. Obtain it before contesting."
    return {"reason": d.reason, "evidence": ev, "required": req, "missing_required": missing, "contradictions": cons,
            "score": score, "score_breakdown": parts, "score_note": "Evidence completeness only. This is NOT a probability of winning.",
            "recommendation": rec, "recommendation_reason": why, "deadline": urgency(d),
            "response_due_date": d.response_due_date.isoformat() if d.response_due_date else None}


def timeline(db: Session, d: Dispute, ev: dict) -> list[tuple[str, str]]:
    t = []
    if ev["order"]["data"]:
        t.append((ev["order"]["data"]["created_at"], f"Order {ev['order']['data']['number']} placed"))
    if ev["payment"]["data"]:
        t.append((ev["payment"]["data"]["created_at"], f"Payment captured ({ev['payment']['data']['capture_id']})"))
    for e in ev["tracking"]["data"] or []:
        t.append((e["at"], f"Courier: {e['description']}"))
    for m in ev["customer_communication"]["data"] or []:
        t.append((m["at"], f"Customer message ({m['direction']}): {m['body'][:140]}"))
    t.append((d.opened_at.isoformat(), f"Dispute {d.paypal_dispute_id} opened — reason {d.reason}"))
    return sorted(t)


def generate_response(c: Ctx, d: Dispute, actor_type="user") -> DisputeResponse:
    """Builds a DRAFT. Never submits. Uses only verified evidence; states plainly what is missing."""
    a = analyze(c.db, c.org, d)
    ev = a["evidence"]
    lines = [f"DISPUTE RESPONSE — {d.paypal_dispute_id}", "", "1. SUMMARY",
             f"Dispute of {d.amount} {d.currency}, reason: {d.reason}. Order {ev['order']['data']['number'] if ev['order']['data'] else '(not linked)'}.", "",
             "2. MERCHANT'S EXPLANATION"]
    if a["recommendation"] == "accept":
        lines.append(f"The buyer has already been refunded. {a['recommendation_reason']}")
    else:
        lines.append("We provide the records below from our order, payment, fulfilment and support systems.")
        for k in a["required"]:
            lines.append(f"- {LABEL[k]}: " + (ev[k]["detail"] if ev[k]["available"] else "NOT AVAILABLE — we cannot provide this."))
    lines += ["", "3. TIMELINE OF EVENTS"] + [f"- {ts[:16].replace('T', ' ')} UTC  {txt}" for ts, txt in timeline(c.db, d, ev)]
    lines += ["", "4. EVIDENCE LIST"] + [f"- [{'x' if ev[k]['available'] else ' '}] {LABEL[k]}" + (f" — {ev[k]['source']}" if ev[k]["available"] else " — missing") for k in KINDS]
    lines += ["", "5. SUPPORTING EXPLANATION", a["recommendation_reason"]]
    if a["contradictions"]:
        lines += ["Open inconsistencies in our records: " + "; ".join(a["contradictions"])]
    lines += ["", "6. RECOMMENDED ACTION", {"accept": "Accept the claim.", "contest": "Contest and submit this evidence.",
              "review": "Do not submit until inconsistencies are resolved.", "consider_accept": "Gather missing evidence, otherwise accept.",
              "gather_evidence": "Gather missing evidence, then contest."}[a["recommendation"]]]
    body = "\n".join(lines)
    resp = DisputeResponse(org_id=c.org.id, dispute_id=d.id, action="accept" if a["recommendation"] == "accept" else "contest",
                           body=body, analysis={k: v for k, v in a.items() if k != "evidence"} | {"evidence_kinds": {k: ev[k]["available"] for k in KINDS}}, status="draft")
    c.db.add(resp)
    for k in KINDS:  # persist what was collected so the record of "what we relied on" is auditable
        if ev[k]["available"]:
            c.db.add(DisputeEvidence(org_id=c.org.id, dispute_id=d.id, kind=k, source=ev[k]["source"], detail=ev[k]["detail"]))
    c.audit("dispute.draft_generated", "dispute", d.id, {"score": a["score"], "recommendation": a["recommendation"]}, actor_type=actor_type)
    c.db.flush()
    return resp


class DisputeError(Exception):
    def __init__(self, msg, code=409):
        super().__init__(msg)
        self.code = code


def edit_response(c: Ctx, resp: DisputeResponse, body: str, action: str | None):
    if resp.status in ("submitted",):
        raise DisputeError("Already submitted")
    resp.body, resp.status, resp.approved_by = body, "draft", None  # any edit voids prior approval
    if action in ("contest", "accept"):
        resp.action = action
    c.audit("dispute.response_edited", "dispute_response", resp.id)


def approve_response(c: Ctx, resp: DisputeResponse):
    if resp.status != "draft":
        raise DisputeError(f"Response is '{resp.status}'")
    resp.status, resp.approved_by = "approved", c.user.id
    c.audit("dispute.response_approved", "dispute_response", resp.id, {"action": resp.action})


def submit_response(c: Ctx, d: Dispute, resp: DisputeResponse) -> DisputeResponse:
    if resp.status != "approved" or not resp.approved_by:
        raise DisputeError("Merchant approval is required before submission", 403)
    if urgency(d)["level"] == "overdue":
        raise DisputeError("The PayPal response deadline has passed")
    if d.is_simulated or c.org.mode == "demo":
        resp.result = {"simulated": True, "note": "Demo mode: nothing was sent to PayPal."}
    else:
        try:
            if resp.action == "accept":
                resp.result = paypal.get_client().accept_claim(d.paypal_dispute_id, resp.body)
            else:
                ev = collect_evidence(c.db, c.org, d)
                tr = ev["shipping_info"]["data"]
                resp.result = paypal.get_client().provide_evidence(
                    d.paypal_dispute_id, resp.body, {"carrier_name": "OTHER", "carrier_name_other": tr["carrier"], "tracking_number": tr["tracking_number"]} if tr else None)
        except paypal.PayPalError as e:
            resp.status, resp.result = "failed", {"error": str(e)[:300]}
            c.audit("dispute.submit_failed", "dispute", d.id, resp.result)
            raise DisputeError(f"PayPal rejected the submission: {e}", 502)
    resp.status, resp.submitted_at = "submitted", now()
    d.status = "under_review" if resp.action == "contest" else "resolved"
    c.audit("dispute.submitted", "dispute", d.id, {"action": resp.action, "simulated": bool((resp.result or {}).get("simulated"))})
    finance.notify(c.db, c.org.id, f"Dispute {d.paypal_dispute_id} {resp.action} submitted", "Recorded in audit log.", "info", "/disputepilot")
    return resp


_STATUS = [("SELLER_RESPONSE", "waiting_merchant"), ("UNDER_REVIEW", "under_review"), ("RESOLVED", "resolved"), ("OPEN", "open")]


def upsert_dispute(db: Session, org, det: dict) -> Dispute:
    """Map a PayPal dispute object onto our row. Field names follow the Disputes spec; unmapped status -> 'open'."""
    st = det.get("status", "")
    status = next((v for k, v in _STATUS if k in st), "open")
    if det.get("dispute_outcome", {}).get("outcome_code") == "RESOLVED_BUYER_FAVOUR":
        status = "lost"
    tx = (det.get("disputed_transactions") or [{}])[0]
    cap = db.query(PaypalCapture).filter_by(org_id=org.id, capture_id=tx.get("seller_transaction_id", "")).first()
    due = det.get("seller_response_due_date")  # None when PayPal does not provide it: never invented
    row = db.query(Dispute).filter_by(org_id=org.id, paypal_dispute_id=det["dispute_id"]).first() or Dispute(org_id=org.id, paypal_dispute_id=det["dispute_id"])
    amt = det.get("dispute_amount", {})
    row.reason, row.status = det.get("reason", "OTHER"), status
    row.amount, row.currency = Decimal(amt.get("value", "0")), amt.get("currency_code", org.currency)
    row.response_due_date = datetime.fromisoformat(due.replace("Z", "+00:00")) if due else None
    row.capture_id, row.order_id, row.is_simulated, row.raw = tx.get("seller_transaction_id"), cap.order_id if cap else None, False, det
    db.add(row)
    db.flush()
    return row


def sync_from_paypal(db: Session, org) -> int:
    pp, n = paypal.get_client(), 0
    for item in pp.list_disputes():
        upsert_dispute(db, org, pp.get_dispute(item["dispute_id"]))
        n += 1
    return n
