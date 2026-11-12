import os

# Must be set before the app is imported. Real Postgres: SQLite would silently turn Numeric money into floats.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://localhost/marketpilot_test")
os.environ["SYNC_INTERVAL_SECONDS"] = "0"  # never start the background thread in tests
for k in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_WEBHOOK_ID", "OPENAI_API_KEY", "PAYPAL_PARTNER_ID"):
    os.environ[k] = ""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password, make_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Organization, OrganizationMember, Product, Seller, User
from app.services import finance


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


class World:
    """Direct-DB fixture builder; the behaviour under test is always exercised through the HTTP API."""

    def __init__(self, mode="sandbox"):
        self.db = SessionLocal()
        self.org = Organization(name="Test Mkt", mode=mode, default_commission_pct=Decimal("10.00"))
        self.db.add(self.org)
        self.db.flush()

    def client(self, role="admin", seller_id=None, org=None) -> TestClient:
        org = org or self.org
        u = User(email=f"{uuid.uuid4().hex[:8]}@example.com", name=role, password_hash=hash_password("pw-pw-pw-pw-1"))
        self.db.add(u)
        self.db.flush()
        self.db.add(OrganizationMember(org_id=org.id, user_id=u.id, role=role, seller_id=seller_id))
        self.db.commit()
        c = TestClient(app, headers={"X-Requested-With": "marketpilot", "X-Org-Id": str(org.id)})
        c.cookies.set("mp_session", make_token(u.id))
        return c

    def seller(self, name="Acme", pct=None, status="active") -> Seller:
        s = Seller(org_id=self.org.id, business_name=name, email=f"{name.lower()}@example.com", status=status, commission_pct=pct)
        self.db.add(s)
        self.db.flush()
        return s

    def product(self, seller, price="100.00") -> Product:
        p = Product(org_id=self.org.id, seller_id=seller.id, name="Widget", price=Decimal(price))
        self.db.add(p)
        self.db.flush()
        return p

    def paid_order(self, seller=None, price="100.00", qty=1, cap="CAP-1"):
        seller = seller or self.seller()
        p = self.product(seller, price)
        o = finance.create_order(self.db, self.org, f"ORD-{uuid.uuid4().hex[:6]}", "CUST-1", "c@example.com", [(p.id, qty)], "1 Main St")
        finance.record_payment(self.db, self.org, o, cap, fee=Decimal("3.20"), simulated=False)
        self.db.commit()
        return o


@pytest.fixture
def w():
    world = World()
    yield world
    world.db.close()


@pytest.fixture
def demo_world():
    world = World(mode="demo")
    yield world
    world.db.close()
