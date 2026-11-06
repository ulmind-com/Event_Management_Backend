"""
test_verified_user_dob_immutable.py
====================================
End-to-end tests for verified user Date of Birth immutability:
1. Unverified user can freely update their date of birth.
2. Admin verifies user (is_verified: true, verified_badge: true, verification_status: 'VERIFIED').
3. User profile returns is_verified=True and is_dob_locked=True.
4. Verified user attempting to change date_of_birth via PUT /api/profile/me is rejected (400 Bad Request).
5. Verified user attempting to change date_of_birth via POST /api/profile/complete is rejected (400 Bad Request).
6. Verified user can update OTHER profile fields (name, address, phone) without error.
7. Verified user re-submitting the SAME date_of_birth is allowed without error (idempotent).
8. Verified user attempting to alter date_of_birth during competition registration is rejected (400 Bad Request).
9. If admin un-verifies user, date_of_birth modification is unlocked.
"""

import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from bson import ObjectId

from app.main import app
from app.core.security import create_access_token, get_password_hash
from app.core.database import (
    user_collection,
    admin_collection,
    competition_collection,
    competition_registration_collection,
)


@pytest.mark.asyncio
async def test_verified_user_cannot_modify_date_of_birth():
    # Setup test admin and user
    ts = int(datetime.now().timestamp())
    admin_email = f"superadmin_dob_{ts}@test.com"
    user_email = f"verified_user_{ts}@test.com"

    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpass123"),
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc),
    })
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Insert active unverified user
    user_insert = await user_collection.insert_one({
        "email": user_email,
        "name": "Jane Contestant",
        "phone_number": 9876543201,
        "date_of_birth": "2000-01-01",
        "address": "100 Initial Road",
        "parent_phone_number": 9876543202,
        "role": "USER",
        "is_verified": False,
        "is_id_verified": False,
        "is_govt_id_verified": False,
        "verified_badge": False,
        "registration_complete": True,
        "is_profile_completed": True,
        "profile_completed": True,
    })
    user_id = str(user_insert.inserted_id)

    user_token = create_access_token({"sub": user_email, "email": user_email, "role": "USER"})
    user_headers = {"Authorization": f"Bearer {user_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Unverified user CAN update their date of birth
        res_unverified = await ac.put(
            "/api/profile/me",
            data={"date_of_birth": "2002-05-15"},
            headers=user_headers
        )
        assert res_unverified.status_code == 200
        assert res_unverified.json()["user"]["date_of_birth"] == "2002-05-15"
        assert res_unverified.json()["user"]["is_verified"] is False
        assert res_unverified.json()["user"]["is_dob_locked"] is False

        # 2. Admin verifies the user
        verify_res = await ac.post(
            f"/api/admin/users/{user_id}/verify-govt-id",
            json={"notes": "Aadhaar verified in person."},
            headers=admin_headers
        )
        assert verify_res.status_code == 200
        assert verify_res.json()["is_verified"] is True

        # 3. User profile now shows is_verified=True and is_dob_locked=True
        profile_res = await ac.get("/api/profile/me", headers=user_headers)
        assert profile_res.status_code == 200
        profile_data = profile_res.json()
        assert profile_data["is_verified"] is True
        assert profile_data["is_dob_locked"] is True
        assert profile_data["date_of_birth"] == "2002-05-15"

        # 4. Verified user tries to CHANGE date_of_birth via PUT /api/profile/me -> 400 Bad Request
        modify_put_res = await ac.put(
            "/api/profile/me",
            data={"date_of_birth": "2010-01-01"},
            headers=user_headers
        )
        assert modify_put_res.status_code == 400
        assert "verified by admin" in modify_put_res.json()["detail"].lower()
        assert "cannot modify your date of birth" in modify_put_res.json()["detail"].lower()

        # Verify DB date_of_birth remained untouched
        usr_in_db = await user_collection.find_one({"_id": ObjectId(user_id)})
        assert usr_in_db["date_of_birth"] == "2002-05-15"

        # 5. Verified user tries to CHANGE date_of_birth via POST /api/profile/complete -> 400 Bad Request
        modify_json_res = await ac.post(
            "/api/profile/complete",
            json={"date_of_birth": "2010-01-01", "name": "Jane New Name"},
            headers=user_headers
        )
        assert modify_json_res.status_code == 400
        assert "verified by admin" in modify_json_res.json()["detail"].lower()

        # 6. Verified user updates OTHER fields (address, name) -> SUCCESS
        other_update = await ac.put(
            "/api/profile/me",
            data={"name": "Jane Verified", "address": "999 Verified Avenue"},
            headers=user_headers
        )
        assert other_update.status_code == 200
        assert other_update.json()["user"]["name"] == "Jane Verified"
        assert other_update.json()["user"]["address"] == "999 Verified Avenue"
        assert other_update.json()["user"]["date_of_birth"] == "2002-05-15"

        # 7. Verified user re-submits the SAME date_of_birth -> SUCCESS (no error)
        same_dob_res = await ac.put(
            "/api/profile/me",
            data={"date_of_birth": "2002-05-15", "address": "1000 Verified Boulevard"},
            headers=user_headers
        )
        assert same_dob_res.status_code == 200
        assert same_dob_res.json()["user"]["date_of_birth"] == "2002-05-15"

        # 8. Verified user attempts to change DOB during competition participation -> 400 Bad Request
        now = datetime.now(timezone.utc)
        comp_payload = {
            "title": f"Championship For DOB Lock {ts}",
            "description": "Testing verified DOB immutability during registration",
            "categories": [{"name": "Junior", "age_limit": "12-15"}],
            "start_date": (now - timedelta(days=1)).isoformat(),
            "end_date": (now + timedelta(days=30)).isoformat(),
            "registration_start_date": (now - timedelta(days=1)).isoformat(),
            "submission_deadline": (now + timedelta(days=25)).isoformat(),
            "is_published": True,
            "judge_scoring_enabled": True,
            "audience_voting_enabled": False,
        }
        c_res = await ac.post("/api/admin/advanced-competitions/", json=comp_payload, headers=admin_headers)
        assert c_res.status_code == 200
        comp_id = c_res.json()["_id"]

        # Verified user tries to pass date_of_birth="2013-05-15" to pretend they are Junior (13 yrs old)
        fraud_reg = await ac.post(
            f"/api/v1/competitions/{comp_id}/categories/Junior/participate",
            json={"rules_accepted": True, "date_of_birth": "2013-05-15"},
            headers=user_headers
        )
        assert fraud_reg.status_code == 400
        assert "verified by admin" in fraud_reg.json()["detail"].lower()

        # 9. Admin un-verifies user -> user can now change date_of_birth again
        unverify_res = await ac.post(
            f"/api/admin/users/{user_id}/unverify?reason=User+requested+correction",
            headers=admin_headers
        )
        assert unverify_res.status_code == 200
        assert unverify_res.json()["is_verified"] is False

        # Now update DOB succeeds
        unlock_update = await ac.put(
            "/api/profile/me",
            data={"date_of_birth": "2001-11-20"},
            headers=user_headers
        )
        assert unlock_update.status_code == 200
        assert unlock_update.json()["user"]["date_of_birth"] == "2001-11-20"
