import pytest
import hmac
import hashlib
import json
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone, timedelta
from bson import ObjectId

from app.main import app
from app.core.database import (
    user_collection,
    admin_collection,
    event_collection,
    vip_configuration_collection,
    vip_booking_collection,
    vip_pass_collection,
    vip_checkin_collection,
)
from app.core.security import create_access_token
from app.services.vip_payment_gateway import VIPPaymentGatewayAdapter
from app.services.vip_service import VIPService

pytestmark = pytest.mark.asyncio

async def setup_vip_test_data():
    now_utc = datetime.now(timezone.utc)
    user1_email = "vip_alice@viptest.com"
    user2_email = "vip_bob@viptest.com"
    admin_email = "vip_superadmin@viptest.com"

    await user_collection.delete_many({"email": {"$in": [user1_email, user2_email]}})
    await admin_collection.delete_many({"email": admin_email})

    u1_res = await user_collection.insert_one({
        "name": "Alice VIP",
        "email": user1_email,
        "role": "USER",
        "registration_complete": True,
        "created_at": now_utc
    })

    u2_res = await user_collection.insert_one({
        "name": "Bob VIP",
        "email": user2_email,
        "role": "USER",
        "registration_complete": True,
        "created_at": now_utc
    })

    adm_res = await admin_collection.insert_one({
        "email": admin_email,
        "name": "Super Admin",
        "role": "SUPER_ADMIN",
        "created_at": now_utc
    })

    # Test Event
    event_res = await event_collection.insert_one({
        "title": "Grand Tech Festival 2026",
        "name": "Grand Tech Festival 2026",
        "is_published": True,
        "registration_enabled": True,
        "ticket_enabled": True,
        "created_at": now_utc
    })

    user1_token = create_access_token(data={"sub": user1_email, "type": "user"})
    user2_token = create_access_token(data={"sub": user2_email, "type": "user"})
    admin_token = create_access_token(data={"sub": admin_email, "type": "admin"})

    return {
        "event_id": str(event_res.inserted_id),
        "user1_id": str(u1_res.inserted_id),
        "user1_email": user1_email,
        "user1_token": user1_token,
        "user2_id": str(u2_res.inserted_id),
        "user2_email": user2_email,
        "user2_token": user2_token,
        "admin_token": admin_token,
        "admin_email": admin_email
    }


# ──────────────────────────────────────────────────────────
# 1. Super Admin Configuration & Paid-Only Policy Tests
# ──────────────────────────────────────────────────────────

async def test_super_admin_create_vip_configuration_success():
    data = await setup_vip_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        payload = {
            "name": "Gold VIP Pass",
            "category": "Premium VIP",
            "description": "Exclusive access to main hall and VIP lounge",
            "price": 1499.0, # Positive price
            "currency": "INR",
            "capacity": 5,
            "booking_start": (now - timedelta(hours=1)).isoformat(),
            "booking_end": (now + timedelta(days=5)).isoformat(),
            "validity_start": now.isoformat(),
            "validity_end": (now + timedelta(days=2)).isoformat(),
            "max_passes_per_user": 2,
            "access_areas": ["MAIN_GATE", "VIP_LOUNGE"],
            "entry_policy": "SINGLE_ENTRY",
            "is_enabled": True
        }
        res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json=payload,
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert res.status_code == 201
        body = res.json()
        assert body["name"] == "Gold VIP Pass"
        assert body["price"] == 1499.0
        assert body["capacity"]["total_capacity"] == 5
        assert body["capacity"]["available_count"] == 5
        assert body["capacity"]["sold_out"] is False

async def test_ordinary_user_cannot_create_vip_configuration():
    data = await setup_vip_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        payload = {
            "name": "Hacker VIP",
            "price": 500.0,
            "capacity": 10,
            "booking_start": now.isoformat(),
            "booking_end": (now + timedelta(days=1)).isoformat(),
            "validity_start": now.isoformat(),
            "validity_end": (now + timedelta(days=1)).isoformat()
        }
        # Attempt with user token -> Must be rejected with 403
        res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json=payload,
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert res.status_code == 403

