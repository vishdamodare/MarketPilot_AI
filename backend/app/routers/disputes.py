import uuid
from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import Ctx, ctx
from ..models import Dispute, DisputeResponse
from ..services import disputes as svc
from ..services import paypal
from ..util import d

router = APIRouter(prefix="/api/disputes")


def _get(c: Ctx, did) -> Dispute:
    x = c.db.query(Dispute).filter_by(id=did, org_id=c.org.id).first()
    if not x:
        raise HTTPException(404, "Dispute not found")
    return x


def _err(c, e):
    c.db.commit()  # keep audit trail of failures
    raise HTTPException(e.code, str(e))


@router.get("")
def list_disputes(c: Ctx = Depends(ctx("disputes.read"))):
    ds = c.db.query(Dispute).filter_by(org_id=c.org.id).order_by(Dispute.response_due_date.asc().nulls_last()).all()
    by = defaultdict(lambda: {"count": 0, "amount": Decimal("0")})
    for x in ds:
        by[x.status]["count"] += 1
        by[x.status]["amount"] += x.amount
    return {"stats": {k: {"count": v["count"], "amount": str(v["amount"])} for k, v in by.items()},
            "disputes": [d(x, "raw") | {"urgency": svc.urgency(x)} for x in ds]}


@router.post("/sync")
def sync(c: Ctx = Depends(ctx("disputes.write"))):
    if c.org.mode == "demo":
        raise HTTPException(409, "Demo orgs contain simulated disputes only; PayPal sync is for Sandbox orgs.")
    try:
        n = svc.sync_from_paypal(c.db, c.org)
    except paypal.PayPalError as e:
        raise HTTPException(502, f"PayPal Disputes API error: {e}")
    c.audit("disputes.synced", details={"count": n})
    c.db.commit()
    return {"synced": n}


@router.get("/{did}")
def detail(did: uuid.UUID, c: Ctx = Depends(ctx("disputes.read"))):
    x = _get(c, did)
    a = svc.analyze(c.db, c.org, x)
    a["evidence"] = list(a["evidence"].values())
    resp = c.db.query(DisputeResponse).filter_by(dispute_id=x.id).order_by(DisputeResponse.created_at.desc()).first()
    return {"dispute": d(x, "raw"), "analysis": a, "response": d(resp) if resp else None, "simulated": x.is_simulated}


@router.post("/{did}/generate")
def generate(did: uuid.UUID, c: Ctx = Depends(ctx("disputes.write"))):
    x = _get(c, did)
    svc.generate_response(c, x)
    c.db.commit()
    return detail(did, c)


class Edit(BaseModel):
    body: str = Field(min_length=10, max_length=20000)
    action: str | None = Field(None, pattern="^(contest|accept)$")


def _resp(c, did, rid):
    _get(c, did)
    r = c.db.query(DisputeResponse).filter_by(id=rid, dispute_id=did).first()
    if not r:
        raise HTTPException(404, "Response not found")
    return r


@router.put("/{did}/responses/{rid}")
def edit(did: uuid.UUID, rid: uuid.UUID, b: Edit, c: Ctx = Depends(ctx("disputes.write"))):
    try:
        svc.edit_response(c, _resp(c, did, rid), b.body, b.action)
    except svc.DisputeError as e:
        _err(c, e)
    c.db.commit()
    return detail(did, c)


@router.post("/{did}/responses/{rid}/approve")
def approve(did: uuid.UUID, rid: uuid.UUID, c: Ctx = Depends(ctx("disputes.submit"))):
    try:
        svc.approve_response(c, _resp(c, did, rid))
    except svc.DisputeError as e:
        _err(c, e)
    c.db.commit()
    return detail(did, c)


@router.post("/{did}/responses/{rid}/submit")
def submit(did: uuid.UUID, rid: uuid.UUID, c: Ctx = Depends(ctx("disputes.submit"))):
    try:
        svc.submit_response(c, _get(c, did), _resp(c, did, rid))
    except svc.DisputeError as e:
        _err(c, e)
    c.db.commit()
    return detail(did, c)
