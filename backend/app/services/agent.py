"""MarketPilot agent. A fixed registry of read-only tools (+ draft-only writes). The model never sees SQL, never moves
money, never approves or submits anything. Every tool call is permission-checked and audit-logged (actor_type='ai').
With OPENAI_API_KEY the model chooses tools (function calling); without it a deterministic router picks them.
"""
import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func

from ..auth import Ctx
from ..models import (AiInvestigation, Dispute, Order, PaypalCapture, Payout, Refund, RefundInvestigation, Seller,
                      SellerSettlement, now)
from . import disputes as dsp
from . import finance, llm, reconcile, refunds as rfd

SYSTEM = ("You are MarketPilot, a financial operations analyst for a marketplace. Use the provided tools to investigate; "
          "state ONLY facts and numbers returned by tools, never compute or estimate totals yourself, and cite references "
          "(order numbers, capture ids, dispute ids). If data is missing say so. You cannot move money, approve refunds, "
          "submit disputes or change settings; recommend those actions for the merchant to approve. Never accuse a customer of fraud. Be concise.")


def ref(t, i, label=None):
    return {"type": t, "id": str(i), "label": label or str(i)}


def _seller(c: Ctx, q: str | None):
    qy = c.db.query(Seller).filter_by(org_id=c.org.id)
    try:
        return qy.filter_by(id=uuid.UUID(str(q))).first()
    except ValueError:
        return qy.filter(Seller.business_name.ilike(f"%{q}%")).first() if q else None


# ---- tools: each returns {"data": ..., "findings": [...]} ----------------------------------------------------------
def get_marketplace_summary(c: Ctx, days: int = 30):
    start = now() - timedelta(days=max(1, min(days, 365)))
    s = reconcile.summary(c.db, c.org.id, start, None)
    return {"data": {"period_days": days, "currency": c.org.currency, **s}, "findings": []}


def get_transaction_details(c: Ctx, reference: str):
    o = c.db.query(Order).filter(Order.org_id == c.org.id, func.lower(Order.number) == reference.lower()).first()
    cap = c.db.query(PaypalCapture).filter_by(org_id=c.org.id, capture_id=reference).first()
    if cap and not o:
        o = c.db.get(Order, cap.order_id) if cap.order_id else None
    if not o and not cap:
        return {"data": {"error": f"No order or capture found for '{reference}'"}, "findings": []}
    caps = c.db.query(PaypalCapture).filter_by(org_id=c.org.id, order_id=o.id).all() if o else [cap]
    rs = c.db.query(Refund).filter_by(org_id=c.org.id, order_id=o.id).all() if o else []
    recs = [r for r in reconcile.latest_run(c.db, c.org.id)[1] if o and r.ref_id in (o.number, *[x.capture_id for x in caps])]
    return {"data": {"order": {"number": o.number, "total": str(o.total), "status": o.status, "customer_id": o.customer_id} if o else None,
                     "captures": [{"id": x.capture_id, "amount": str(x.amount), "status": x.status, "simulated": x.is_simulated} for x in caps],
                     "refunds": [{"id": str(r.id)[:8], "amount": str(r.amount), "status": r.status} for r in rs],
                     "reconciliation": [{"kind": r.kind, "message": r.message} for r in recs]}, "findings": []}


def get_seller_details(c: Ctx, seller: str):
    s = _seller(c, seller)
    if not s:
        return {"data": {"error": "Seller not found"}, "findings": []}
    return {"data": {"id": str(s.id), "name": s.business_name, "status": s.status, "onboarding": s.onboarding_status,
                     "paypal_merchant_id": s.paypal_merchant_id, "commission_pct": str(s.commission_pct if s.commission_pct is not None else c.org.default_commission_pct),
                     "balance": str(finance.seller_balance(c.db, c.org.id, s.id))}, "findings": []}


def get_seller_earnings(c: Ctx, seller: str | None = None, days: int = 30):
    s = _seller(c, seller) if seller else None
    start = now() - timedelta(days=days)
    sid = s.id if s else None
    sm = reconcile.summary(c.db, c.org.id, start, None, sid)
    d = {"scope": s.business_name if s else "all sellers", **{k: sm[k] for k in ("payments", "commissions", "seller_earnings", "refunds", "pending_settlements", "payouts")}}
    if not s:
        d["top_sellers_by_balance"] = sorted(({"seller": x.business_name, "balance": str(finance.seller_balance(c.db, c.org.id, x.id))} for x in c.db.query(Seller).filter_by(org_id=c.org.id)),
                                              key=lambda x: -Decimal(x["balance"]))[:5]
    return {"data": d, "findings": []}