async def test_zero_price_free_vip_configuration_strictly_rejected():
    data = await setup_vip_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        payload = {
            "name": "Illegal Free VIP",
            "price": 0.0, # Zero price -> Strictly prohibited!
            "capacity": 10,
            "booking_start": now.isoformat(),
            "booking_end": (now + timedelta(days=1)).isoformat(),
            "validity_start": now.isoformat(),
            "validity_end": (now + timedelta(days=1)).isoformat(),
            "is_enabled": True
        }
        res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json=payload,
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        # Must be rejected with 422 or 400
        assert res.status_code in [400, 422]

# ──────────────────────────────────────────────────────────
# 2. Discovery & Booking Window Tests
# ──────────────────────────────────────────────────────────

async def test_user_discovery_with_live_capacity_stats():
    data = await setup_vip_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        # Create enabled config
        c1_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json={
                "name": "Platinum VIP",
                "price": 2999.0,
                "capacity": 10,
                "booking_start": (now - timedelta(hours=1)).isoformat(),
                "booking_end": (now + timedelta(days=2)).isoformat(),
                "validity_start": now.isoformat(),
                "validity_end": (now + timedelta(days=1)).isoformat(),
                "is_enabled": True
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert c1_res.status_code == 201
        c1_id = c1_res.json()["id"]

        # Create disabled config
        await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json={
                "name": "Hidden VIP",
                "price": 5000.0,
                "capacity": 5,
                "booking_start": now.isoformat(),
                "booking_end": (now + timedelta(days=1)).isoformat(),
                "validity_start": now.isoformat(),
                "validity_end": (now + timedelta(days=1)).isoformat(),
                "is_enabled": False
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )

        # Public discovery should only return enabled configs
        disc_res = await ac.get(f"/api/events/{data['event_id']}/vip-passes")
        assert disc_res.status_code == 200
        passes = disc_res.json()
        assert len(passes) >= 1
        assert any(p["name"] == "Platinum VIP" for p in passes)
        assert not any(p["name"] == "Hidden VIP" for p in passes)

        target = next(p for p in passes if p["name"] == "Platinum VIP")
        assert target["capacity"]["total_capacity"] == 10
        assert target["capacity"]["available_count"] == 10
        assert target["capacity"]["booking_open"] is True

async def test_disabled_or_out_of_window_booking_rejected():
    data = await setup_vip_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        # Config where booking closed in past
        c_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json={
                "name": "Closed VIP",
                "price": 999.0,
                "capacity": 10,
                "booking_start": (now - timedelta(days=5)).isoformat(),
                "booking_end": (now - timedelta(days=1)).isoformat(), # closed!
                "validity_start": now.isoformat(),
                "validity_end": (now + timedelta(days=1)).isoformat(),
                "is_enabled": True
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert c_res.status_code == 201
        closed_id = c_res.json()["id"]

        # Attempt to book -> 400 Bad Request
        book_res = await ac.post(
            f"/api/events/{data['event_id']}/vip-passes/{closed_id}/book",
            json={"quantity": 1},
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert book_res.status_code == 400
        assert "closed" in book_res.json()["detail"].lower()

# ──────────────────────────────────────────────────────────
# 3. User Booking, Capacity Allocation & Sold Out Tests
# ──────────────────────────────────────────────────────────

async def test_user_booking_paid_pending_and_atomic_allocation():
    data = await setup_vip_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        c_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json={
                "name": "Silver VIP",
                "price": 800.0,
                "capacity": 2, # Small capacity to test allocation
                "booking_start": (now - timedelta(hours=1)).isoformat(),
                "booking_end": (now + timedelta(days=1)).isoformat(),
                "validity_start": now.isoformat(),
                "validity_end": (now + timedelta(days=2)).isoformat(),
                "max_passes_per_user": 2,
                "is_enabled": True
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert c_res.status_code == 201
        config_id = c_res.json()["id"]

        # Book 1 pass
        b_res = await ac.post(
            f"/api/events/{data['event_id']}/vip-passes/{config_id}/book",
            json={"quantity": 1, "holder_names": ["Alice VIP"]},
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert b_res.status_code == 201
        b_data = b_res.json()
        assert b_data["booking_status"] == "PENDING_PAYMENT"
        assert b_data["total_amount"] == 800.0
        assert b_data["capacity"]["pending_payment_count"] == 1
        assert b_data["capacity"]["available_count"] == 1
        assert b_data["capacity"]["sold_out"] is False

        # Book 2nd pass with user 2 -> Now reaches capacity
        b2_res = await ac.post(
            f"/api/events/{data['event_id']}/vip-passes/{config_id}/book",
            json={"quantity": 1, "holder_names": ["Bob VIP"]},
            headers={"Authorization": f"Bearer {data['user2_token']}"}
        )
        assert b2_res.status_code == 201
        b2_data = b2_res.json()
        assert b2_data["capacity"]["pending_payment_count"] == 2
        assert b2_data["capacity"]["available_count"] == 0
        assert b2_data["capacity"]["sold_out"] is True

        # 3rd booking attempt -> SOLD OUT!
        b3_res = await ac.post(
            f"/api/events/{data['event_id']}/vip-passes/{config_id}/book",
            json={"quantity": 1},
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert b3_res.status_code == 409
        assert "sold out" in b3_res.json()["detail"].lower()

# ──────────────────────────────────────────────────────────
# 4. Webhook Verification, Idempotency & Pass Issuance
# ──────────────────────────────────────────────────────────

async def test_valid_webhook_confirms_booking_and_issues_vip_pass():
    data = await setup_vip_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        c_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json={
                "name": "Diamond VIP",
                "price": 5000.0,
                "capacity": 10,
                "booking_start": (now - timedelta(hours=1)).isoformat(),
                "booking_end": (now + timedelta(days=1)).isoformat(),
                "validity_start": now.isoformat(),
                "validity_end": (now + timedelta(days=2)).isoformat(),
                "access_areas": ["MAIN_GATE", "VIP_LOUNGE", "BACKSTAGE"],
                "entry_policy": "SINGLE_ENTRY",
                "is_enabled": True
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert c_res.status_code == 201
        config_id = c_res.json()["id"]

        # Book pass
        book_res = await ac.post(
            f"/api/events/{data['event_id']}/vip-passes/{config_id}/book",
            json={"quantity": 1, "holder_names": ["Alice Star"]},
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert book_res.status_code == 201
        booking_ref = book_res.json()["booking_reference"]

        # 1. Invalid webhook signature -> Must be rejected (400)
        invalid_wh = await ac.post(
            "/api/vip/payments/webhook",
            json={"booking_reference": booking_ref, "amount": 5000.0, "status": "PAID"},
            headers={"X-Webhook-Signature": "invalid_fake_sig"}
        )
        assert invalid_wh.status_code == 400

        # 2. Valid webhook signature
        test_secret = "test_webhook_secret_key_12345"
        import os
        os.environ["PAYMENT_WEBHOOK_SECRET"] = test_secret

        wh_payload = {
            "event": "payment.captured",
            "booking_reference": booking_ref,
            "transaction_id": "pay_test_txn_998877",
            "provider_order_id": f"order_{booking_ref}",
            "amount": 5000.0,
            "currency": "INR",
            "status": "PAID"
        }
        raw_body = json.dumps(wh_payload).encode("utf-8")
        valid_sig = hmac.new(test_secret.encode(), raw_body, hashlib.sha256).hexdigest()

        wh_res = await ac.post(
            "/api/vip/payments/webhook",
            content=raw_body,
            headers={"Content-Type": "application/json", "X-Webhook-Signature": valid_sig}
        )
        assert wh_res.status_code == 200
        assert wh_res.json()["status"] == "SUCCESS"

        # Check DB: booking must be CONFIRMED and VIP pass issued
        b_doc = await vip_booking_collection.find_one({"booking_reference": booking_ref})
        assert b_doc["booking_status"] == "CONFIRMED"
        assert b_doc["payment_status"] == "PAID"
        assert len(b_doc["issued_pass_ids"]) == 1

        pass_doc = await vip_pass_collection.find_one({"booking_reference": booking_ref})
        assert pass_doc is not None
        assert pass_doc["status"] == "VALID"
        assert pass_doc["holder_name"] == "Alice Star"
        assert pass_doc["token_hash"] is not None
        assert pass_doc["qr_image_base64"] is not None

        # 3. Duplicate Webhook Delivery is Idempotent
        wh_dup_res = await ac.post(
            "/api/vip/payments/webhook",
            content=raw_body,
            headers={"Content-Type": "application/json", "X-Webhook-Signature": valid_sig}
        )
        assert wh_dup_res.status_code == 200
        # Passes count in DB should still be exactly 1!
        passes_in_db = await vip_pass_collection.count_documents({"booking_reference": booking_ref})
        assert passes_in_db == 1

# ──────────────────────────────────────────────────────────
# 5. Access Control, Gate Verification & Re-entry Tests
# ──────────────────────────────────────────────────────────

async def test_vip_pass_entry_verification_approved():
    data = await setup_vip_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        c_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json={
                "name": "Access VIP",
                "price": 1000.0,
                "capacity": 5,
                "booking_start": (now - timedelta(hours=1)).isoformat(),
                "booking_end": (now + timedelta(days=1)).isoformat(),
                "validity_start": (now - timedelta(hours=1)).isoformat(),
                "validity_end": (now + timedelta(days=2)).isoformat(),
                "access_areas": ["MAIN_GATE", "VIP_LOUNGE"],
                "entry_policy": "SINGLE_ENTRY",
                "is_enabled": True
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        config_id = c_res.json()["id"]

        # Manual Issue to test verification
        issue_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-passes/issue?configuration_id={config_id}",
            json={
                "user_email": data["user1_email"],
                "holder_name": "Alice Verified",
                "payment_reference": "INV-AUDIT-2026-001",
                "amount_paid": 1000.0,
                "currency": "INR"
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert issue_res.status_code == 201
        pass_data = issue_res.json()
        qr_token = pass_data["qr_token"]

        # 1. Gate Scan at MAIN_GATE -> Approved!
        verify_res = await ac.post(
            "/api/admin/vip-passes/verify",
            json={
                "verification_token": qr_token,
                "event_id": data["event_id"],
                "access_area_id": "MAIN_GATE",
                "gate_id": "GATE_A"
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert verify_res.status_code == 200
        v_body = verify_res.json()
        assert v_body["allowed"] is True
        assert v_body["outcome"] == "ENTRY_APPROVED"
        assert v_body["holder_name"] == "Alice Verified"

        # 2. Second Scan of Single Entry Pass -> DUPLICATE_ENTRY Rejection!
        dup_res = await ac.post(
            "/api/admin/vip-passes/verify",
            json={
                "verification_token": qr_token,
                "event_id": data["event_id"],
                "access_area_id": "MAIN_GATE",
                "gate_id": "GATE_A"
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert dup_res.status_code == 200
        dup_body = dup_res.json()
        assert dup_body["allowed"] is False
        assert dup_body["outcome"] == "DUPLICATE_ENTRY"

async def test_unauthorized_access_area_rejected():
    data = await setup_vip_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        c_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json={
                "name": "Standard Lounge VIP",
                "price": 1200.0,
                "capacity": 5,
                "booking_start": (now - timedelta(hours=1)).isoformat(),
                "booking_end": (now + timedelta(days=1)).isoformat(),
                "validity_start": (now - timedelta(hours=1)).isoformat(),
                "validity_end": (now + timedelta(days=2)).isoformat(),
                "access_areas": ["MAIN_GATE", "VIP_LOUNGE"], # BACKSTAGE NOT INCLUDED
                "is_enabled": True
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        config_id = c_res.json()["id"]

        issue_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-passes/issue?configuration_id={config_id}",
            json={
                "user_email": data["user1_email"],
                "holder_name": "Lounge Only Guest",
                "payment_reference": "INV-AUDIT-2026-002",
                "amount_paid": 1200.0
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        qr_token = issue_res.json()["qr_token"]

        # Scan at BACKSTAGE -> ACCESS_DENIED!
        bs_res = await ac.post(
            "/api/admin/vip-passes/verify",
            json={
                "verification_token": qr_token,
                "event_id": data["event_id"],
                "access_area_id": "BACKSTAGE", # Unauthorized!
                "gate_id": "STAGE_DOOR_1"
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert bs_res.status_code == 200
        bs_body = bs_res.json()
        assert bs_body["allowed"] is False
        assert bs_body["outcome"] == "ACCESS_DENIED"

async def test_revoked_pass_entry_rejected():
    data = await setup_vip_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        c_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json={
                "name": "Revoke Test VIP",
                "price": 2000.0,
                "capacity": 5,
                "booking_start": (now - timedelta(hours=1)).isoformat(),
                "booking_end": (now + timedelta(days=1)).isoformat(),
                "validity_start": (now - timedelta(hours=1)).isoformat(),
                "validity_end": (now + timedelta(days=2)).isoformat(),
                "access_areas": ["MAIN_GATE"],
                "is_enabled": True
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        config_id = c_res.json()["id"]

        issue_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-passes/issue?configuration_id={config_id}",
            json={
                "user_email": data["user1_email"],
                "holder_name": "Bad Actor",
                "payment_reference": "INV-AUDIT-2026-003",
                "amount_paid": 2000.0
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        pass_id = issue_res.json()["pass_id"]
        qr_token = issue_res.json()["qr_token"]

        # Revoke pass
        rev_res = await ac.post(
            f"/api/admin/vip-passes/{pass_id}/revoke",
            json={"reason": "Security policy violation"},
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert rev_res.status_code == 200

        # Verification attempt -> PASS_REVOKED
        v_res = await ac.post(
            "/api/admin/vip-passes/verify",
            json={
                "verification_token": qr_token,
                "event_id": data["event_id"],
                "access_area_id": "MAIN_GATE",
                "gate_id": "GATE_A"
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert v_res.status_code == 200
        assert v_res.json()["allowed"] is False
        assert v_res.json()["outcome"] == "PASS_REVOKED"

# ──────────────────────────────────────────────────────────
# 6. User Library & Admin User Management Integration Tests
# ──────────────────────────────────────────────────────────

async def test_user_pass_library_and_admin_user_history():
    data = await setup_vip_test_data()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        now = datetime.now(timezone.utc)
        c_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-configurations",
            json={
                "name": "Festival VIP",
                "price": 1500.0,
                "capacity": 10,
                "booking_start": (now - timedelta(hours=1)).isoformat(),
                "booking_end": (now + timedelta(days=1)).isoformat(),
                "validity_start": (now - timedelta(hours=1)).isoformat(),
                "validity_end": (now + timedelta(days=2)).isoformat(),
                "is_enabled": True
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        config_id = c_res.json()["id"]

        # Issue 1 pass to Alice
        issue_res = await ac.post(
            f"/api/admin/events/{data['event_id']}/vip-passes/issue?configuration_id={config_id}",
            json={
                "user_email": data["user1_email"],
                "holder_name": "Alice VIP",
                "payment_reference": "INV-AUDIT-2026-004",
                "amount_paid": 1500.0
            },
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        pass_id = issue_res.json()["pass_id"]

        # 1. Alice views her passes
        my_passes_res = await ac.get(
            "/api/users/me/vip-passes",
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert my_passes_res.status_code == 200
        p_body = my_passes_res.json()
        assert p_body["total"] >= 1
        assert any(p["pass_id"] == pass_id for p in p_body["passes"])

        # 2. Alice retrieves QR code
        qr_res = await ac.get(
            f"/api/users/me/vip-passes/{pass_id}/qr",
            headers={"Authorization": f"Bearer {data['user1_token']}"}
        )
        assert qr_res.status_code == 200
        qr_body = qr_res.json()
        assert qr_body["qr_token"] is not None
        assert qr_body["qr_image_base64"] is not None

        # 3. Super Admin inspects Alice's VIP bookings in 360 User Profile
        admin_user_vip_res = await ac.get(
            f"/api/admin/users/{data['user1_id']}/vip-bookings",
            headers={"Authorization": f"Bearer {data['admin_token']}"}
        )
        assert admin_user_vip_res.status_code == 200
        adm_usr_body = admin_user_vip_res.json()
        assert adm_usr_body["email"] == data["user1_email"]
        assert adm_usr_body["total_bookings"] >= 1
        assert adm_usr_body["active_passes"] >= 1
