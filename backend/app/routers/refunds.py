import uuid
from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import Ctx, ctx
from ..models import Order, Refund, RefundInvestigation
from ..services import refunds as svc
from ..util import d

router = APIRouter(prefix="/api/refunds")


def _row(c, r: Refund):
    o = c.db.get(Order, r.order_id) if r.order_id else None
    inv = c.db.query(RefundInvestigation).filter_by(refund_id=r.id).first()
    return d(r) | {"order_number": o.number if o else None, "risk": inv.risk_level if inv else None, "has_investigation": bool(inv)}


@router.get("")
def list_refunds(c: Ctx = Depends(ctx("refunds.read"))):
    rs = c.db.query(Refund).filter_by(org_id=c.org.id).order_by(Refund.created_at.desc()).limit(300).all()
    by = defaultdict(lambda: {"count": 0, "amount": Decimal("0")})
    for r in rs:
        k = "pending" if r.status in ("requested", "needs_review", "pending_approval", "escalated", "processing") else r.status
        by[k]["count"] += 1
        by[k]["amount"] += r.amount
    trend = defaultdict(lambda: Decimal("0"))
    for r in rs:
        trend[r.created_at.strftime("%m-%d")] += r.amount
    return {"stats": {k: {"count": v["count"], "amount": str(v["amount"])} for k, v in by.items()}, "total": len(rs),
            "total_amount": str(sum((r.amount for r in rs), Decimal("0"))),
            "trend": [{"day": k, "amount": float(v)} for k, v in sorted(trend.items())][-30:], "refunds": [_row(c, r) for r in rs]}


class RefundIn(BaseModel):
    order_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    reason: str | None = Field(None, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)


@router.post("/validate")
def validate(b: RefundIn, c: Ctx = Depends(ctx("refunds.write"))):
    o = c.db.query(Order).filter_by(id=b.order_id, org_id=c.org.id).first()
    return svc.validate(c.db, c.org.id, o, b.amount)


@router.post("")
def request_refund(b: RefundIn, c: Ctx = Depends(ctx("refunds.write"))):
    try:
        r = svc.request_refund(c, b.order_id, b.amount, b.reason, b.idempotency_key)
    except svc.RefundError as e:
        c.db.rollback()
        raise HTTPException(e.status_code, {"message": str(e), "checks": e.checks})
    c.db.commit()
    return _row(c, r)


@router.get("/{rid}")
def detail(rid: uuid.UUID, c: Ctx = Depends(ctx("refunds.read"))):
    r = c.db.query(Refund).filter_by(id=rid, org_id=c.org.id).first()
    if not r:
        raise HTTPException(404, "Refund not found")
    inv = c.db.query(RefundInvestigation).filter_by(refund_id=r.id).first()
    return {"refund": _row(c, r), "investigation": d(inv) if inv else None}


def _act(fn, c, rid, *a):
    try:
        r = fn(c, rid, *a)
        c.db.commit()
    except svc.RefundError as e:
        c.db.commit()  # keep audit entries such as refund.failed
        raise HTTPException(e.status_code, {"message": str(e), "checks": e.checks})
    return _row(c, r)


@router.post("/{rid}/investigate")
def investigate(rid: uuid.UUID, c: Ctx = Depends(ctx("refunds.write"))):
    r = c.db.query(Refund).filter_by(id=rid, org_id=c.org.id).first()
    if not r:
        raise HTTPException(404, "Refund not found")
    svc.investigate(c.db, c.org, r)
    c.audit("refund.investigated", "refund", r.id)
    c.db.commit()
    return detail(rid, c)


@router.post("/{rid}/approve")
def approve(rid: uuid.UUID, c: Ctx = Depends(ctx("refunds.approve"))):
    return _act(svc.approve_refund, c, rid)


class Note(BaseModel):
    note: str | None = Field(None, max_length=500)


@router.post("/{rid}/reject")
def reject(rid: uuid.UUID, b: Note, c: Ctx = Depends(ctx("refunds.approve"))):
    return _act(svc.reject_refund, c, rid, b.note)


@router.post("/{rid}/escalate")
def escalate(rid: uuid.UUID, c: Ctx = Depends(ctx("refunds.write"))):
    return _act(svc.escalate_refund, c, rid)