def get_refund_history(c: Ctx, order: str | None = None, days: int = 30):
    qy = c.db.query(Refund).filter(Refund.org_id == c.org.id, Refund.created_at >= now() - timedelta(days=days))
    if order:
        o = c.db.query(Order).filter_by(org_id=c.org.id, number=order).first()
        qy = qy.filter(Refund.order_id == (o.id if o else None))
    rs = qy.order_by(Refund.created_at.desc()).limit(50).all()
    return {"data": {"count": len(rs), "refunds": [{"id": str(r.id), "amount": str(r.amount), "status": r.status, "order_id": str(r.order_id)} for r in rs]}, "findings": []}


def investigate_refund(c: Ctx, refund_id: str):
    r = c.db.query(Refund).filter_by(id=uuid.UUID(refund_id), org_id=c.org.id).first()
    if not r:
        return {"data": {"error": "Refund not found"}, "findings": []}
    inv = rfd.investigate(c.db, c.org, r)
    return {"data": {"risk": inv.risk_level, "report": inv.report, "recommended_action": inv.recommended_action}, "findings": []}


def _due(d):
    u = dsp.urgency(d)
    return f"{u['level']}" + (f" ({u['hours_left']}h left)" if u.get("hours_left") is not None else "")


def get_open_disputes(c: Ctx):
    ds = c.db.query(Dispute).filter(Dispute.org_id == c.org.id, Dispute.status.in_(("open", "under_review", "waiting_merchant"))).all()
    items = []
    for d in ds:
        a = dsp.analyze(c.db, c.org, d)
        items.append({"id": str(d.id), "paypal_id": d.paypal_dispute_id, "reason": d.reason, "status": d.status, "amount": str(d.amount),
                      "deadline": _due(d), "score": a["score"], "missing": a["missing_required"], "contradictions": a["contradictions"], "recommendation": a["recommendation"]})
    return {"data": {"count": len(items), "disputes": items}, "findings": []}


def get_dispute_details(c: Ctx, dispute_id: str):
    d = c.db.query(Dispute).filter_by(id=uuid.UUID(dispute_id), org_id=c.org.id).first()
    if not d:
        return {"data": {"error": "Dispute not found"}, "findings": []}
    a = dsp.analyze(c.db, c.org, d)
    return {"data": {"paypal_id": d.paypal_dispute_id, "reason": d.reason, "amount": str(d.amount), "deadline": _due(d), "score": a["score"],
                     "score_note": a["score_note"], "missing_required": a["missing_required"], "contradictions": a["contradictions"],
                     "recommendation": a["recommendation"], "why": a["recommendation_reason"]}, "findings": []}


collect_dispute_evidence = lambda c, dispute_id: {"data": {k: {"available": v["available"], "detail": v["detail"]} for k, v in dsp.collect_evidence(
    c.db, c.org, c.db.query(Dispute).filter_by(id=uuid.UUID(dispute_id), org_id=c.org.id).one()).items()}, "findings": []}


def generate_dispute_response(c: Ctx, dispute_id: str):
    d = c.db.query(Dispute).filter_by(id=uuid.UUID(dispute_id), org_id=c.org.id).one()
    r = dsp.generate_response(c, d, actor_type="ai")
    return {"data": {"response_id": str(r.id), "status": "draft (NOT submitted; merchant approval required)", "recommendation": r.analysis["recommendation"]}, "findings": []}


def get_reconciliation_report(c: Ctx, rerun: bool = True):
    run = reconcile.run_reconciliation(c.db, c.org, c.user.id) if rerun else None
    run, recs = reconcile.latest_run(c.db, c.org.id) if not run else (run, c.db.query(reconcile.ReconciliationRecord).filter_by(run_id=run.id).all())
    by = {}
    for r in recs:
        by[r.kind] = by.get(r.kind, 0) + 1
    return {"data": {"checked": run.total_checked if run else 0, "mismatches": len(recs), "by_kind": by,
                     "records": [{"kind": r.kind, "severity": r.severity, "ref": r.ref_id, "expected": str(r.expected) if r.expected is not None else None, "actual": str(r.actual) if r.actual is not None else None, "message": r.message} for r in recs[:25]]}, "findings": []}


def find_unmatched_transactions(c: Ctx):
    recs = reconcile.latest_run(c.db, c.org.id)[1]
    k = ("order_without_payment", "payment_without_order", "unreconciled_refund", "duplicate_record")
    return {"data": [{"kind": r.kind, "ref": r.ref_id, "message": r.message} for r in recs if r.kind in k], "findings": []}


