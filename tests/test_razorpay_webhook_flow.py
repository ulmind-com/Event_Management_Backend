import pytest
import hmac
import hashlib
import json
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings
from app.services.payment import PaymentService
from app.core.database import (
    competition_collection,
    competition_registration_collection,
    booking_collection,
    user_collection,
)


def _compute_webhook_signature(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_razorpay_webhook_endpoints_health_check():
    """Verify GET endpoints on all supported webhook URLs return 200 OK."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Root endpoint
        resp_root = await client.get("/razorpay/webhook")
        assert resp_root.status_code == 200
        data_root = resp_root.json()
        assert data_root["status"] == "active"
        assert data_root["service"] == "razorpay-webhook"

        # Under /api prefix
        resp_api = await client.get("/api/razorpay/webhook")
        assert resp_api.status_code == 200
        data_api = resp_api.json()
        assert data_api["status"] == "active"

        # Under /api/payments prefix
        resp_pay = await client.get("/api/payments/razorpay/webhook")
        assert resp_pay.status_code == 200
        data_pay = resp_pay.json()
        assert data_pay["status"] == "active"


@pytest.mark.asyncio
async def test_razorpay_webhook_missing_and_invalid_signature():
    """Verify security validation: missing signature and invalid signature both return 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {"event": "payment.captured", "id": f"evt_{uuid.uuid4().hex[:12]}"}
        payload_bytes = json.dumps(payload).encode("utf-8")

        # Missing signature
        resp_no_sig = await client.post(
            "/razorpay/webhook",
            content=payload_bytes,
            headers={"Content-Type": "application/json"}
        )
        assert resp_no_sig.status_code == 400
        assert "Missing X-Razorpay-Signature header" in resp_no_sig.json()["detail"]

        # Invalid signature
        resp_bad_sig = await client.post(
            "/razorpay/webhook",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": "invalid_signature_hex_12345"
            }
        )
        assert resp_bad_sig.status_code == 400
        assert "Invalid Razorpay webhook signature" in resp_bad_sig.json()["detail"]


@pytest.mark.asyncio
async def test_razorpay_webhook_live_secret_verification_unit():
    """Verify that PaymentService.verify_webhook_signature succeeds with configured secrets."""
    secret = settings.RAZORPAY_KEY_SECRET or "ukDfU9e7NAEI8eyz5OpMMdgT"
    payload = b'{"event":"payment.captured","test":true}'
    valid_sig = _compute_webhook_signature(payload, secret)

    is_valid = PaymentService.verify_webhook_signature(payload, valid_sig)
    assert is_valid is True

    is_invalid = PaymentService.verify_webhook_signature(payload, "invalid_sig_abc")
    assert is_invalid is False


@pytest.mark.asyncio
async def test_razorpay_webhook_competition_payment_captured():
    """Verify POST /razorpay/webhook successfully processes competition payment."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        test_uid = uuid.uuid4().hex[:8]
        reg_ref = f"CRG-WEBHOOK-{test_uid}"
        test_email = f"webhook_tester_{test_uid}@example.com"
        secret = settings.RAZORPAY_KEY_SECRET or "ukDfU9e7NAEI8eyz5OpMMdgT"

        # Create user & competition & registration
        await user_collection.update_one(
            {"email": test_email},
            {"$set": {"email": test_email, "role": "USER", "full_name": "Webhook User"}},
            upsert=True
        )
        comp_res = await competition_collection.insert_one({
            "title": f"Webhook Comp {test_uid}",
            "participation_fee": 199.0,
            "status": "PUBLISHED",
            "registered_count": 0
        })

        await competition_registration_collection.insert_one({
            "registration_reference": reg_ref,
            "competition_id": str(comp_res.inserted_id),
            "user_email": test_email,
            "status": "PAYMENT_PENDING",
            "payment_status": "PENDING",
            "provider_order_id": f"order_hook_{test_uid}",
            "amount": 199.0,
        })

        webhook_event = {
            "id": f"evt_comp_{test_uid}",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": f"pay_hook_{test_uid}",
                        "order_id": f"order_hook_{test_uid}",
                        "amount": 19900,
                        "currency": "INR",
                        "status": "captured",
                        "notes": {
                            "booking_reference": reg_ref,
                            "type": "competition_registration"
                        }
                    }
                }
            }
        }
        body_bytes = json.dumps(webhook_event).encode("utf-8")
        sig = _compute_webhook_signature(body_bytes, secret)

        # Call POST /razorpay/webhook
        resp = await client.post(
            "/razorpay/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": sig
            }
        )
        assert resp.status_code == 200
        res_data = resp.json()
        assert res_data["status"] in ["PROCESSED", "DUPLICATE_WEBHOOK_ACKNOWLEDGED"]

        # Verify registration was confirmed
        updated_reg = await competition_registration_collection.find_one({"registration_reference": reg_ref})
        assert updated_reg is not None
        assert updated_reg.get("payment_status") == "PAID"
        assert updated_reg.get("status") == "CONFIRMED"


@pytest.mark.asyncio
async def test_razorpay_webhook_api_path_alias():
    """Verify POST /api/razorpay/webhook works equivalently via the API prefix alias."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        test_uid = uuid.uuid4().hex[:8]
        booking_ref = f"BK-WEBHOOK-{test_uid}"
        test_email = f"booker_{test_uid}@example.com"
        secret = settings.RAZORPAY_KEY_SECRET or "ukDfU9e7NAEI8eyz5OpMMdgT"

        await booking_collection.insert_one({
            "booking_reference": booking_ref,
            "user_email": test_email,
            "payment_status": "PENDING",
            "provider_order_id": f"order_bk_{test_uid}",
            "total_amount": 500.0,
            "status": "INITIATED"
        })

        webhook_event = {
            "id": f"evt_bk_{test_uid}",
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": f"pay_bk_{test_uid}",
                        "order_id": f"order_bk_{test_uid}",
                        "amount": 50000,
                        "currency": "INR",
                        "status": "captured",
                        "notes": {
                            "booking_reference": booking_ref
                        }
                    }
                }
            }
        }
        body_bytes = json.dumps(webhook_event).encode("utf-8")
        sig = _compute_webhook_signature(body_bytes, secret)

        resp = await client.post(
            "/api/razorpay/webhook",
            content=body_bytes,
            headers={
                "Content-Type": "application/json",
                "x-razorpay-signature": sig
            }
        )
        assert resp.status_code == 200
        assert resp.json()["status"] in ["PROCESSED", "DUPLICATE_WEBHOOK_ACKNOWLEDGED"]

        # Verify booking confirmed
        updated_booking = await booking_collection.find_one({"booking_reference": booking_ref})
        assert updated_booking is not None
        assert updated_booking.get("payment_status") in ["PAID", "CONFIRMED"]
