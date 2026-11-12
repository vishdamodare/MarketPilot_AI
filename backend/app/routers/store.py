"""Storefront API: a real store pushes orders, shipments and customer messages here with a Bearer API key.

- Auth is the key alone (no cookie, so no CSRF surface). Keys are per-organization, stored as SHA-256 hashes, revocable.
- Orders are priced server-side from the products table; `external_id` makes order creation idempotent.
- Shipments/tracking/messages pushed here are the REAL evidence DisputePilot later uses (nothing is ever invented).
"""
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import Ctx, ctx
from ..db import get_db
from ..models import (ApiKey, AuditLog, CustomerMessage, Order, Organization, PaypalCapture, PaypalOrder, Product, Refund, Seller,
                      SellerAllocation, Shipment, TrackingEvent, now)
from ..services import finance
from ..util import d
from . import paypal as pp_router
from .auth_org import rate_limit

router = APIRouter(prefix="/api/store/v1")
keys_router = APIRouter(prefix="/api/settings/api-keys")


def hash_key(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


@dataclass
class ApiCtx(Ctx):
    key: ApiKey | None = None

    def audit(self, action, entity=None, entity_id=None, details=None, actor_type="api"):
        self.db.add(AuditLog(org_id=self.org.id, user_id=None, actor_type="api", action=action, entity=entity,
                             entity_id=str(entity_id) if entity_id else None, details={**(details or {}), "key": self.key.prefix}))


def store_ctx(authorization: str | None = Header(None), db: Session = Depends(get_db)) -> ApiCtx:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing Bearer API key")
    key = db.query(ApiKey).filter_by(key_hash=hash_key(authorization[7:].strip()), revoked_at=None).first()
    if not key:
        raise HTTPException(401, "Invalid or revoked API key")
    rate_limit(f"key:{key.id}", 120, 60)
    key.last_used_at = now()
    return ApiCtx(db, SimpleNamespace(id=None), db.get(Organization, key.org_id), SimpleNamespace(role="api", seller_id=None), key)


# ---------------------------------------------------------------- key management (dashboard, admin only)
class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@keys_router.post("")
def create_key(b: KeyIn, c: Ctx = Depends(ctx("settings.manage"))):
    token = "mpk_" + secrets.token_urlsafe(32)
    k = ApiKey(org_id=c.org.id, name=b.name, prefix=token[:8], key_hash=hash_key(token), created_by=c.user.id)
    c.db.add(k)
    c.db.flush()
    c.audit("api_key.created", "api_key", k.id, {"name": b.name})
    c.db.commit()
    return {**d(k, "key_hash"), "key": token, "note": "Copy this key now. It is shown only once and cannot be recovered."}


@keys_router.get("")
def list_keys(c: Ctx = Depends(ctx("settings.manage"))):
    return [d(k, "key_hash") for k in c.db.query(ApiKey).filter_by(org_id=c.org.id).order_by(ApiKey.created_at.desc())]


@keys_router.delete("/{kid}")
def revoke_key(kid: uuid.UUID, c: Ctx = Depends(ctx("settings.manage"))):
    k = c.db.query(ApiKey).filter_by(id=kid, org_id=c.org.id).first()
    if not k:
        raise HTTPException(404, "Key not found")
    k.revoked_at = k.revoked_at or now()
    c.audit("api_key.revoked", "api_key", k.id)
    c.db.commit()
    return d(k, "key_hash")


# ---------------------------------------------------------------- storefront endpoints
class LineIn(BaseModel):
    product_id: uuid.UUID
    qty: int = Field(1, ge=1, le=1000)


class StoreOrderIn(BaseModel):
    external_id: str = Field(min_length=1, max_length=128, description="Your order id; resubmitting it returns the original order")
    customer_id: str = Field(min_length=1, max_length=64)
    customer_email: EmailStr
    address: str | None = Field(None, max_length=255)
    lines: list[LineIn] = Field(min_length=1, max_length=50)


def view(db: Session, o: Order) -> dict:
    cap = db.query(PaypalCapture).filter_by(org_id=o.org_id, order_id=o.id).first()
    return {"id": str(o.id), "number": o.number, "external_id": o.external_id, "status": o.status, "total": str(o.total), "currency": o.currency,
            "allocations": [{"seller_id": str(a.seller_id), "gross": str(a.gross), "commission": str(a.commission), "net": str(a.net)} for a in db.query(SellerAllocation).filter_by(order_id=o.id)],
            "payment": {"capture_id": cap.capture_id, "status": cap.status, "simulated": cap.is_simulated} if cap else None,
            "refunds": [{"id": str(r.id), "amount": str(r.amount), "status": r.status} for r in db.query(Refund).filter_by(order_id=o.id)]}


def find(c: ApiCtx, ref: str) -> Order:
    try:
        o = c.db.query(Order).filter_by(id=uuid.UUID(ref), org_id=c.org.id).first()
    except ValueError:
        o = None
    o = o or c.db.query(Order).filter_by(external_id=ref, org_id=c.org.id).first()
    if not o:
        raise HTTPException(404, "Order not found")
    return o


@router.get("/products")
def products(c: ApiCtx = Depends(store_ctx)):
    q = c.db.query(Product, Seller).join(Seller, Seller.id == Product.seller_id).filter(Product.org_id == c.org.id, Seller.status == "active")
    c.db.commit()
    return [{"id": str(p.id), "name": p.name, "price": str(p.price), "currency": c.org.currency, "seller": s.business_name} for p, s in q]


@router.post("/orders")
def create_order(b: StoreOrderIn, response: Response, c: ApiCtx = Depends(store_ctx)):
    """Idempotent on external_id. Prices come from the products table; any client-sent price is ignored."""
    existing = c.db.query(Order).filter_by(org_id=c.org.id, external_id=b.external_id).first()
    if existing:
        c.db.commit()
        return {**view(c.db, existing), "replayed": True}
    try:
        o = finance.create_order(c.db, c.org, f"ORD-{uuid.uuid4().hex[:8].upper()}", b.customer_id, b.customer_email,
                                 [(l.product_id, l.qty) for l in b.lines], b.address)
        o.external_id = b.external_id
        c.audit("store.order_created", "order", o.id, {"external_id": b.external_id, "total": str(o.total)})
        c.db.commit()
    except ValueError as e:
        c.db.rollback()
        raise HTTPException(422, str(e))
    except IntegrityError:  # concurrent duplicate of the same external_id
        c.db.rollback()
        existing = c.db.query(Order).filter_by(org_id=c.org.id, external_id=b.external_id).first()
        if not existing:
            raise
        return {**view(c.db, existing), "replayed": True}
    response.status_code = 201
    return {**view(c.db, o), "replayed": False}


@router.get("/orders/{ref}")
def get_order(ref: str, c: ApiCtx = Depends(store_ctx)):
    o = find(c, ref)
    c.db.commit()
    return view(c.db, o)


@router.post("/orders/{ref}/paypal-order")
def paypal_order(ref: str, c: ApiCtx = Depends(store_ctx)):
    """Sandbox orgs: creates the PayPal order and returns the approve link for your buyer."""
    o = find(c, ref)
    out = pp_router.create_paypal_order(pp_router.OrderRef(order_id=o.id), c)
    return out


class CaptureIn(BaseModel):
    paypal_order_id: str = Field(min_length=1, max_length=64)


@router.post("/orders/{ref}/capture")
def capture(ref: str, b: CaptureIn, c: ApiCtx = Depends(store_ctx)):
    o = find(c, ref)
    if not c.db.query(PaypalOrder).filter_by(org_id=c.org.id, order_id=o.id, paypal_order_id=b.paypal_order_id).first():
        raise HTTPException(404, "That PayPal order does not belong to this order")
    return pp_router.capture(b.paypal_order_id, c)


@router.post("/orders/{ref}/simulate-payment")
def simulate(ref: str, c: ApiCtx = Depends(store_ctx)):
    if c.org.mode != "demo":
        raise HTTPException(409, "Simulated payments exist only in demo organizations")
    o = find(c, ref)
    if o.status != "created":
        raise HTTPException(409, "Order already processed")
    finance.record_payment(c.db, c.org, o, f"SIM-CAP-{uuid.uuid4().hex[:8].upper()}", fee=finance.q(o.total * Decimal("0.029") + Decimal("0.30")), simulated=True)
    c.audit("store.payment_simulated", "order", o.id)
    c.db.commit()
    return view(c.db, o)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class EventIn(BaseModel):
    status: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=255)
    occurred_at: datetime


