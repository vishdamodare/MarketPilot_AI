"""PayPal adapter tests. The mock-transport tests always run. The live Sandbox tests run ONLY when real sandbox
credentials are present in the environment (PAYPAL_LIVE_TEST=1 plus PAYPAL_CLIENT_ID/SECRET); otherwise they are skipped."""
import base64
import json
import os
from decimal import Decimal

import httpx
import pytest

from app.services import paypal
from app.services.paypal import PayPalClient, PayPalError


@pytest.fixture
def mk(monkeypatch):
    monkeypatch.setattr(paypal.time, "sleep", lambda s: None)
    seen: list[httpx.Request] = []
    script: list = []

    def make(partner_id=""):
        PayPalClient._token = None

        def handler(req: httpx.Request):
            seen.append(req)
            if req.url.path == "/v1/oauth2/token":
                return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 300})
            return script.pop(0) if script else httpx.Response(200, json={"id": "OK"})

        c = PayPalClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(c.s, "paypal_client_id", "cid-secret-id")
        monkeypatch.setattr(c.s, "paypal_client_secret", "csecret-value")
        monkeypatch.setattr(c.s, "paypal_partner_id", partner_id)
        monkeypatch.setattr(c.s, "paypal_webhook_id", "")
        return c

    return make, seen, script


def test_oauth_uses_basic_auth_form_body_and_token_is_cached(mk):
    make, seen, _ = mk
    c = make()
    c.get_order("A")
    c.get_order("B")
    tokens = [r for r in seen if r.url.path == "/v1/oauth2/token"]
    assert len(tokens) == 1  # cached across calls
    assert tokens[0].headers["authorization"].startswith("Basic ") and b"grant_type=client_credentials" in tokens[0].content
    assert seen[-1].headers["authorization"] == "Bearer tok-123"


def test_not_configured_raises_clean_error(mk):
    make, _, _ = mk
    c = make()
    c.s.paypal_client_id = ""
    with pytest.raises(paypal.PayPalNotConfigured):
        c.get_order("A")


def test_create_order_platform_collected_has_idempotency_key_and_exact_amount(mk):
    make, seen, _ = mk
    c = make()
    c.create_order(amount=Decimal("130.00"), currency="USD", reference_id="ORD-1", request_id="req-1", seller_merchant_id="MERCH1", platform_fee=Decimal("13.00"))
    r = seen[-1]
    body = json.loads(r.content)
    assert r.url.path == "/v2/checkout/orders" and r.headers["paypal-request-id"] == "req-1"
    assert body["intent"] == "CAPTURE" and body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "130.00"}
    assert "payee" not in body["purchase_units"][0] and "paypal-auth-assertion" not in r.headers  # no partner approval -> never pretend to be multiparty


def test_create_order_multiparty_when_partner_configured(mk):
    make, seen, _ = mk
    c = make(partner_id="PARTNER1")
    c.create_order(amount=Decimal("100.00"), currency="USD", reference_id="ORD-2", request_id="req-2", seller_merchant_id="MERCH1", platform_fee=Decimal("10.00"))
    r = seen[-1]
    unit = json.loads(r.content)["purchase_units"][0]
    assert unit["payee"] == {"merchant_id": "MERCH1"}
    assert unit["payment_instruction"]["platform_fees"][0]["amount"]["value"] == "10.00"
    hdr, payload, sig = r.headers["paypal-auth-assertion"].split(".")
    assert sig == "" and json.loads(base64.urlsafe_b64decode(payload + "==" * 2))["payer_id"] == "PARTNER1"


def test_refund_forwards_idempotency_key_and_amount(mk):
    make, seen, _ = mk
    make().refund_capture("CAP-9", Decimal("10.5"), "USD", request_id="idem-xyz", note="x")
    r = seen[-1]
    assert r.url.path == "/v2/payments/captures/CAP-9/refund" and r.headers["paypal-request-id"] == "idem-xyz"
    assert json.loads(r.content)["amount"] == {"currency_code": "USD", "value": "10.50"}


def test_retries_rate_limits_then_succeeds_and_gives_up_on_persistent_errors(mk):
    make, seen, script = mk
    c = make()
    script += [httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(503), httpx.Response(200, json={"id": "FINE"})]
    assert c.get_order("A")["id"] == "FINE"
    script += [httpx.Response(500)] * 3
    with pytest.raises(PayPalError):
        c.get_order("B")


def test_client_errors_are_not_retried_and_secrets_are_redacted(mk):
    make, seen, script = mk
    c = make()
    script += [httpx.Response(422, text='{"message":"bad csecret-value cid-secret-id"}', headers={"Paypal-Debug-Id": "dbg1"})]
    with pytest.raises(PayPalError) as e:
        c.get_order("A")
    assert e.value.status == 422 and e.value.debug_id == "dbg1"
    assert "csecret-value" not in str(e.value) and "cid-secret-id" not in str(e.value)
    assert len([r for r in seen if r.url.path.startswith("/v2/checkout/orders/A")]) == 1


def test_webhook_verification_fails_closed(mk):
    make, seen, script = mk
    c = make()
    assert c.verify_webhook({}, {"id": "E1"}) is False  # no webhook id configured
    c.s.paypal_webhook_id = "WH-1"
    script += [httpx.Response(200, json={"verification_status": "SUCCESS"})]
    hdrs = {"PayPal-Transmission-Id": "t", "PayPal-Transmission-Sig": "s", "PayPal-Transmission-Time": "x", "PayPal-Cert-Url": "u", "PayPal-Auth-Algo": "a"}
    assert c.verify_webhook(hdrs, {"id": "E1"}) is True
    sent = json.loads(seen[-1].content)
    assert sent["webhook_id"] == "WH-1" and sent["transmission_id"] == "t" and sent["webhook_event"] == {"id": "E1"}
    script += [httpx.Response(200, json={"verification_status": "FAILURE"})]
    assert c.verify_webhook(hdrs, {"id": "E1"}) is False
    script += [httpx.Response(500)] * 3
    assert c.verify_webhook(hdrs, {"id": "E1"}) is False  # PayPal unreachable -> reject, never accept


# ------------------------------------------------------------------ live Sandbox (skipped without credentials)
live = pytest.mark.skipif(not (os.environ.get("PAYPAL_LIVE_TEST") == "1" and os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET")),
                          reason="set PAYPAL_LIVE_TEST=1 with sandbox PAYPAL_CLIENT_ID/PAYPAL_CLIENT_SECRET to run")


@live
def test_live_sandbox_oauth_and_create_order():
    from app.config import Settings
    s = Settings()
    assert s.paypal_is_sandbox, "refusing to run live tests against a non-sandbox base URL"
    c = PayPalClient()
    r = c.create_order(amount=Decimal("10.00"), currency="USD", reference_id="MP-LIVE-TEST", request_id=paypal.new_request_id())
    assert r["status"] == "CREATED" and any(l["rel"] in ("approve", "payer-action") for l in r["links"])
    assert c.get_order(r["id"])["id"] == r["id"]


@live
def test_live_sandbox_list_disputes():
    assert isinstance(PayPalClient().list_disputes(page_size=5), list)
