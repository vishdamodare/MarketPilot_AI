import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..auth import ROLE_PERMS, Ctx, csrf_guard, ctx, current_user, hash_password, make_token, verify_password
from ..config import get_settings
from ..db import get_db
from ..models import IntegrationSetting, Organization, OrganizationMember, User
from ..services import demo
from ..util import d

router = APIRouter(prefix="/api")
log = logging.getLogger("auth")

_hits: dict[str, list[float]] = defaultdict(list)


def rate_limit(key: str, n: int = 10, per: int = 60):
    # ponytail: in-process limiter; use Redis (slowapi/redis) when running >1 worker
    t = time.time()
    _hits[key] = [x for x in _hits[key] if t - x < per]
    if len(_hits[key]) >= n:
        raise HTTPException(429, "Too many attempts, slow down")
    _hits[key].append(t)


class Signup(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    org_name: str = Field(min_length=1, max_length=120)
    mode: str = Field("sandbox", pattern="^(demo|sandbox)$")


class Login(BaseModel):
    email: EmailStr
    password: str


def _set_cookie(resp: Response, user_id):
    s = get_settings()
    resp.set_cookie("mp_session", make_token(user_id), httponly=True, samesite="lax", secure=s.cookie_secure, max_age=s.token_ttl_minutes * 60, path="/")


def _me(db: Session, user: User) -> dict:
    ms = db.query(OrganizationMember, Organization).join(Organization, Organization.id == OrganizationMember.org_id).filter(OrganizationMember.user_id == user.id).all()
    return {"user": {"id": str(user.id), "name": user.name, "email": user.email},
            "memberships": [{"org_id": str(o.id), "org_name": o.name, "mode": o.mode, "currency": o.currency, "role": m.role,
                             "seller_id": str(m.seller_id) if m.seller_id else None, "permissions": sorted(ROLE_PERMS[m.role])} for m, o in ms]}


def _new_org(db: Session, user: User, name: str, mode: str) -> Organization:
    org = Organization(name=name, mode=mode)
    db.add(org)
    db.flush()
    db.add(OrganizationMember(org_id=org.id, user_id=user.id, role="admin"))
    db.add(IntegrationSetting(org_id=org.id, provider="paypal"))
    if mode == "demo":
        demo.seed_demo(db, org, user.id)
    return org


@router.post("/auth/signup", dependencies=[Depends(csrf_guard)])
def signup(body: Signup, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit(f"signup:{request.client.host if request.client else '?'}", 5, 300)
    if db.query(User).filter_by(email=body.email.lower()).first():
        raise HTTPException(409, "An account with this email already exists")
    user = User(email=body.email.lower(), name=body.name, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    _new_org(db, user, body.org_name, body.mode)
    db.commit()
    _set_cookie(response, user.id)
    return _me(db, user)


@router.post("/auth/login", dependencies=[Depends(csrf_guard)])
def login(body: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit(f"login:{body.email.lower()}", 8, 60)
    user = db.query(User).filter_by(email=body.email.lower()).first()
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")  # same message for unknown email / wrong password
    _set_cookie(response, user.id)
    return _me(db, user)


@router.post("/auth/logout", dependencies=[Depends(csrf_guard)])
def logout(response: Response):
    response.delete_cookie("mp_session", path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _me(db, user)


class Forgot(BaseModel):
    email: EmailStr


class Reset(BaseModel):
    token: str
    password: str = Field(min_length=10, max_length=128)


@router.post("/auth/forgot", dependencies=[Depends(csrf_guard)])
def forgot(body: Forgot, request: Request, db: Session = Depends(get_db)):
    rate_limit(f"forgot:{body.email.lower()}", 3, 300)
    user = db.query(User).filter_by(email=body.email.lower()).first()
    if user:
        s = get_settings()
        tok = jwt.encode({"sub": str(user.id), "purpose": "reset", "exp": datetime.now(timezone.utc) + timedelta(minutes=15), "pw": user.password_hash[-8:]}, s.secret_key, algorithm="HS256")
        # No SMTP configured -> the link goes to the server log (dev). With SMTP, send it by email.
        log.warning("PASSWORD RESET LINK (dev; email delivery not configured): /reset?token=%s", tok)
    return {"ok": True, "message": "If that account exists, a reset link has been sent."}  # same answer either way


@router.post("/auth/reset", dependencies=[Depends(csrf_guard)])
def reset(body: Reset, db: Session = Depends(get_db)):
    try:
        p = jwt.decode(body.token, get_settings().secret_key, algorithms=["HS256"])
        user = db.get(User, uuid.UUID(p["sub"]))
        if p.get("purpose") != "reset" or not user or p["pw"] != user.password_hash[-8:]:  # single-use: dies once the password changes
            raise ValueError
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(400, "Invalid or expired reset link")
    user.password_hash = hash_password(body.password)
    db.commit()
    return {"ok": True}


class NewOrg(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mode: str = Field(pattern="^(demo|sandbox)$")


@router.post("/orgs", dependencies=[Depends(csrf_guard)])
def create_org(body: NewOrg, user: User = Depends(current_user), db: Session = Depends(get_db)):
    org = _new_org(db, user, body.name, body.mode)
    db.commit()
    return {"org_id": str(org.id), **_me(db, user)}


class OrgPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    currency: str | None = Field(None, pattern="^[A-Z]{3}$")
    default_commission_pct: Decimal | None = Field(None, ge=0, le=100)
    marketplace_type: str | None = Field(None, max_length=64)


@router.patch("/org")
def patch_org(body: OrgPatch, c: Ctx = Depends(ctx("settings.manage"))):
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(c.org, k, v)
    c.audit("org.updated", "organization", c.org.id, body.model_dump(exclude_none=True, mode="json"))
    c.db.commit()
    return d(c.org)


@router.get("/org")
def get_org(c: Ctx = Depends(ctx("notifications.read"))):
    return d(c.org)


@router.post("/demo/seed")
def seed(c: Ctx = Depends(ctx("settings.manage"))):
    if c.org.mode != "demo":
        raise HTTPException(409, "Demo data can only be loaded into a Demo organization (never mixed with Sandbox data)")
    res = demo.seed_demo(c.db, c.org, c.user.id)
    c.audit("demo.reset_and_seeded", "organization", c.org.id, {k: v for k, v in res.items() if k != "seller_login"})
    c.db.commit()
    return res


class Member(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    role: str = Field(pattern="^(admin|finance_manager)$")


@router.get("/team")
def team(c: Ctx = Depends(ctx("settings.manage"))):
    rows = c.db.query(OrganizationMember, User).join(User, User.id == OrganizationMember.user_id).filter(OrganizationMember.org_id == c.org.id).all()
    return [{"user_id": str(u.id), "name": u.name, "email": u.email, "role": m.role} for m, u in rows]


@router.post("/team")
def add_member(body: Member, c: Ctx = Depends(ctx("settings.manage"))):
    u = c.db.query(User).filter_by(email=body.email.lower()).first()
    if not u:
        u = User(email=body.email.lower(), name=body.name, password_hash=hash_password(body.password))
        c.db.add(u)
        c.db.flush()
    if c.db.query(OrganizationMember).filter_by(org_id=c.org.id, user_id=u.id).first():
        raise HTTPException(409, "Already a member")
    c.db.add(OrganizationMember(org_id=c.org.id, user_id=u.id, role=body.role))
    c.audit("team.member_added", "user", u.id, {"role": body.role})
    c.db.commit()
    return {"ok": True}