def get_delayed_payouts(c: Ctx):
    ps = c.db.query(Payout).filter(Payout.org_id == c.org.id, Payout.status.in_(("delayed", "processing", "failed", "pending_approval"))).all()
    names = {s.id: s.business_name for s in c.db.query(Seller).filter_by(org_id=c.org.id)}
    return {"data": [{"payout": str(p.id)[:8], "seller": names.get(p.seller_id), "amount": str(p.amount), "status": p.status} for p in ps], "findings": []}


def get_financial_risk_summary(c: Ctx):
    """Cross-module investigation: refunds + disputes + reconciliation + payouts. Produces ranked findings with refs."""
    db, org, f = c.db, c.org, []
    invs = db.query(RefundInvestigation, Refund).join(Refund, Refund.id == RefundInvestigation.refund_id).filter(
        RefundInvestigation.org_id == org.id, RefundInvestigation.status == "open", Refund.status.in_(("needs_review", "escalated", "pending_approval")), RefundInvestigation.risk_level != "low").all()
    dups = [(i, r) for i, r in invs if any(x["code"] == "potential_duplicate" for x in i.report["flags"])]
    if invs:
        f.append({"title": f"{len(invs)} refund request(s) flagged for review" + (f", {len(dups)} potential duplicate(s)" if dups else ""), "severity": "high" if dups else "medium",
                  "detail": "; ".join(f"{i.report['order_number']}: {i.report['reason']}" for i, r in invs[:5]),
                  "refs": [ref("refund", r.id, f"Refund {r.amount} on {i.report['order_number']}") for i, r in invs],
                  "action": "Open RefundGuard and approve, reject or escalate each (human approval required)."})
    ds = [d for d in db.query(Dispute).filter(Dispute.org_id == org.id, Dispute.status.in_(("open", "waiting_merchant"))).all()]
    if ds:
        det = [(d, dsp.analyze(db, org, d)) for d in ds]
        urgent = [d for d, a in det if a["deadline"]["level"] in ("critical", "overdue")]
        total = sum((d.amount for d in ds), Decimal("0"))
        f.append({"title": f"{len(ds)} dispute(s) need a merchant response ({total} {org.currency} at stake)" + (f", {len(urgent)} urgent" if urgent else ""), "severity": "high" if urgent else "medium",
                  "detail": "; ".join(f"{d.paypal_dispute_id}: score {a['score']}/100, {a['recommendation']}" + (f", missing {', '.join(a['missing_required'])}" if a["missing_required"] else "") for d, a in det[:6]),
                  "refs": [ref("dispute", d.id, d.paypal_dispute_id) for d in ds],
                  "action": "Open DisputePilot, review the AI-drafted responses and approve before the deadline."})
    run, recs = reconcile.latest_run(db, org.id)
    if not run:
        run = reconcile.run_reconciliation(db, org, c.user.id)
        recs = db.query(reconcile.ReconciliationRecord).filter_by(run_id=run.id).all()
    pm = [r for r in recs if r.kind in ("payout_mismatch", "payout_delayed")]
    other = [r for r in recs if r not in pm]
    if pm:
        f.append({"title": f"{len(pm)} payout record(s) do not match the ledger or are delayed", "severity": "high", "detail": "; ".join(r.message for r in pm[:4]),
                  "refs": [ref("payout", r.ref_id) for r in pm], "action": "Review payouts in MoneyMatch before releasing further funds."})
    if other:
        f.append({"title": f"{len(other)} unreconciled transaction record(s)", "severity": "medium", "detail": "; ".join(r.message for r in other[:4]),
                  "refs": [ref(r.ref_type, r.ref_id) for r in other], "action": "Open MoneyMatch → Mismatches and resolve each record."})
    return {"data": {"issue_count": len(f)}, "findings": f}


