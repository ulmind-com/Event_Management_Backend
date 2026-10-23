import pytest
from bson import ObjectId
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone

from app.main import app
from app.core.database import (
    user_collection,
    otp_collection,
    admin_collection,
    issued_ticket_collection,
    booking_collection,
)
from app.core.security import create_access_token
from app.services.ticket import TicketService


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


@pytest.mark.asyncio
async def test_volunteer_gate_ticket_checkin():
    unique_id = str(ObjectId())
    volunteer_email = f"gate_scanner_{unique_id}@eventora.com"
    ticket_ref = f"TCK-{unique_id[:8]}"
    booking_ref = f"BKG-{unique_id[:8]}"

    # 1. Setup Volunteer with set password
    await user_collection.delete_many({"email": volunteer_email})
    await issued_ticket_collection.delete_many({"ticket_ref": ticket_ref})
    await booking_collection.delete_many({"booking_reference": booking_ref})

    from app.core.security import get_password_hash
    await user_collection.insert_one({
        "email": volunteer_email,
        "role": "VOLUNTEER",
        "registration_complete": True,
        "has_password": True,
        "password_hash": get_password_hash("ScannerPassword123!"),
        "created_at": datetime.now(timezone.utc),
    })

    # 2. Setup Issued Ticket & Booking in DB
    booking_res = await booking_collection.insert_one({
        "booking_reference": booking_ref,
        "booking_status": "CONFIRMED",
        "created_at": datetime.now(timezone.utc),
    })
    booking_id = str(booking_res.inserted_id)

    qr_token = TicketService.generate_qr_token(ticket_ref, booking_ref)
    await issued_ticket_collection.insert_one({
        "ticket_ref": ticket_ref,
        "booking_id": booking_id,
        "booking_reference": booking_ref,
        "holder_name": "Arijit Das",
        "holder_email": "attendee@test.com",
        "is_checked_in": False,
        "check_in_time": None,
        "qr_token": qr_token,
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 3. Volunteer logs in directly with Email + Password
        res_login = await client.post("/api/volunteer/auth/login/password", json={
            "email": volunteer_email,
            "password": "ScannerPassword123!"
        })
        assert res_login.status_code == 200
        vol_token = res_login.json()["access_token"]
        vol_headers = {"Authorization": f"Bearer {vol_token}"}

        # 4. Verify QR ticket through /volunteer/tickets/verify
        res_verify = await client.post(
            "/api/volunteer/tickets/verify",
            json={"qr_token": qr_token},
            headers=vol_headers
        )
        assert res_verify.status_code == 200
        verify_data = res_verify.json()
        assert verify_data["is_valid"] is True
        assert verify_data["ticket"]["ticket_ref"] == ticket_ref
        assert verify_data["ticket"]["holder_name"] == "Arijit Das"
        assert verify_data["ticket"]["is_checked_in"] is False

        # 5. Check in the ticket through /volunteer/tickets/check-in
        res_checkin = await client.post(
            "/api/volunteer/tickets/check-in",
            json={"qr_token": qr_token},
            headers=vol_headers
        )
        assert res_checkin.status_code == 200
        assert res_checkin.json()["ticket_ref"] == ticket_ref
        assert "Successfully checked in" in res_checkin.json()["message"]

        # Verify DB updated
        t_db = await issued_ticket_collection.find_one({"ticket_ref": ticket_ref})
        assert t_db["is_checked_in"] is True
        assert t_db["scanned_by"] == volunteer_email
        assert t_db["check_in_time"] is not None

        # 6. Duplicate check-in must be rejected
        res_dup = await client.post(
            "/api/volunteer/tickets/check-in",
            json={"qr_token": qr_token},
            headers=vol_headers
        )
        assert res_dup.status_code == 400
        assert "already been checked in" in res_dup.json()["detail"]

        # 7. Also verify via /admin/tickets/check-in endpoint
        res_admin_alias = await client.post(
            "/api/admin/tickets/check-in",
            json={"qr_token": qr_token},
            headers=vol_headers
        )
        assert res_admin_alias.status_code == 400  # already checked in

        # 8. Regular USER token cannot access scanner endpoints
        user_token = create_access_token({"sub": "random_user@test.com", "type": "user"})
        res_unauthorized = await client.post(
            "/api/volunteer/tickets/verify",
            json={"qr_token": qr_token},
            headers={"Authorization": f"Bearer {user_token}"}
        )
        assert res_unauthorized.status_code == 403

    # Clean up
    await user_collection.delete_many({"email": volunteer_email})
    await issued_ticket_collection.delete_many({"ticket_ref": ticket_ref})
    await booking_collection.delete_many({"booking_reference": booking_ref})

