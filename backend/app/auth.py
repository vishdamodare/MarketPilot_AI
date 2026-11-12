"""Authentication, RBAC and request context. Every protected route depends on `ctx(<permission>)`."""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AuditLog, Organization, OrganizationMember, User

ALL = {
    "dashboard", "sellers.read", "sellers.write", "commission.write", "transactions.read", "orders.write",
    "refunds.read", "refunds.write", "refunds.approve", "disputes.read", "disputes.write", "disputes.submit",
    "reconcile.read", "reconcile.run", "reports.read", "agent.use", "integrations.manage", "settings.manage",
    "payouts.approve", "audit.read", "notifications.read", "portal.read",
}
ROLE_PERMS: dict[str, set[str]] = {
    "admin": ALL - {"portal.read"},
    "finance_manager": {
        "dashboard", "sellers.read", "transactions.read", "refunds.read", "refunds.write", "refunds.approve",
        "disputes.read", "disputes.write", "reconcile.read", "reconcile.run", "reports.read", "agent.use",
        "notifications.read", "audit.read",
    },
    "seller": {"portal.read", "notifications.read"},
}


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode()[:72], h.encode())
    except ValueError:
        return False


def make_token(user_id: uuid.UUID) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=s.token_ttl_minutes)
    return jwt.encode({"sub": str(user_id), "exp": exp}, s.secret_key, algorithm="HS256")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("mp_session")
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        uid = uuid.UUID(jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "Invalid session")
    user = db.get(User, uid)
    if not user or not user.is_active:
        raise HTTPException(401, "Invalid session")
    return user


def csrf_guard(request: Request, x_requested_with: str | None = Header(None)):
    """Cookie auth + SameSite=Lax + required custom header on mutating requests (browsers can't send it cross-site without CORS preflight)."""
    if request.method not in ("GET", "HEAD", "OPTIONS") and x_requested_with != "marketpilot":
        raise HTTPException(403, "Missing CSRF header")


@dataclass
class Ctx:
    db: Session
    user: User
    org: Organization
    member: OrganizationMember

    @property
    def role(self) -> str:
        return self.member.role

    @property
    def seller_scope(self) -> uuid.UUID | None:
        return self.member.seller_id if self.role == "seller" else None

    def can(self, perm: str) -> bool:
        return perm in ROLE_PERMS.get(self.role, set())

    def audit(self, action: str, entity: str | None = None, entity_id=None, details: dict | None = None, actor_type="user"):
        self.db.add(AuditLog(org_id=self.org.id, user_id=self.user.id, actor_type=actor_type, action=action,
                             entity=entity, entity_id=str(entity_id) if entity_id else None, details=details))


def ctx(perm: str):
    """Dependency factory: verifies session, CSRF header, org membership (from X-Org-Id) and permission, in the backend."""
    def dep(request: Request, x_org_id: str | None = Header(None), user: User = Depends(current_user),
            db: Session = Depends(get_db), _=Depends(csrf_guard)) -> Ctx:
        try:
            org_id = uuid.UUID(x_org_id or "")
        except ValueError:
            raise HTTPException(400, "X-Org-Id header required")
        member = db.query(OrganizationMember).filter_by(org_id=org_id, user_id=user.id).first()
        if not member:
            raise HTTPException(403, "Not a member of this organization")
        c = Ctx(db, user, db.get(Organization, org_id), member)
        if not c.can(perm):
            raise HTTPException(403, f"Role '{member.role}' lacks permission '{perm}'")
        return c
    return dep