TOOLS = {
    "get_marketplace_summary": (get_marketplace_summary, "reports.read", "Totals (payments, refunds, fees, commissions, settlements, payouts) for the last N days.", {"days": "integer"}),
    "get_transaction_details": (get_transaction_details, "transactions.read", "Details for an order number or PayPal capture id, incl. refunds and reconciliation flags.", {"reference": "string!"}),
    "get_seller_details": (get_seller_details, "sellers.read", "Seller profile and ledger balance by name or id.", {"seller": "string!"}),
    "get_seller_earnings": (get_seller_earnings, "reports.read", "Earnings/settlement figures for one seller or all.", {"seller": "string", "days": "integer"}),
    "get_refund_history": (get_refund_history, "refunds.read", "Recent refunds, optionally for one order number.", {"order": "string", "days": "integer"}),
    "investigate_refund": (investigate_refund, "refunds.write", "Run the refund anomaly investigation for a refund id.", {"refund_id": "string!"}),
    "get_open_disputes": (get_open_disputes, "disputes.read", "Open disputes with deadlines, evidence completeness and missing evidence.", {}),
    "get_dispute_details": (get_dispute_details, "disputes.read", "Detailed analysis of one dispute.", {"dispute_id": "string!"}),
    "collect_dispute_evidence": (collect_dispute_evidence, "disputes.read", "Which evidence exists for a dispute (from connected sources only).", {"dispute_id": "string!"}),
    "generate_dispute_response": (generate_dispute_response, "disputes.write", "Create a DRAFT response for merchant review. Never submits.", {"dispute_id": "string!"}),
    "get_reconciliation_report": (get_reconciliation_report, "reconcile.run", "Run reconciliation and return mismatches.", {}),
    "find_unmatched_transactions": (find_unmatched_transactions, "reconcile.read", "Unmatched payments/orders/refunds from the latest reconciliation.", {}),
    "get_delayed_payouts": (get_delayed_payouts, "reconcile.read", "Payouts that are delayed, failed, processing or awaiting approval.", {}),
    "get_financial_risk_summary": (get_financial_risk_summary, "reports.read", "Cross-module risk investigation with ranked findings.", {}),
}


def run_tool(c: Ctx, name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"data": {"error": f"Unknown tool {name}"}, "findings": []}
    fn, perm, _, params = TOOLS[name]
    if not c.can(perm) or not c.can("agent.use"):
        c.audit("agent.tool_denied", "tool", name, {"role": c.role}, actor_type="ai")
        return {"data": {"error": f"Not authorized: role '{c.role}' cannot use {name}"}, "findings": []}
    clean = {k: v for k, v in (args or {}).items() if k in params}  # drop unknown args
    try:
        res = fn(c, **clean)
    except (TypeError, ValueError, LookupError) as e:
        res = {"data": {"error": f"Bad arguments: {type(e).__name__}"}, "findings": []}
    c.audit("agent.tool_call", "tool", name, {"args": clean}, actor_type="ai")
    return res


def _schemas():
    out = []
    for n, (_, _, desc, params) in TOOLS.items():
        props = {k: {"type": "integer" if v.startswith("integer") else "string"} for k, v in params.items()}
        out.append({"type": "function", "function": {"name": n, "description": desc, "parameters": {"type": "object", "properties": props, "required": [k for k, v in params.items() if v.endswith("!")]}}})
    return out


def _route(q: str) -> list[tuple[str, dict]]:
    s = q.lower()
    m = re.search(r"\b(ORD-[A-Za-z0-9]+)\b", q, re.I)
    if m:
        return [("get_transaction_details", {"reference": m.group(1).upper()})]
    if re.search(r"prepare|draft", s) and "dispute" in s:
        return [("get_open_disputes", {}), ("__draft_all__", {})]
    if re.search(r"risk|lose|lost|loss|leak|money.*(at|away)|attention", s):
        return [("get_financial_risk_summary", {})]
    if re.search(r"dispute|chargeback", s):
        return [("get_open_disputes", {})]
    if re.search(r"payout|delay|settlement", s):
        return [("get_delayed_payouts", {}), ("get_seller_earnings", {})]
    if re.search(r"refund", s):
        return [("get_refund_history", {"days": 7 if "week" in s else 30}), ("get_financial_risk_summary", {})]
    if re.search(r"reconcil|mismatch|unmatched|match", s):
        return [("get_reconciliation_report", {})]
    if re.search(r"earn|revenue|commission|volume|month|week", s):
        return [("get_marketplace_summary", {"days": 7 if "week" in s else 30})]
    return [("get_financial_risk_summary", {})]


