import csv
import io
import uuid
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..auth import Ctx, ctx
from ..models import FinancialLedger, Seller, now
from ..services import reconcile
from ..util import d, val

router = APIRouter(prefix="/api/moneymatch")


def _run(c, run, recs):
    return {"run": d(run) if run else None, "records": [d(r) for r in recs]}


@router.get("/summary")
def summary(days: int = 30, c: Ctx = Depends(ctx("reconcile.read"))):
    days = max(1, min(days, 365))
    return {"days": days, "currency": c.org.currency, "metrics": reconcile.summary(c.db, c.org.id, now() - timedelta(days=days), None)}


@router.get("/trace")
def trace(metric: str, days: int = 30, seller_id: uuid.UUID | None = None, c: Ctx = Depends(ctx("reconcile.read"))):
    """Drill-down: the exact source rows whose SUM is the dashboard number."""
    if metric not in reconcile.METRICS:
        raise HTTPException(422, "Unknown metric")
    rows = reconcile.rows(c.db, c.org.id, metric, now() - timedelta(days=max(1, min(days, 365))), None, seller_id)
    return {"metric": metric, "total": str(sum((r["amount"] for r in rows), Decimal("0"))), "rows": [{**r, "amount": str(r["amount"])} for r in rows]}


@router.post("/run")
def run(c: Ctx = Depends(ctx("reconcile.run"))):
    r = reconcile.run_reconciliation(c.db, c.org, c.user.id)
    c.audit("reconciliation.run", "reconciliation_run", r.id, {"mismatches": r.mismatches})
    c.db.commit()
    return _run(c, *reconcile.latest_run(c.db, c.org.id))


@router.get("/latest")
def latest(c: Ctx = Depends(ctx("reconcile.read"))):
    return _run(c, *reconcile.latest_run(c.db, c.org.id))


@router.get("/ledger")
def ledger(c: Ctx = Depends(ctx("reconcile.read"))):
    from sqlalchemy import func
    sums = dict(c.db.query(FinancialLedger.account, func.coalesce(func.sum(FinancialLedger.amount), 0)).filter_by(org_id=c.org.id).group_by(FinancialLedger.account).all())
    cash, seller, platform = (Decimal(sums.get(k, 0)) for k in ("cash", "seller", "platform"))
    names = {s.id: s.business_name for s in c.db.query(Seller).filter_by(org_id=c.org.id)}
    rows = c.db.query(FinancialLedger).filter_by(org_id=c.org.id).order_by(FinancialLedger.created_at.desc()).limit(200)
    return {"totals": {"cash": str(cash), "seller_liability": str(seller), "platform": str(platform)},
            "invariant": {"cash_minus_seller_minus_platform": str(cash - seller - platform), "note": "Must be 0.00 for ledger-posted activity: cash = seller liability + platform revenue."},
            "entries": [d(e) | {"seller": names.get(e.seller_id)} for e in rows]}


def _safe(v):
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") and not s.replace(".", "", 1).lstrip("-").isdigit() else s  # CSV formula-injection guard


@router.get("/reports/{kind}")
def reports(kind: str, format: str = "json", seller_id: uuid.UUID | None = None, c: Ctx = Depends(ctx("reports.read"))):
    try:
        rep = reconcile.report(c.db, c.org, kind, seller_id)
    except ValueError:
        raise HTTPException(404, "Unknown report")
    c.audit("report.generated", "report", kind, {"format": format})
    c.db.commit()
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(rep["columns"])
        for r in rep["rows"]:
            w.writerow([_safe(x) for x in r])
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{kind}-report.csv"'})
    return rep
