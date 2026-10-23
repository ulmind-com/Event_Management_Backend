import pytest
from bson import ObjectId
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone

from app.main import app
from app.core.database import user_collection, otp_collection, admin_collection
from app.core.security import create_access_token


@pytest.mark.asyncio
async def test_full_admin_invitation_to_judge_password_flow():
    unique_id = str(ObjectId())
    admin_email = f"admin_{unique_id}@eventora.com"
    judge_email = f"invited_judge_{unique_id}@eventora.com"

    # Clean up DB
    await user_collection.delete_many({"email": {"$in": [judge_email, admin_email]}})
    await admin_collection.delete_many({"email": admin_email})
    await otp_collection.delete_many({"email": judge_email})

    # Setup Super Admin
    await admin_collection.insert_one({
        "email": admin_email,
        "name": "Super Admin",
        "role": "super_admin"
    })
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Admin creates Judge
        res_create = await client.post(
            "/api/admin/judges/",
            json={"full_name": "Dr. Arindam Roy", "email": judge_email},
            headers=admin_headers
        )
        assert res_create.status_code == 200
        assert res_create.json()["email"] == judge_email

        # Step 2: Admin triggers invitation email
        res_invite = await client.post(
            f"/api/admin/judges/{judge_email}/send-invitation",
            headers=admin_headers
        )
        assert res_invite.status_code == 200
        assert "Invitation sent" in res_invite.json()["message"]

        # Step 3: Judge receives email and enters email on login page
        res_step1 = await client.post("/api/judge/auth/login/step1", json={
            "email": judge_email
        })
        assert res_step1.status_code == 200
        assert "OTP sent" in res_step1.json()["message"]

        # Retrieve generated OTP from DB
        otp_doc = await otp_collection.find_one({"email": judge_email})
        assert otp_doc is not None
        otp_code = otp_doc["otp"]

        # Step 4: Judge submits OTP -> first login
        res_step2 = await client.post("/api/judge/auth/login/step2", json={
            "email": judge_email,
            "otp": otp_code
        })
        assert res_step2.status_code == 200
        step2_data = res_step2.json()
        assert step2_data["has_password"] is False
        assert step2_data["role"] == "JUDGE"
        judge_token = step2_data["access_token"]
        judge_headers = {"Authorization": f"Bearer {judge_token}"}

        # Step 5: Judge sets permanent password
        res_set_pw = await client.post(
            "/api/judge/auth/set-password",
            json={
                "password": "JudgeSecretPassword2026!",
                "confirm_password": "JudgeSecretPassword2026!"
            },
            headers=judge_headers
        )
        assert res_set_pw.status_code == 200
        assert res_set_pw.json()["has_password"] is True

        # Verify DB has password_hash and has_password=True
        judge_db = await user_collection.find_one({"email": judge_email})
        assert judge_db["has_password"] is True
        assert judge_db["password_hash"] is not None

        # Step 6: Judge logs out and logs in next time with Email & Password (NO OTP NEEDED)
        # Verify invalid password rejected
        res_wrong_pw = await client.post("/api/judge/auth/login/password", json={
            "email": judge_email,
            "password": "WrongPassword!"
        })
        assert res_wrong_pw.status_code == 401
        assert "Incorrect email or password" in res_wrong_pw.json()["detail"]

        # Verify correct Email + Password login succeeds
        res_pw_login = await client.post("/api/judge/auth/login/password", json={
            "email": judge_email,
            "password": "JudgeSecretPassword2026!"
        })
        assert res_pw_login.status_code == 200
        login_res = res_pw_login.json()
        assert login_res["role"] == "JUDGE"
        assert login_res["has_password"] is True
        assert "access_token" in login_res

    # Clean up
    await user_collection.delete_many({"email": judge_email})
    await admin_collection.delete_many({"email": admin_email})
    await otp_collection.delete_many({"email": judge_email})


@pytest.mark.asyncio
async def test_full_admin_invitation_to_volunteer_password_flow():
    unique_id = str(ObjectId())
    admin_email = f"admin_vol_{unique_id}@eventora.com"
    volunteer_email = f"invited_volunteer_{unique_id}@eventora.com"

    # Clean up DB
    await user_collection.delete_many({"email": {"$in": [volunteer_email, admin_email]}})
    await admin_collection.delete_many({"email": admin_email})
    await otp_collection.delete_many({"email": volunteer_email})

    # Setup Super Admin
    await admin_collection.insert_one({
        "email": admin_email,
        "name": "Super Admin",
        "role": "super_admin"
    })
    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Admin adds Volunteer (this automatically triggers the invitation email)
        res_add = await client.post(
            "/api/admin/volunteers/",
            json={"email": volunteer_email},
            headers=admin_headers
        )
        assert res_add.status_code == 200
        assert "Volunteer added successfully" in res_add.json()["message"]

        # Step 2: Volunteer enters email on volunteer login page
        res_step1 = await client.post("/api/volunteer/auth/login/step1", json={
            "email": volunteer_email
        })
        assert res_step1.status_code == 200
        assert "OTP sent" in res_step1.json()["message"]

        # Retrieve generated OTP from DB
        otp_doc = await otp_collection.find_one({"email": volunteer_email})
        assert otp_doc is not None
        otp_code = otp_doc["otp"]

        # Step 3: Volunteer submits OTP -> first login
        res_step2 = await client.post("/api/volunteer/auth/login/step2", json={
            "email": volunteer_email,
            "otp": otp_code
        })
        assert res_step2.status_code == 200
        step2_data = res_step2.json()
        assert step2_data["has_password"] is False
        assert step2_data["role"] == "VOLUNTEER"
        vol_token = step2_data["access_token"]
        vol_headers = {"Authorization": f"Bearer {vol_token}"}

        # Step 4: Volunteer sets permanent password
        res_set_pw = await client.post(
            "/api/volunteer/auth/set-password",
            json={
                "password": "VolunteerPassword999!",
                "confirm_password": "VolunteerPassword999!"
            },
            headers=vol_headers
        )
        assert res_set_pw.status_code == 200
        assert res_set_pw.json()["has_password"] is True

        # Verify DB
        vol_db = await user_collection.find_one({"email": volunteer_email})
        assert vol_db["has_password"] is True
        assert vol_db["password_hash"] is not None

        # Step 5: Volunteer logs out and logs in next time with Email & Password (NO OTP NEEDED)
        # Verify invalid password rejected
        res_wrong_pw = await client.post("/api/volunteer/auth/login/password", json={
            "email": volunteer_email,
            "password": "WrongPassword!"
        })
        assert res_wrong_pw.status_code == 401

        # Verify correct Email + Password login succeeds
        res_pw_login = await client.post("/api/volunteer/auth/login/password", json={
            "email": volunteer_email,
            "password": "VolunteerPassword999!"
        })
        assert res_pw_login.status_code == 200
        login_res = res_pw_login.json()
        assert login_res["role"] == "VOLUNTEER"
        assert login_res["has_password"] is True
        assert "access_token" in login_res

    # Clean up
    await user_collection.delete_many({"email": volunteer_email})
    await admin_collection.delete_many({"email": admin_email})
    await otp_collection.delete_many({"email": volunteer_email})