def _render(name, data) -> str:
    if name == "get_marketplace_summary":
        return f"Last {data['period_days']} days ({data['currency']}): payments {data['payments']['amount']} ({data['payments']['count']} captures), refunds {data['refunds']['amount']}, PayPal fees {str(data['fees']['amount']).lstrip('-')}, marketplace commission {data['commissions']['amount']}, pending seller settlements {data['pending_settlements']['amount']}, payouts {data['payouts']['amount']}."
    if name == "get_open_disputes":
        return f"{data['count']} open dispute(s):\n" + "\n".join(f"- {d['paypal_id']} ({d['reason']}, {d['amount']}): deadline {d['deadline']}, completeness {d['score']}/100, recommended: {d['recommendation']}" + (f"; missing {', '.join(d['missing'])}" if d["missing"] else "") for d in data["disputes"])
    if name == "get_delayed_payouts":
        return f"{len(data)} payout record(s) need attention:\n" + "\n".join(f"- {p['seller']}: {p['amount']} — {p['status']} (payout {p['payout']})" for p in data) if data else "No delayed or pending payouts."
    if name == "get_reconciliation_report":
        return f"Reconciliation checked {data['checked']} records and found {data['mismatches']} mismatch(es): {data['by_kind']}.\n" + "\n".join(f"- [{r['severity']}] {r['message']}" for r in data["records"][:10])
    if name == "get_refund_history":
        return f"{data['count']} refund record(s) in the period."
    if name == "get_transaction_details":
        return json.dumps(data, indent=1) if "error" not in data else data["error"]
    if name == "get_seller_earnings":
        return f"Earnings for {data['scope']}: seller earnings {data['seller_earnings']['amount']}, commission {data['commissions']['amount']}, pending settlements {data['pending_settlements']['amount']}, payouts {data['payouts']['amount']}."
    return ""


def ask(c: Ctx, question: str) -> dict:
    trace, findings = [], []

    def call(n, a):
        res = run_tool(c, n, a)
        trace.append({"tool": n, "args": a, "ok": "error" not in (res["data"] if isinstance(res["data"], dict) else {})})
        findings.extend(res["findings"])
        return res

    if llm.enabled():
        try:
            msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
            cl = llm.client()
            for _ in range(6):
                r = cl.chat.completions.create(model=llm.get_settings().openai_model, messages=msgs, tools=_schemas(), temperature=0)
                m = r.choices[0].message
                if not m.tool_calls:
                    out = {"mode": "llm", "answer": m.content or "", "findings": findings, "tool_calls": trace}
                    break
                msgs.append(m)
                for tc in m.tool_calls:
                    res = call(tc.function.name, json.loads(tc.function.arguments or "{}"))
                    msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps({"data": res["data"], "findings": res["findings"]}, default=str)[:12000]})
            else:
                out = {"mode": "llm", "answer": "Investigation step limit reached.", "findings": findings, "tool_calls": trace}
            return _save(c, question, out)
        except Exception as e:  # LLM failure -> deterministic path, clearly labelled
            trace.clear(); findings.clear()
            note = f"(LLM unavailable: {type(e).__name__}; using deterministic investigator)\n\n"
    else:
        note = ""
    parts = []
    for name, args in _route(question):
        if name == "__draft_all__":
            made = 0
            ready = [x for x in parts_data["disputes"] if x["recommendation"] in ("contest", "accept") and not x["missing"]]
            for d in ready:
                call("generate_dispute_response", {"dispute_id": d["id"]})
                made += 1
            todo = [x for x in parts_data["disputes"] if x not in ready]
            parts.append(f"Prepared {made} draft response(s) for disputes with sufficient evidence. {len(todo)} need more work: " +
                         "; ".join(f"{x['paypal_id']} ({', '.join(x['missing']) or x['recommendation']})" for x in todo) +
                         ". Drafts are NOT submitted; open DisputePilot to review and approve.")
            continue
        res = call(name, args)
        if name == "get_open_disputes":
            parts_data = res["data"]
        if isinstance(res["data"], dict) and "error" in res["data"]:
            parts.append(res["data"]["error"])
        elif isinstance(res["data"], dict) or isinstance(res["data"], list):
            t = _render(name, res["data"])
            if t:
                parts.append(t)
    if findings:
        findings.sort(key=lambda x: {"high": 0, "medium": 1}.get(x["severity"], 2))
        head = f"I investigated refunds, disputes, reconciliation and payouts and found {len(findings)} issue(s):"
        parts.insert(0, head + "\n" + "\n".join(f"{i}. **{x['title']}** — {x['detail']} → {x['action']}" for i, x in enumerate(findings, 1)))
    elif not parts:
        parts.append("I found nothing requiring attention in the connected records.")
    return _save(c, question, {"mode": "deterministic", "answer": note + "\n\n".join(parts), "findings": findings, "tool_calls": trace})


def _save(c: Ctx, q: str, out: dict) -> dict:
    inv = AiInvestigation(org_id=c.org.id, user_id=c.user.id, question=q[:2000], answer={"answer": out["answer"], "findings": out["findings"]}, tool_calls=out["tool_calls"])
    c.db.add(inv)
    c.audit("agent.question", "ai_investigation", None, {"q": q[:200], "tools": [t["tool"] for t in out["tool_calls"]]}, actor_type="ai")
    return out
