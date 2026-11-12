"""All tables. Money is Numeric(18,2) -> Decimal, never float. Timestamps are UTC.

Every domain table carries org_id (tenant isolation). Demo vs Sandbox data are
separated structurally: an org has a fixed `mode`, so the two never share rows.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


Money = Numeric(18, 2)


def pk():
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def fk(table: str, nullable=False, index=True):
    return mapped_column(Uuid, ForeignKey(f"{table}.id"), nullable=nullable, index=index)


def ts(**kw):
    return mapped_column(DateTime(timezone=True), default=now, **kw)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = ts()


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(16), default="sandbox")  # 'demo' | 'sandbox'
    currency: Mapped[str] = mapped_column(String(3), default="USD")  # ISO 4217
    default_commission_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("10.00"))
    marketplace_type: Mapped[str] = mapped_column(String(64), default="ecommerce")
    created_at: Mapped[datetime] = ts()


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(32), unique=True)


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("role_id", "code"),)
    id: Mapped[uuid.UUID] = pk()
    role_id: Mapped[uuid.UUID] = fk("roles")
    code: Mapped[str] = mapped_column(String(64))


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)
    id: Mapped[uuid.UUID] = pk()
    org_id: Mapped[uuid.UUID] = fk("organizations")
    user_id: Mapped[uuid.UUID] = fk("users")
    role: Mapped[str] = mapped_column(String(32))  # admin | finance_manager | seller
    seller_id: Mapped[uuid.UUID | None] = fk("sellers", nullable=True)
    created_at: Mapped[datetime] = ts()


class OrgScoped:
    org_id: Mapped[uuid.UUID] = fk("organizations")


class Seller(Base, OrgScoped):
    __tablename__ = "sellers"
    id: Mapped[uuid.UUID] = pk()
    business_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|active|suspended
    commission_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)  # None -> org default
    commission_fixed: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    paypal_merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    onboarding_status: Mapped[str] = mapped_column(String(24), default="not_started")
    payment_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts()


class SellerPaypalAccount(Base, OrgScoped):
    __tablename__ = "seller_paypal_accounts"
    id: Mapped[uuid.UUID] = pk()
    seller_id: Mapped[uuid.UUID] = fk("sellers")
    merchant_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="linked")
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts()


class SellerOnboarding(Base, OrgScoped):
    __tablename__ = "seller_onboarding"
    id: Mapped[uuid.UUID] = pk()
    seller_id: Mapped[uuid.UUID] = fk("sellers")
    tracking_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="started")
    action_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts()


class Product(Base, OrgScoped):
    __tablename__ = "products"
    id: Mapped[uuid.UUID] = pk()
    seller_id: Mapped[uuid.UUID] = fk("sellers")
    name: Mapped[str] = mapped_column(String(255))
    price: Mapped[Decimal] = mapped_column(Money)


class Order(Base, OrgScoped):
    __tablename__ = "orders"
    id: Mapped[uuid.UUID] = pk()
    number: Mapped[str] = mapped_column(String(32))
    customer_id: Mapped[str] = mapped_column(String(64))
    customer_email: Mapped[str] = mapped_column(String(255))
    shipping_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(24), default="created")  # created|authorized|paid|failed|refunded
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # storefront's own order id -> idempotency key
    created_at: Mapped[datetime] = ts(index=True)
    __table_args__ = (UniqueConstraint("org_id", "number"), UniqueConstraint("org_id", "external_id"))


class OrderItem(Base, OrgScoped):
    __tablename__ = "order_items"
    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID] = fk("orders")
    seller_id: Mapped[uuid.UUID] = fk("sellers")
    product_id: Mapped[uuid.UUID | None] = fk("products", nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Money)


class MarketplaceTransaction(Base, OrgScoped):
    """Unified internal transaction record: payment | refund | payout | fee."""
    __tablename__ = "marketplace_transactions"
    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID | None] = fk("orders", nullable=True)
    seller_id: Mapped[uuid.UUID | None] = fk("sellers", nullable=True)
    type: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(24))
    external_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # paypal capture/refund/payout id
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts(index=True)


class PaypalOrder(Base, OrgScoped):
    __tablename__ = "paypal_orders"
    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID | None] = fk("orders", nullable=True)
    paypal_order_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24))
    approve_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts()


class PaypalCapture(Base, OrgScoped):
    __tablename__ = "paypal_captures"
    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID | None] = fk("orders", nullable=True)
    paypal_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capture_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24))  # COMPLETED|PENDING|DECLINED|PARTIALLY_REFUNDED|REFUNDED
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    paypal_fee: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts(index=True)


class Commission(Base, OrgScoped):
    __tablename__ = "commissions"
    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID] = fk("orders")
    seller_id: Mapped[uuid.UUID] = fk("sellers")
    rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    fixed: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    amount: Mapped[Decimal] = mapped_column(Money)


class SellerAllocation(Base, OrgScoped):
    __tablename__ = "seller_allocations"
    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID] = fk("orders")
    seller_id: Mapped[uuid.UUID] = fk("sellers")
    gross: Mapped[Decimal] = mapped_column(Money)
    commission: Mapped[Decimal] = mapped_column(Money)
    net: Mapped[Decimal] = mapped_column(Money)


class Payout(Base, OrgScoped):
    __tablename__ = "payouts"
    id: Mapped[uuid.UUID] = pk()
    seller_id: Mapped[uuid.UUID] = fk("sellers")
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(24))  # pending_approval|processing|paid|failed|delayed
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adjustment: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))  # amount - sum(settlements): refund clawbacks netted from this payout
    approved_by: Mapped[uuid.UUID | None] = fk("users", nullable=True, index=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts(index=True)


class SellerSettlement(Base, OrgScoped):
    __tablename__ = "seller_settlements"
    id: Mapped[uuid.UUID] = pk()
    seller_id: Mapped[uuid.UUID] = fk("sellers")
    allocation_id: Mapped[uuid.UUID] = fk("seller_allocations")
    amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|settled|failed
    payout_id: Mapped[uuid.UUID | None] = fk("payouts", nullable=True)
    created_at: Mapped[datetime] = ts()


class Refund(Base, OrgScoped):
    __tablename__ = "refunds"
    __table_args__ = (UniqueConstraint("org_id", "idempotency_key"),)
    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID | None] = fk("orders", nullable=True)
    capture_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seller_id: Mapped[uuid.UUID | None] = fk("sellers", nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    # requested -> needs_review|pending_approval -> completed|failed|rejected|escalated
    status: Mapped[str] = mapped_column(String(24), default="requested")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    paypal_refund_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[uuid.UUID | None] = fk("users", nullable=True, index=False)
    approved_by: Mapped[uuid.UUID | None] = fk("users", nullable=True, index=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts(index=True)


class RefundInvestigation(Base, OrgScoped):
    __tablename__ = "refund_investigations"
    id: Mapped[uuid.UUID] = pk()
    refund_id: Mapped[uuid.UUID] = fk("refunds")
    risk_level: Mapped[str] = mapped_column(String(8))  # low|medium|high
    report: Mapped[dict] = mapped_column(JSON)
    recommended_action: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|escalated|closed
    created_at: Mapped[datetime] = ts()


class Dispute(Base, OrgScoped):
    __tablename__ = "disputes"
    __table_args__ = (UniqueConstraint("org_id", "paypal_dispute_id"),)
    id: Mapped[uuid.UUID] = pk()
    paypal_dispute_id: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[uuid.UUID | None] = fk("orders", nullable=True)
    seller_id: Mapped[uuid.UUID | None] = fk("sellers", nullable=True)
    capture_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(64))
    # open|under_review|waiting_merchant|resolved|lost
    status: Mapped[str] = mapped_column(String(24))
    amount: Mapped[Decimal] = mapped_column(Money)
    currency: Mapped[str] = mapped_column(String(3))
    response_due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # from PayPal; NULL if unknown
    opened_at: Mapped[datetime] = ts()
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DisputeEvidence(Base, OrgScoped):
    __tablename__ = "dispute_evidence"
    id: Mapped[uuid.UUID] = pk()
    dispute_id: Mapped[uuid.UUID] = fk("disputes")
    kind: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = ts()


class DisputeResponse(Base, OrgScoped):
    __tablename__ = "dispute_responses"
    id: Mapped[uuid.UUID] = pk()
    dispute_id: Mapped[uuid.UUID] = fk("disputes")
    action: Mapped[str] = mapped_column(String(16), default="contest")  # contest|accept
    body: Mapped[str] = mapped_column(Text)
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|approved|submitted|failed
    approved_by: Mapped[uuid.UUID | None] = fk("users", nullable=True, index=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = ts()


class Shipment(Base, OrgScoped):
    __tablename__ = "shipments"
    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID] = fk("orders")
    carrier: Mapped[str] = mapped_column(String(64))
    tracking_number: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))  # in_transit|delivered|exception
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TrackingEvent(Base, OrgScoped):
    __tablename__ = "tracking_events"
    id: Mapped[uuid.UUID] = pk()
    shipment_id: Mapped[uuid.UUID] = fk("shipments")
    status: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CustomerMessage(Base, OrgScoped):
    __tablename__ = "customer_messages"
    id: Mapped[uuid.UUID] = pk()
    order_id: Mapped[uuid.UUID] = fk("orders")
    direction: Mapped[str] = mapped_column(String(8))  # inbound|outbound
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReconciliationRun(Base, OrgScoped):
    __tablename__ = "reconciliation_runs"
    id: Mapped[uuid.UUID] = pk()
    started_at: Mapped[datetime] = ts()
    total_checked: Mapped[int] = mapped_column(Integer, default=0)
    mismatches: Mapped[int] = mapped_column(Integer, default=0)
    run_by: Mapped[uuid.UUID | None] = fk("users", nullable=True, index=False)


class ReconciliationRecord(Base, OrgScoped):
    __tablename__ = "reconciliation_records"
    id: Mapped[uuid.UUID] = pk()
    run_id: Mapped[uuid.UUID] = fk("reconciliation_runs")
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))
    ref_type: Mapped[str] = mapped_column(String(24))
    ref_id: Mapped[str] = mapped_column(String(64))
    expected: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    actual: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    message: Mapped[str] = mapped_column(Text)


class FinancialLedger(Base, OrgScoped):
    """Append-only. Balances are always derived (SUM) from these rows. No UPDATE/DELETE in app code."""
    __tablename__ = "financial_ledger"
    __table_args__ = (Index("ix_ledger_org_seller", "org_id", "seller_id"),)
    id: Mapped[uuid.UUID] = pk()
    seller_id: Mapped[uuid.UUID | None] = fk("sellers", nullable=True, index=False)
    order_id: Mapped[uuid.UUID | None] = fk("orders", nullable=True, index=False)
    entry_type: Mapped[str] = mapped_column(String(32))  # payment|commission|seller_earning|refund|commission_reversal|seller_clawback|payout|fee
    account: Mapped[str] = mapped_column(String(16))  # platform|seller
    amount: Mapped[Decimal] = mapped_column(Money)  # signed
    currency: Mapped[str] = mapped_column(String(3))
    ref_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_id: Mapped[uuid.UUID | None] = fk("users", nullable=True, index=False)
    created_at: Mapped[datetime] = ts(index=True)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: Mapped[uuid.UUID] = pk()
    org_id: Mapped[uuid.UUID] = fk("organizations")
    event_id: Mapped[str] = mapped_column(String(128), unique=True)  # PayPal's event id -> dedupe
    event_type: Mapped[str] = mapped_column(String(64))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict] = mapped_column(JSON)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = ts()


class AiInvestigation(Base, OrgScoped):
    __tablename__ = "ai_investigations"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = fk("users", index=False)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[dict] = mapped_column(JSON)
    tool_calls: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = ts()


class AiRecommendation(Base, OrgScoped):
    __tablename__ = "ai_recommendations"
    id: Mapped[uuid.UUID] = pk()
    investigation_id: Mapped[uuid.UUID | None] = fk("ai_investigations", nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(8), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = ts()


class Notification(Base, OrgScoped):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = pk()
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(8), default="info")  # info|warning|critical
    link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = ts(index=True)


class AuditLog(Base, OrgScoped):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID | None] = fk("users", nullable=True, index=False)
    actor_type: Mapped[str] = mapped_column(String(8), default="user")  # user|ai|system|webhook
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = ts(index=True)


class IntegrationSetting(Base, OrgScoped):
    """Non-secret integration state. Credentials come from backend env only."""
    __tablename__ = "integration_settings"
    __table_args__ = (UniqueConstraint("org_id", "provider"),)
    id: Mapped[uuid.UUID] = pk()
    provider: Mapped[str] = mapped_column(String(16), default="paypal")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_webhook_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_prefs: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ApiKey(Base, OrgScoped):
    """Storefront API key. Only the SHA-256 hash is stored; the plaintext is shown once at creation."""
    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(12))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[uuid.UUID | None] = fk("users", nullable=True, index=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = ts()
