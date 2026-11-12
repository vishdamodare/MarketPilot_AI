"""Money rules: commission, allocations, payment recording, append-only ledger, payouts.

Ledger accounts and the invariant  cash = seller + platform :
  payment      cash     +gross
  fee          cash -fee and platform -fee (PayPal fee is borne by the marketplace)
  seller_earn  seller   +net        (liability owed to seller)
  commission   platform +commission (marketplace revenue)
  refund       cash     -amount
  seller_clawback seller -(amount - commission_reversal)
  commission_reversal platform -(amount * commission/gross)
  payout       cash -amt  and  seller -amt
"""
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import (Commission, FinancialLedger, MarketplaceTransaction, Notification, Order, OrderItem, Organization,
                      PaypalCapture, Payout, Product, Seller, SellerAllocation, SellerSettlement)

CENT = Decimal("0.01")


def q(x) -> Decimal:
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def commission_for(org: Organization, seller: Seller, gross: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """(rate_pct, fixed, amount). Seller override beats org default. Amount never exceeds gross."""
    rate = seller.commission_pct if seller.commission_pct is not None else org.default_commission_pct
    fixed = seller.commission_fixed or Decimal("0")
    return Decimal(rate), q(fixed), min(q(gross * Decimal(rate) / 100) + q(fixed), q(gross))


def notify(db: Session, org_id, title: str, body: str, severity="info", link=None):
    db.add(Notification(org_id=org_id, title=title, body=body, severity=severity, link=link))


def ledger(db: Session, org_id, entry_type: str, account: str, amount: Decimal, currency: str, *, seller_id=None,
           order_id=None, ref_type=None, ref_id=None, actor_id=None):
    db.add(FinancialLedger(org_id=org_id, seller_id=seller_id, order_id=order_id, entry_type=entry_type, account=account,
                           amount=q(amount), currency=currency, ref_type=ref_type, ref_id=str(ref_id) if ref_id else None,
                           actor_id=actor_id))


def create_order(db: Session, org: Organization, number: str, customer_id: str, customer_email: str,
                 lines: list[tuple[uuid.UUID, int]], address: str | None = None) -> Order:
    """Server-side pricing: amounts come from the products table, never from the client."""
    items: list[OrderItem] = []
    order = Order(org_id=org.id, number=number, customer_id=customer_id, customer_email=customer_email,
                  shipping_address=address, total=Decimal("0"), currency=org.currency)
    db.add(order)
    db.flush()
    for pid, qty in lines:
        p = db.query(Product).filter_by(id=pid, org_id=org.id).first()
        if not p or qty < 1 or qty > 1000:
            raise ValueError("Invalid product or quantity")
        s = db.get(Seller, p.seller_id)
        if s.status != "active":
            raise ValueError(f"Seller {s.business_name} is not active")
        items.append(OrderItem(org_id=org.id, order_id=order.id, seller_id=p.seller_id, product_id=p.id, name=p.name,
                               amount=q(p.price * qty)))
    if not items:
        raise ValueError("Order needs at least one item")
    db.add_all(items)
    order.total = sum((i.amount for i in items), Decimal("0"))
    allocate(db, org, order, items)
    return order


def allocate(db: Session, org: Organization, order: Order, items: list[OrderItem]):
    by_seller: dict[uuid.UUID, Decimal] = {}
    for i in items:
        by_seller[i.seller_id] = by_seller.get(i.seller_id, Decimal("0")) + i.amount
    for sid, gross in by_seller.items():
        seller = db.get(Seller, sid)
        rate, fixed, amt = commission_for(org, seller, gross)
        db.add(Commission(org_id=org.id, order_id=order.id, seller_id=sid, rate_pct=rate, fixed=fixed, amount=amt))
        db.add(SellerAllocation(org_id=org.id, order_id=order.id, seller_id=sid, gross=gross, commission=amt, net=gross - amt))
    db.flush()


def record_payment(db: Session, org: Organization, order: Order, capture_id: str, *, fee: Decimal = Decimal("0"),
                   simulated: bool, paypal_order_id: str | None = None, status="COMPLETED", actor_id=None) -> PaypalCapture:
    """Single entry point for 'money arrived'. Idempotent on capture_id."""
    existing = db.query(PaypalCapture).filter_by(org_id=org.id, capture_id=capture_id).first()
    if existing:
        return existing
    cap = PaypalCapture(org_id=org.id, order_id=order.id, paypal_order_id=paypal_order_id, capture_id=capture_id,
                        status=status, amount=order.total, currency=order.currency, paypal_fee=fee, is_simulated=simulated)
    db.add(cap)
    db.add(MarketplaceTransaction(org_id=org.id, order_id=order.id, type="payment", amount=order.total,
                                  currency=order.currency, status="completed", external_ref=capture_id, is_simulated=simulated))
    if status == "COMPLETED":
        order.status = "paid"
        ledger(db, org.id, "payment", "cash", order.total, order.currency, order_id=order.id, ref_type="capture", ref_id=capture_id, actor_id=actor_id)
        if fee:
            ledger(db, org.id, "fee", "cash", -fee, order.currency, order_id=order.id, ref_type="capture", ref_id=capture_id)
            ledger(db, org.id, "fee", "platform", -fee, order.currency, order_id=order.id, ref_type="capture", ref_id=capture_id)
        for a in db.query(SellerAllocation).filter_by(order_id=order.id):
            ledger(db, org.id, "commission", "platform", a.commission, order.currency, seller_id=a.seller_id, order_id=order.id, ref_type="allocation", ref_id=a.id)
            ledger(db, org.id, "seller_earning", "seller", a.net, order.currency, seller_id=a.seller_id, order_id=order.id, ref_type="allocation", ref_id=a.id)
            db.add(SellerSettlement(org_id=org.id, seller_id=a.seller_id, allocation_id=a.id, amount=a.net, status="pending"))
    elif status == "DECLINED":
        order.status = "failed"
    db.flush()
    return cap


def refund_split(db: Session, order_id, amount: Decimal) -> list[tuple[uuid.UUID, Decimal, Decimal]]:
    """Split a refund across sellers proportionally to allocation gross: [(seller_id, seller_share, commission_reversal)]."""
    allocs = db.query(SellerAllocation).filter_by(order_id=order_id).all()
    total = sum((a.gross for a in allocs), Decimal("0"))
    out, used = [], Decimal("0")
    for n, a in enumerate(allocs):
        share = amount - used if n == len(allocs) - 1 else q(amount * a.gross / total)
        used += share
        rev = q(share * a.commission / a.gross) if a.gross else Decimal("0")
        out.append((a.seller_id, share - rev, rev))
    return out


def post_refund_ledger(db: Session, org: Organization, order: Order, amount: Decimal, refund_id, actor_id=None):
    ledger(db, org.id, "refund", "cash", -amount, order.currency, order_id=order.id, ref_type="refund", ref_id=refund_id, actor_id=actor_id)
    for sid, seller_part, rev in refund_split(db, order.id, amount):
        ledger(db, org.id, "seller_clawback", "seller", -seller_part, order.currency, seller_id=sid, order_id=order.id, ref_type="refund", ref_id=refund_id)
        ledger(db, org.id, "commission_reversal", "platform", -rev, order.currency, seller_id=sid, order_id=order.id, ref_type="refund", ref_id=refund_id)


def seller_balance(db: Session, org_id, seller_id) -> Decimal:
    """Derived from the ledger: what the platform still owes the seller."""
    v = db.query(func.coalesce(func.sum(FinancialLedger.amount), 0)).filter_by(org_id=org_id, seller_id=seller_id, account="seller").scalar()
    return q(v)


def create_payout_request(db: Session, org: Organization, seller: Seller, actor_id) -> Payout | None:
    """Bundle a seller's pending settlements into a payout awaiting merchant approval (never auto-paid)."""
    sets = db.query(SellerSettlement).filter_by(org_id=org.id, seller_id=seller.id, status="pending", payout_id=None).all()
    amt = min(sum((s.amount for s in sets), Decimal("0")), max(seller_balance(db, org.id, seller.id), Decimal("0")))
    if not sets or amt <= 0:
        return None
    p = Payout(org_id=org.id, seller_id=seller.id, amount=q(amt), currency=org.currency, status="pending_approval", is_simulated=org.mode == "demo",
               adjustment=q(amt) - q(sum((s.amount for s in sets), Decimal("0"))))
    db.add(p)
    db.flush()
    for s in sets:
        s.payout_id = p.id
    return p


def mark_payout_paid(db: Session, org: Organization, payout: Payout, reference: str, actor_id):
    payout.status, payout.reference, payout.approved_by = "paid", reference, actor_id
    for s in db.query(SellerSettlement).filter_by(payout_id=payout.id):
        s.status = "settled"
    ledger(db, org.id, "payout", "cash", -payout.amount, payout.currency, seller_id=None, ref_type="payout", ref_id=payout.id, actor_id=actor_id)
    ledger(db, org.id, "payout", "seller", -payout.amount, payout.currency, seller_id=payout.seller_id, ref_type="payout", ref_id=payout.id, actor_id=actor_id)
    db.add(MarketplaceTransaction(org_id=org.id, seller_id=payout.seller_id, type="payout", amount=payout.amount,
                                  currency=payout.currency, status="completed", external_ref=reference, is_simulated=payout.is_simulated))