class ShipmentIn(BaseModel):
    carrier: str = Field(min_length=1, max_length=64)
    tracking_number: str = Field(min_length=1, max_length=64)
    status: str = Field(pattern="^(in_transit|delivered|exception)$")
    address: str | None = Field(None, max_length=255)
    events: list[EventIn] = Field(default_factory=list, max_length=100)


@router.post("/orders/{ref}/shipments")
def push_shipment(ref: str, b: ShipmentIn, c: ApiCtx = Depends(store_ctx)):
    """Upserts by tracking number; events are de-duplicated. This becomes dispute evidence verbatim (courier-reported facts only)."""
    o = find(c, ref)
    sh = c.db.query(Shipment).filter_by(org_id=c.org.id, order_id=o.id, tracking_number=b.tracking_number).first()
    if not sh:
        sh = Shipment(org_id=c.org.id, order_id=o.id, carrier=b.carrier, tracking_number=b.tracking_number, status=b.status, address=b.address)
        c.db.add(sh)
        c.db.flush()
    sh.status, sh.carrier = b.status, b.carrier
    if b.address:
        sh.address = b.address
    added = 0
    for e in b.events:
        at = _utc(e.occurred_at)
        if not c.db.query(TrackingEvent).filter_by(shipment_id=sh.id, status=e.status, occurred_at=at).first():
            c.db.add(TrackingEvent(org_id=c.org.id, shipment_id=sh.id, status=e.status, description=e.description, occurred_at=at))
            added += 1
    c.audit("store.shipment_pushed", "order", o.id, {"tracking": b.tracking_number, "events_added": added})
    c.db.commit()
    return {"shipment_id": str(sh.id), "events_added": added}


class MessageIn(BaseModel):
    direction: str = Field(pattern="^(inbound|outbound)$")
    body: str = Field(min_length=1, max_length=5000)
    sent_at: datetime


@router.post("/orders/{ref}/messages")
def push_message(ref: str, b: MessageIn, c: ApiCtx = Depends(store_ctx)):
    o = find(c, ref)
    at = _utc(b.sent_at)
    dup = c.db.query(CustomerMessage).filter_by(order_id=o.id, direction=b.direction, sent_at=at, body=b.body).first()
    if not dup:
        c.db.add(CustomerMessage(org_id=c.org.id, order_id=o.id, direction=b.direction, body=b.body, sent_at=at))
        c.audit("store.message_pushed", "order", o.id, {"direction": b.direction})
    c.db.commit()
    return {"stored": not dup}
