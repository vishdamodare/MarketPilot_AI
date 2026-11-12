"""PayPal REST adapter. The ONLY module that talks to PayPal. Secrets never leave the backend.

Endpoint verification status (see PAYPAL_INTEGRATION.md):
  VERIFIED against PayPal's OpenAPI specs: /v1/oauth2/token, POST /v2/checkout/orders, GET /v1/customer/disputes,
    GET /v1/customer/disputes/{id}, POST .../provide-evidence (multipart), POST .../accept-claim (multipart),
    POST /v1/payments/payouts, POST /v2/customer/partner-referrals, GET /v1/reporting/transactions (path only).
  NOT VERIFIED from specs (implemented from PayPal's documented conventions; confirm in sandbox before relying):
    POST /v2/checkout/orders/{id}/capture, POST /v2/payments/captures/{id}/refund, GET /v2/payments/refunds/{id},
    GET /v2/payments/captures/{id}, POST /v1/notifications/verify-webhook-signature,
    platform_fees schema, PayPal-Auth-Assertion, provide-evidence 'input' JSON shape.
"""
import base64
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from ..config import get_settings

log = logging.getLogger("paypal")


class PayPalError(Exception):
    def __init__(self, message: str, status: int | None = None, debug_id: str | None = None):
        super().__init__(message)
        self.status, self.debug_id = status, debug_id


class PayPalNotConfigured(PayPalError):
    pass


def _redact(s: str) -> str:
    st = get_settings()
    for secret in (st.paypal_client_secret, st.paypal_client_id):
        if secret:
            s = s.replace(secret, "***")
    return s[:500]


