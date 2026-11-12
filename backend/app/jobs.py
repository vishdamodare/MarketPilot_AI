"""Scheduled sync. One cycle, per organization (each in its own transaction so one failure never blocks others):
  1. sandbox orgs with PayPal credentials: pull disputes, poll refunds stuck in 'processing' (catches missed webhooks)
  2. reconcile, and alert when mismatches grew
  3. alert on dispute deadlines (critical / overdue), de-duplicated for 24 h
  4. prune old reconciliation runs
Triggers: in-process thread every SYNC_INTERVAL_SECONDS (0 = off), `python -m app.jobs` (cron), POST /api/jobs/run.
A Postgres advisory lock makes concurrent workers/cron safe: only one cycle runs at a time.
# ponytail: thread + advisory lock instead of Celery/Redis; move to a real queue when jobs must retry independently or scale out.
"""
import json
import logging
import threading
import time
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from .auth import Ctx, ctx
from .config import get_settings
from .db import SessionLocal, engine
from .models import (AuditLog, Dispute, IntegrationSetting, Notification, Organization, ReconciliationRecord, ReconciliationRun, Refund, now)
from .services import disputes as dsp
from .services import finance, paypal, reconcile
from .services import refunds as rfd
from .util import val

log = logging.getLogger("jobs")
router = APIRouter(prefix="/api/jobs")
LOCK_ID = 727001
KEEP_RUNS = 50


def _pref(db: Session, org_id, key: str) -> bool:
    s = db.query(IntegrationSetting).filter_by(org_id=org_id).first()
    return (s.notification_prefs or {}).get(key, True) if s else True


def _poll_refunds(db: Session, org) -> int:
    """PayPal is the source of truth for refunds left 'processing'; finalise (ledger exactly once) or fail them."""
    pp, n = paypal.get_client(), 0
    for r in db.query(Refund).filter(Refund.org_id == org.id, Refund.status == "processing", Refund.paypal_refund_id.isnot(None)):
        status = pp.get_refund(r.paypal_refund_id).get("status")
        if status == "COMPLETED":
            rfd._finalize(db, org, r, r.paypal_refund_id, None)
            n += 1
        elif status in ("FAILED", "CANCELLED"):
            r.status = "failed"
            n += 1
    return n


def _deadline_alerts(db: Session, org) -> int:
    if not _pref(db, org.id, "dispute_deadlines"):
        return 0
    made = 0
    for dp in db.query(Dispute).filter(Dispute.org_id == org.id, Dispute.status.in_(("open", "waiting_merchant"))):
        lvl = dsp.urgency(dp)["level"]
        if lvl not in ("critical", "overdue"):
            continue
        title = f"Dispute {dp.paypal_dispute_id} response {'is OVERDUE' if lvl == 'overdue' else 'due within 48 hours'}"
        if db.query(Notification).filter(Notification.org_id == org.id, Notification.title == title, Notification.created_at > now() - timedelta(hours=24)).first():
            continue
        finance.notify(db, org.id, title, f"{dp.amount} {dp.currency} at stake. Open DisputePilot to review the draft and approve.", "critical", "/disputepilot")
        made += 1
    return made


def _prune(db: Session, org):
    keep = [r[0] for r in db.query(ReconciliationRun.id).filter_by(org_id=org.id).order_by(ReconciliationRun.started_at.desc()).limit(KEEP_RUNS)]
    old = [r[0] for r in db.query(ReconciliationRun.id).filter(ReconciliationRun.org_id == org.id, ~ReconciliationRun.id.in_(keep))]
    if old:
        db.query(ReconciliationRecord).filter(ReconciliationRecord.run_id.in_(old)).delete(synchronize_session=False)
        db.query(ReconciliationRun).filter(ReconciliationRun.id.in_(old)).delete(synchronize_session=False)


def sync_org(db: Session, org) -> dict:
    out: dict = {"org": org.name, "mode": org.mode}
    st = db.query(IntegrationSetting).filter_by(org_id=org.id, provider="paypal").first()
    if not st:
        st = IntegrationSetting(org_id=org.id, provider="paypal")
        db.add(st)
    if org.mode == "sandbox":
        if get_settings().paypal_configured:
            try:
                out["disputes_synced"] = dsp.sync_from_paypal(db, org)
                out["refunds_updated"] = _poll_refunds(db, org)
                st.last_sync_at, st.last_error = now(), None
            except paypal.PayPalError as e:  # record it, keep going: reconciliation still runs on local data
                st.last_error = str(e)[:300]
                out["paypal_error"] = st.last_error
        else:
            out["paypal"] = "skipped: credentials not configured"
    prev = db.query(ReconciliationRun).filter_by(org_id=org.id).order_by(ReconciliationRun.started_at.desc()).first()
    run = reconcile.run_reconciliation(db, org)
    out["mismatches"] = run.mismatches
    if prev is not None and run.mismatches > prev.mismatches and _pref(db, org.id, "recon_mismatches"):
        finance.notify(db, org.id, f"Reconciliation found {run.mismatches - prev.mismatches} new mismatch(es)", f"{run.mismatches} open in total. Review them in MoneyMatch.", "warning", "/moneymatch")
    out["deadline_alerts"] = _deadline_alerts(db, org)
    _prune(db, org)
    db.add(AuditLog(org_id=org.id, actor_type="system", action="job.sync_completed", details=out))
    return out


def run_cycle() -> list[dict]:
    with SessionLocal() as lock_db:
        pg = engine.dialect.name == "postgresql"
        if pg and not lock_db.execute(text("select pg_try_advisory_lock(:i)"), {"i": LOCK_ID}).scalar():
            return [{"skipped": "another worker holds the sync lock"}]
        try:
            ids = [o.id for o in lock_db.query(Organization)]
            results = []
            for oid in ids:
                with SessionLocal() as db:
                    org = db.get(Organization, oid)
                    try:
                        results.append(sync_org(db, org))
                        db.commit()
                    except Exception as e:
                        db.rollback()
                        log.exception("sync failed for org %s", oid)
                        results.append({"org": org.name, "error": type(e).__name__})
            return results
        finally:
            if pg:
                lock_db.execute(text("select pg_advisory_unlock(:i)"), {"i": LOCK_ID})


_thread: threading.Thread | None = None


def start_scheduler():
    global _thread
    every = get_settings().sync_interval_seconds
    if every <= 0 or _thread:
        return

    def loop():
        time.sleep(min(30, every))  # let the app settle first
        while True:
            try:
                run_cycle()
            except Exception:
                log.exception("sync cycle crashed")
            time.sleep(every)

    _thread = threading.Thread(target=loop, daemon=True, name="mp-sync")
    _thread.start()


@router.get("/status")
def status(c: Ctx = Depends(ctx("dashboard"))):
    last = c.db.query(AuditLog).filter_by(org_id=c.org.id, action="job.sync_completed").order_by(AuditLog.created_at.desc()).first()
    every = get_settings().sync_interval_seconds
    return {"scheduler_enabled": every > 0, "interval_seconds": every, "paypal_configured": get_settings().paypal_configured,
            "last_run": {"at": val(last.created_at), "result": last.details} if last else None}


@router.post("/run")
def run_now(c: Ctx = Depends(ctx("integrations.manage"))):
    out = sync_org(c.db, c.org)
    c.audit("job.run_manually", "organization", c.org.id)
    c.db.commit()
    return out


if __name__ == "__main__":  # cron: */15 * * * * cd backend && .venv/bin/python -m app.jobs
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run_cycle(), default=str, indent=1))
