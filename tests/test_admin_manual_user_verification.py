import pytest
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
from bson import ObjectId

from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from app.core.database import user_collection, admin_collection, kyc_verification_collection
from app.main import app


@pytest.mark.asyncio
async def test_admin_manual_user_verification_lifecycle():
    """
    End-to-End Test for Admin Manual User Govt ID Verification:
    1. Setup Admin and User accounts.
    2. User initially has is_verified=False, verified_badge=False.
    3. Admin checks user list and verifies user via POST /api/admin/users/{user_id}/verify.
    4. Profile check via GET /api/profile/me reflects is_verified=True, verified_tick=True, verified_badge=True.
    5. Profile update by user via PUT /api/profile/me does NOT remove verification.
    6. Admin inspects user profile summary via GET /api/admin/users/{user_id} and sees verification details.
    7. Admin deletes user. When a new account is registered with the same email, it starts unverified.
    8. Admin verifies new account and can optionally unverify via POST /api/admin/users/{user_id}/unverify.
    """
    admin_email = "admin_verify_test@eventora.com"
    user_email = "user_verify_test@eventora.com"

    # Clean up test accounts
    await admin_collection.delete_many({"email": admin_email})
    await user_collection.delete_many({"email": {"$in": [user_email, f"deleted_"]}})
    await user_collection.delete_many({"original_email": user_email})
    await kyc_verification_collection.delete_many({"user_email": user_email})

    # Setup admin account
    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpassword123"),
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc)
    })

    # Setup initial user account with complete profile
    user_doc = {
        "email": user_email,
        "name": "Verification Test User",
        "full_name": "Verification Test User",
        "phone_number": "9876543210",
        "date_of_birth": "1995-04-12",
        "address": "789 Salt Lake, Sector V, Kolkata",
        "parent_phone_number": "9876543211",
        "role": "USER",
        "status": "ACTIVE",
        "email_verified": True,
        "registration_complete": True,
        "is_profile_completed": True,
        "profile_completed": True,
        "is_verified": False,
        "is_id_verified": False,
        "is_govt_id_verified": False,
        "verified_badge": False,
        "verified_tick": False,
        "verification_status": "NOT_VERIFIED",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    insert_res = await user_collection.insert_one(user_doc)
    user_id = str(insert_res.inserted_id)

    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    user_token = create_access_token({"sub": user_email, "type": "user"})

    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    user_headers = {"Authorization": f"Bearer {user_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # ── 1. Initial State: User is NOT verified ──
        prof_res = await ac.get("/api/profile/me", headers=user_headers)
        assert prof_res.status_code == 200
        prof_data = prof_res.json()
        assert prof_data["is_verified"] is False
        assert prof_data["verified_badge"] is False
        assert prof_data["verified_tick"] is False
        assert prof_data["verification_status"] == "NOT_VERIFIED"

        # ── 2. Admin Lists Users with filter ──
        admin_users_res = await ac.get("/api/admin/users/?is_verified=false", headers=admin_headers)
        assert admin_users_res.status_code == 200
        unverified_list = admin_users_res.json()
        target_in_list = next((u for u in unverified_list if u.get("email") == user_email), None)
        assert target_in_list is not None
        assert target_in_list["is_verified"] is False

        # ── 3. Admin Manually Verifies User Govt ID ──
        verify_payload = {
            "govt_id_type": "AADHAAR",
            "govt_id_number": "XXXX-XXXX-9012",
            "notes": "Verified Government Aadhaar Card copy physically matched with name and DOB."
        }
        verify_res = await ac.post(
            f"/api/admin/users/{user_id}/verify",
            json=verify_payload,
            headers=admin_headers
        )
        assert verify_res.status_code == 200, f"Verify failed: {verify_res.text}"
        v_data = verify_res.json()
        assert v_data["is_verified"] is True
        assert v_data["is_id_verified"] is True
        assert v_data["verified_badge"] is True
        assert v_data["verified_tick"] is True
        assert v_data["verification_status"] == "VERIFIED"
        assert v_data["verification_details"]["govt_id_type"] == "AADHAAR"
        assert v_data["verification_details"]["govt_id_number"] == "XXXX-XXXX-9012"

        # ── 4. User Profile now shows Verified Badge & Tick ──
        prof_after = await ac.get("/api/profile/me", headers=user_headers)
        assert prof_after.status_code == 200
        prof_after_data = prof_after.json()
        assert prof_after_data["is_verified"] is True
        assert prof_after_data["is_id_verified"] is True
        assert prof_after_data["is_govt_id_verified"] is True
        assert prof_after_data["verified_badge"] is True
        assert prof_after_data["verified_tick"] is True
        assert prof_after_data["verification_status"] == "VERIFIED"
        assert prof_after_data.get("badge_label") == "Verified Profile"

        # Check User Dashboard also returns verified status
        dash_res = await ac.get("/api/profile/dashboard", headers=user_headers)
        assert dash_res.status_code == 200
        dash_profile = dash_res.json()["profile"]
        assert dash_profile["is_verified"] is True
        assert dash_profile["verified_badge"] is True
        assert dash_profile["verified_tick"] is True

        # ── 5. User updates personal details -> verification remains untouched (Lifetime persistence) ──
        up_res = await ac.put(
            "/api/profile/me",
            data={"name": "Verification User Updated", "address": "New Address Lane 5"},
            headers=user_headers
        )
        assert up_res.status_code == 200
        prof_updated = await ac.get("/api/profile/me", headers=user_headers)
        assert prof_updated.status_code == 200
        assert prof_updated.json()["name"] == "Verification User Updated"
        assert prof_updated.json()["is_verified"] is True
        assert prof_updated.json()["verified_badge"] is True
        assert prof_updated.json()["verified_tick"] is True

        # ── 6. Admin User 360° Profile shows Verified status ──
        admin_360 = await ac.get(f"/api/admin/users/{user_id}", headers=admin_headers)
        assert admin_360.status_code == 200
        admin_user_obj = admin_360.json()["user"]
        assert admin_user_obj["is_verified"] is True
        assert admin_user_obj["verified_badge"] is True
        assert admin_user_obj["verified_tick"] is True
        assert admin_user_obj["verification_status"] == "VERIFIED"

        # ── 7. Admin Soft Deletes User ──
        del_res = await ac.delete(f"/api/admin/users/{user_id}", headers=admin_headers)
        assert del_res.status_code == 200

        # Now when a brand NEW account registers with the same email, it starts unverified
        new_user_doc = {
            "email": user_email,
            "name": "New Account Holder",
            "phone_number": "9123456789",
            "date_of_birth": "2001-01-01",
            "address": "Brand New Residence",
            "role": "USER",
            "status": "ACTIVE",
            "email_verified": True,
            "registration_complete": True,
            "is_profile_completed": True,
            "profile_completed": True,
            "is_verified": False,
            "is_id_verified": False,
            "is_govt_id_verified": False,
            "verified_badge": False,
            "verified_tick": False,
            "verification_status": "NOT_VERIFIED",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        new_insert = await user_collection.insert_one(new_user_doc)
        new_user_id = str(new_insert.inserted_id)

        new_user_token = create_access_token({"sub": user_email, "type": "user"})
        new_user_headers = {"Authorization": f"Bearer {new_user_token}"}

        # Verify that new account starts unverified
        new_prof = await ac.get("/api/profile/me", headers=new_user_headers)
        assert new_prof.status_code == 200
        assert new_prof.json()["is_verified"] is False
        assert new_prof.json()["verified_badge"] is False
        assert new_prof.json()["verified_tick"] is False

        # Admin verifies the new account
        verify_new = await ac.post(
            f"/api/admin/users/{new_user_id}/verify",
            json={"govt_id_type": "PAN", "govt_id_number": "ABCDE1234F", "notes": "Verified PAN card for new account."},
            headers=admin_headers
        )
        assert verify_new.status_code == 200
        assert verify_new.json()["is_verified"] is True
        assert verify_new.json()["verified_tick"] is True

        # Check new profile is now verified
        new_prof_verified = await ac.get("/api/profile/me", headers=new_user_headers)
        assert new_prof_verified.status_code == 200
        assert new_prof_verified.json()["is_verified"] is True
        assert new_prof_verified.json()["verified_tick"] is True

        # Admin un-verifies user
        unverify_res = await ac.post(
            f"/api/admin/users/{new_user_id}/unverify",
            json={},
            headers=admin_headers
        )
        assert unverify_res.status_code == 200
        assert unverify_res.json()["is_verified"] is False
        assert unverify_res.json()["verification_status"] == "REVOKED"

    # Cleanup
    await admin_collection.delete_many({"email": admin_email})
    await user_collection.delete_many({"email": {"$in": [user_email, f"deleted_"]}})
    await user_collection.delete_many({"original_email": user_email})
    await kyc_verification_collection.delete_many({"user_email": user_email})