class PayPalClient:
    _token: str | None = None
    _token_exp: float = 0

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.s = get_settings()
        self.http = httpx.Client(base_url=self.s.paypal_base_url, timeout=20, transport=transport)

    # --- auth -------------------------------------------------------------
    def _auth(self) -> str:
        if not self.s.paypal_configured:
            raise PayPalNotConfigured("PayPal credentials are not configured (set PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET).")
        if self._token and time.time() < self._token_exp - 30:
            return self._token
        r = self.http.post("/v1/oauth2/token", auth=(self.s.paypal_client_id, self.s.paypal_client_secret),
                           data={"grant_type": "client_credentials"})
        if r.status_code != 200:
            raise PayPalError(f"OAuth failed ({r.status_code})", r.status_code)
        d = r.json()
        PayPalClient._token, PayPalClient._token_exp = d["access_token"], time.time() + int(d.get("expires_in", 300))
        return PayPalClient._token

    def request(self, method: str, path: str, *, json_body=None, headers: dict | None = None, params=None,
                files=None, data=None, retries: int = 3) -> dict:
        """Retries 429/5xx with backoff (honouring Retry-After). Idempotent calls MUST pass PayPal-Request-Id."""
        for attempt in range(retries):
            h = {"Authorization": f"Bearer {self._auth()}", **(headers or {})}
            try:
                r = self.http.request(method, path, json=json_body, headers=h, params=params, files=files, data=data)
            except httpx.HTTPError as e:
                if attempt == retries - 1:
                    raise PayPalError(f"Network error: {type(e).__name__}") from e
                time.sleep(2 ** attempt * 0.5)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(float(r.headers.get("Retry-After", 2 ** attempt * 0.5)))
                continue
            if r.status_code == 401:
                PayPalClient._token = None
            if r.status_code >= 400:
                log.warning("paypal %s %s -> %s %s", method, path, r.status_code, _redact(r.text))
                raise PayPalError(_redact(r.text) or f"HTTP {r.status_code}", r.status_code, r.headers.get("Paypal-Debug-Id"))
            return r.json() if r.content else {}
        raise PayPalError("Retries exhausted")

    # --- Orders (create VERIFIED; capture path NOT VERIFIED) ---------------
    def auth_assertion(self, seller_merchant_id: str) -> str:
        """Unsigned JWT identifying the seller on whose behalf the partner acts (multiparty). Only valid for approved partners."""
        b = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
        return f"{b({'alg': 'none'})}.{b({'iss': self.s.paypal_client_id, 'payer_id': seller_merchant_id})}."

    def create_order(self, *, amount: Decimal, currency: str, reference_id: str, request_id: str,
                     seller_merchant_id: str | None = None, platform_fee: Decimal | None = None) -> dict:
        unit: dict = {"reference_id": reference_id, "amount": {"currency_code": currency, "value": f"{amount:.2f}"}}
        headers = {"PayPal-Request-Id": request_id}
        if seller_merchant_id and self.s.paypal_partner_id:  # multiparty path: requires PayPal partner approval
            unit["payee"] = {"merchant_id": seller_merchant_id}
            if platform_fee:
                unit["payment_instruction"] = {"disbursement_mode": "INSTANT", "platform_fees": [
                    {"amount": {"currency_code": currency, "value": f"{platform_fee:.2f}"}}]}
            headers["PayPal-Auth-Assertion"] = self.auth_assertion(self.s.paypal_partner_id)
            if self.s.paypal_partner_attribution_id:
                headers["PayPal-Partner-Attribution-Id"] = self.s.paypal_partner_attribution_id
        return self.request("POST", "/v2/checkout/orders", headers=headers,
                            json_body={"intent": "CAPTURE", "purchase_units": [unit]})

    def capture_order(self, paypal_order_id: str, request_id: str) -> dict:
        return self.request("POST", f"/v2/checkout/orders/{paypal_order_id}/capture", headers={"PayPal-Request-Id": request_id}, json_body={})

    def get_order(self, paypal_order_id: str) -> dict:
        return self.request("GET", f"/v2/checkout/orders/{paypal_order_id}")

    # --- Payments (NOT VERIFIED from specs) --------------------------------
    def get_capture(self, capture_id: str) -> dict:
        return self.request("GET", f"/v2/payments/captures/{capture_id}")

    def refund_capture(self, capture_id: str, amount: Decimal, currency: str, request_id: str, note: str | None = None) -> dict:
        body = {"amount": {"currency_code": currency, "value": f"{amount:.2f}"}}
        if note:
            body["note_to_payer"] = note[:255]
        return self.request("POST", f"/v2/payments/captures/{capture_id}/refund", headers={"PayPal-Request-Id": request_id}, json_body=body)

    def get_refund(self, refund_id: str) -> dict:
        return self.request("GET", f"/v2/payments/refunds/{refund_id}")

    # --- Disputes (list/get/provide-evidence/accept-claim VERIFIED) --------
    def list_disputes(self, page_size: int = 50) -> list[dict]:
        out, token = [], None
        for _ in range(10):
            d = self.request("GET", "/v1/customer/disputes", params={"page_size": page_size, **({"next_page_token": token} if token else {})})
            out += d.get("items", [])
            token = next((l["href"].split("next_page_token=")[1].split("&")[0] for l in d.get("links", []) if l.get("rel") == "next" and "next_page_token=" in l.get("href", "")), None)
            if not token:
                break
        return out

    def get_dispute(self, dispute_id: str) -> dict:
        return self.request("GET", f"/v1/customer/disputes/{dispute_id}")

    def provide_evidence(self, dispute_id: str, notes: str, tracking: dict | None = None) -> dict:
        """multipart/form-data: 'input' JSON part (shape NOT VERIFIED) + optional evidence-file parts."""
        evidence = {"evidence_type": "PROOF_OF_FULFILLMENT" if tracking else "OTHER", "notes": notes[:2000]}
        if tracking:
            evidence["evidence_info"] = {"tracking_info": [tracking]}
        files = {"input": ("input.json", json.dumps({"evidences": [evidence]}), "application/json")}
        return self.request("POST", f"/v1/customer/disputes/{dispute_id}/provide-evidence", files=files)

    def accept_claim(self, dispute_id: str, note: str) -> dict:
        """Spec says multipart; accept_claim fields (note/reason) are NOT in the verified spec portion — sent in 'input' part."""
        files = {"input": ("input.json", json.dumps({"note": note[:2000], "accept_claim_reason": "DID_NOT_SHIP_ITEM"}), "application/json")}
        return self.request("POST", f"/v1/customer/disputes/{dispute_id}/accept-claim", files=files)

    # --- Transaction search / payouts / onboarding / webhooks --------------
    def search_transactions(self, start: datetime, end: datetime) -> list[dict]:
        d = self.request("GET", "/v1/reporting/transactions", params={
            "start_date": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fields": "transaction_info", "page_size": 100})
        return d.get("transaction_details", [])

    def create_payout(self, batch_id: str, receiver_email: str, amount: Decimal, currency: str, note: str) -> dict:
        body = {"sender_batch_header": {"sender_batch_id": batch_id, "email_subject": "Marketplace payout"},
                "items": [{"recipient_type": "EMAIL", "receiver": receiver_email, "note": note[:255],
                           "amount": {"value": f"{amount:.2f}", "currency": currency}}]}
        return self.request("POST", "/v1/payments/payouts", headers={"PayPal-Request-Id": batch_id}, json_body=body)

    def create_partner_referral(self, tracking_id: str, return_url: str) -> dict:
        body = {"tracking_id": tracking_id, "operations": [{"operation": "API_INTEGRATION", "api_integration_preference": {
            "rest_api_integration": {"integration_method": "PAYPAL", "integration_type": "THIRD_PARTY",
                                     "third_party_details": {"features": ["PAYMENT", "REFUND", "PARTNER_FEE"]}}}}],
                "products": ["EXPRESS_CHECKOUT"], "legal_consents": [{"type": "SHARE_DATA_CONSENT", "granted": True}],
                "partner_config_override": {"return_url": return_url}}
        return self.request("POST", "/v2/customer/partner-referrals", json_body=body)

    def verify_webhook(self, headers: dict, event: dict) -> bool:
        """Uses PayPal's verify-webhook-signature (path/body NOT VERIFIED from specs). Fails closed."""
        if not self.s.paypal_webhook_id:
            return False
        h = {k.lower(): v for k, v in headers.items()}
        try:
            r = self.request("POST", "/v1/notifications/verify-webhook-signature", json_body={
                "auth_algo": h.get("paypal-auth-algo"), "cert_url": h.get("paypal-cert-url"),
                "transmission_id": h.get("paypal-transmission-id"), "transmission_sig": h.get("paypal-transmission-sig"),
                "transmission_time": h.get("paypal-transmission-time"), "webhook_id": self.s.paypal_webhook_id,
                "webhook_event": event})
        except PayPalError:
            return False
        return r.get("verification_status") == "SUCCESS"


def get_client() -> PayPalClient:
    return PayPalClient()


def new_request_id() -> str:
    return str(uuid.uuid4())
