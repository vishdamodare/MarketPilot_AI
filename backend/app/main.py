import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .auth import ROLE_PERMS
from .db import Base, SessionLocal, engine
from .models import Permission, Role
from . import jobs
from .routers import agent, auth_org, core, disputes, moneymatch, paypal, refunds, store

logging.basicConfig(level=logging.INFO, format='{"t":"%(asctime)s","lvl":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')

app = FastAPI(title="MarketPilot AI", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    r = await call_next(request)
    r.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})
    return r


for m in (auth_org, core, refunds, disputes, moneymatch, agent, paypal, store, jobs):
    app.include_router(m.router)
app.include_router(store.keys_router)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(engine)  # dev convenience; production uses `alembic upgrade head`
    with SessionLocal() as db:  # mirror the code-defined role matrix into roles/permissions tables
        for name, perms in ROLE_PERMS.items():
            role = db.query(Role).filter_by(name=name).first() or Role(name=name)
            db.add(role)
            db.flush()
            have = {p.code for p in db.query(Permission).filter_by(role_id=role.id)}
            db.add_all(Permission(role_id=role.id, code=c) for c in perms - have)
        db.commit()
    jobs.start_scheduler()


@app.get("/api/health")
def health():
    return {"ok": True}
