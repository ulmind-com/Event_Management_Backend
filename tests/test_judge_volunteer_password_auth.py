import pytest
from bson import ObjectId
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone

from app.main import app
from app.core.database import user_collection, otp_collection


@pytest.mark.asyncio
async def test_judge_otp_to_password_lifecycle():
    unique_id = str(ObjectId())
    judge_email = f"judge_{unique_id}@eventora.com"
    
    # 1. Setup Judge in database (initially without password)
    await user_collection.delete_many({"email": judge_email})
    await otp_collection.delete_many({"email": judge_email})

    await user_collection.insert_one({
        "full_name": "Senior Judge Smith",
        "email": judge_email,
        "role": "JUDGE",
        "status": "ACTIVE",
        "is_verified": True,
        "registration_complete": True,
        "created_at": datetime.now(timezone.utc),
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 2. Before password is set, direct password login should return 400
        res_pw_pre = await client.post("/api/judge/auth/login/password", json={
            "email": judge_email,
            "password": "Password123!"
        })
        assert res_pw_pre.status_code == 400
        assert "Password has not been set yet" in res_pw_pre.json()["detail"]

        # 3. Step 1: Send OTP for login
        res_step1 = await client.post("/api/judge/auth/login/step1", json={
            "email": judge_email
        })
        assert res_step1.status_code == 200
        assert "OTP sent" in res_step1.json()["message"]

        # Retrieve OTP directly from DB
        otp_doc = await otp_collection.find_one({"email": judge_email})
        assert otp_doc is not None
        otp_code = otp_doc["otp"]

        # 4. Step 2: Verify OTP -> First login
        res_step2 = await client.post("/api/judge/auth/login/step2", json={
            "email": judge_email,
            "otp": otp_code
        })
        assert res_step2.status_code == 200
        step2_data = res_step2.json()
        assert step2_data["has_password"] is False
        assert step2_data["role"] == "JUDGE"
        judge_token = step2_data["access_token"]
        auth_headers = {"Authorization": f"Bearer {judge_token}"}

        # 5. Set Password validation checks
        # Passwords mismatch
        res_mismatch = await client.post(
            "/api/judge/auth/set-password",
            json={"password": "ValidPassword123", "confirm_password": "WrongPassword123"},
            headers=auth_headers
        )
        assert res_mismatch.status_code == 400
        assert "Passwords do not match" in res_mismatch.json()["detail"]

        # Short password (< 8 chars)
        res_short = await client.post(
            "/api/judge/auth/set-password",
            json={"password": "short", "confirm_password": "short"},
            headers=auth_headers
        )
        assert res_short.status_code == 422

        # 6. Set valid Password
        res_set = await client.post(
            "/api/judge/auth/set-password",
            json={"password": "SuperSecretJudgePassword123!", "confirm_password": "SuperSecretJudgePassword123!"},
            headers=auth_headers
        )
        assert res_set.status_code == 200
        assert res_set.json()["has_password"] is True

        # Check DB updated
        judge_in_db = await user_collection.find_one({"email": judge_email})
        assert judge_in_db.get("has_password") is True
        assert judge_in_db.get("password_hash") is not None

        # 7. Next login: direct login with Email & Password
        # Wrong password check
        res_wrong = await client.post("/api/judge/auth/login/password", json={
            "email": judge_email,
            "password": "WrongPassword!"
        })
        assert res_wrong.status_code == 401
        assert "Incorrect email or password" in res_wrong.json()["detail"]

        # Correct password check
        res_login_pw = await client.post("/api/judge/auth/login/password", json={
            "email": judge_email,
            "password": "SuperSecretJudgePassword123!"
        })
        assert res_login_pw.status_code == 200
        login_data = res_login_pw.json()
        assert login_data["role"] == "JUDGE"
        assert login_data["has_password"] is True
        assert "access_token" in login_data

        # Alias /api/judge/auth/login check
        res_alias = await client.post("/api/judge/auth/login", json={
            "email": judge_email,
            "password": "SuperSecretJudgePassword123!"
        })
        assert res_alias.status_code == 200
        assert "access_token" in res_alias.json()

        # 8. Test change-password
        new_token = login_data["access_token"]
        change_headers = {"Authorization": f"Bearer {new_token}"}
        
        # Wrong current password
        res_cp_wrong = await client.post("/api/judge/auth/change-password", json={
            "current_password": "BadOldPassword!",
            "new_password": "BrandNewJudgePassword456!",
            "confirm_password": "BrandNewJudgePassword456!"
        }, headers=change_headers)
        assert res_cp_wrong.status_code == 401

        # Successful change password
        res_cp_ok = await client.post("/api/judge/auth/change-password", json={
            "current_password": "SuperSecretJudgePassword123!",
            "new_password": "BrandNewJudgePassword456!",
            "confirm_password": "BrandNewJudgePassword456!"
        }, headers=change_headers)
        assert res_cp_ok.status_code == 200

        # Login with newly updated password
        res_new_login = await client.post("/api/judge/auth/login/password", json={
            "email": judge_email,
            "password": "BrandNewJudgePassword456!"
        })
        assert res_new_login.status_code == 200

    # Cleanup
    await user_collection.delete_many({"email": judge_email})
    await otp_collection.delete_many({"email": judge_email})


@pytest.mark.asyncio
async def test_volunteer_otp_to_password_lifecycle():
    unique_id = str(ObjectId())
    volunteer_email = f"volunteer_{unique_id}@eventora.com"
    
    # 1. Setup Volunteer in database
    await user_collection.delete_many({"email": volunteer_email})
    await otp_collection.delete_many({"email": volunteer_email})

    await user_collection.insert_one({
        "email": volunteer_email,
        "role": "VOLUNTEER",
        "registration_complete": True,
        "created_at": datetime.now(timezone.utc),
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 2. Before password set, direct password login returns 400
        res_pw_pre = await client.post("/api/volunteer/auth/login/password", json={
            "email": volunteer_email,
            "password": "Password123!"
        })
        assert res_pw_pre.status_code == 400
        assert "Password has not been set yet" in res_pw_pre.json()["detail"]

        # 3. Step 1: Send OTP
        res_step1 = await client.post("/api/volunteer/auth/login/step1", json={
            "email": volunteer_email
        })
        assert res_step1.status_code == 200

        otp_doc = await otp_collection.find_one({"email": volunteer_email})
        assert otp_doc is not None
        otp_code = otp_doc["otp"]

        # 4. Step 2: Verify OTP
        res_step2 = await client.post("/api/volunteer/auth/login/step2", json={
            "email": volunteer_email,
            "otp": otp_code
        })
        assert res_step2.status_code == 200
        step2_data = res_step2.json()
        assert step2_data["has_password"] is False
        assert step2_data["role"] == "VOLUNTEER"
        vol_token = step2_data["access_token"]
        auth_headers = {"Authorization": f"Bearer {vol_token}"}

        # 5. Set Password
        res_set = await client.post(
            "/api/volunteer/auth/set-password",
            json={"password": "VolunteerSecurePassword123!", "confirm_password": "VolunteerSecurePassword123!"},
            headers=auth_headers
        )
        assert res_set.status_code == 200
        assert res_set.json()["has_password"] is True

        # 6. Direct Email + Password Login
        # Wrong password
        res_wrong = await client.post("/api/volunteer/auth/login/password", json={
            "email": volunteer_email,
            "password": "WrongPassword!"
        })
        assert res_wrong.status_code == 401

        # Correct password
        res_login_pw = await client.post("/api/volunteer/auth/login/password", json={
            "email": volunteer_email,
            "password": "VolunteerSecurePassword123!"
        })
        assert res_login_pw.status_code == 200
        login_data = res_login_pw.json()
        assert login_data["role"] == "VOLUNTEER"
        assert login_data["has_password"] is True
        assert "access_token" in login_data

        # Alias /api/volunteer/auth/login check
        res_alias = await client.post("/api/volunteer/auth/login", json={
            "email": volunteer_email,
            "password": "VolunteerSecurePassword123!"
        })
        assert res_alias.status_code == 200

        # Cross-role isolation checks:
        # Volunteer cannot log in via Judge route
        res_cross_judge = await client.post("/api/judge/auth/login/password", json={
            "email": volunteer_email,
            "password": "VolunteerSecurePassword123!"
        })
        assert res_cross_judge.status_code == 404

    # Cleanup
    await user_collection.delete_many({"email": volunteer_email})
    await otp_collection.delete_many({"email": volunteer_email})
