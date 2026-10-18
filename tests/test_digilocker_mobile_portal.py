import pytest
import secrets
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.security import create_access_token
from app.main import app

@pytest.mark.asyncio
async def test_digilocker_mobile_portal_flow():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db

    user1_email = "digi_tester1@eventora.com"
    user2_email = "digi_tester2@eventora.com"

    # Clean up test accounts
    await db["users"].delete_many({"email": {"$in": [user1_email, user2_email]}})
    await db["kyc_verifications"].delete_many({"user_email": {"$in": [user1_email, user2_email]}})

    # Insert user1
    await db["users"].insert_one({
        "email": user1_email,
        "name": "Sagnik Mondal",
        "date_of_birth": "2000-01-01",
        "role": "USER",
        "registration_complete": True,
        "kyc_status": "NOT_SUBMITTED"
    })
    token1 = create_access_token({"sub": user1_email, "type": "user"})
    headers1 = {"Authorization": f"Bearer {token1}"}

    # Insert user2
    await db["users"].insert_one({
        "email": user2_email,
        "name": "Another Citizen",
        "date_of_birth": "1998-05-15",
        "role": "USER",
        "registration_complete": True,
        "kyc_status": "NOT_SUBMITTED"
    })
    token2 = create_access_token({"sub": user2_email, "type": "user"})
    headers2 = {"Authorization": f"Bearer {token2}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 0. Test GET /api/kyc/digilocker/authorize redirects directly (302) to official DigiLocker portal
        auth_redirect_res = await ac.get(f"/api/kyc/digilocker/authorize?email={user1_email}", follow_redirects=False)
        assert auth_redirect_res.status_code == 302
        assert "location" in auth_redirect_res.headers
        auth_target = auth_redirect_res.headers["location"]
        assert auth_target.startswith("https://api.digitallocker.gov.in/public/oauth2/1/authorize")
        assert "response_type=code" in auth_target
        assert "scope=eaadhaar" in auth_target

        # Test GET /api/kyc/digilocker/authorize with redirect=false returns JSON auth_url
        auth_json_res = await ac.get(f"/api/kyc/digilocker/authorize?email={user1_email}&redirect=false")
        assert auth_json_res.status_code == 200
        auth_json_data = auth_json_res.json()
        assert auth_json_data["auth_url"].startswith("https://api.digitallocker.gov.in/public/oauth2/1/authorize")

        # 1. Test GET /api/kyc/digilocker/portal renders the official HTML page
        portal_res = await ac.get("/api/kyc/digilocker/portal?email=" + user1_email + "&state=test_state_123")
        assert portal_res.status_code == 200
        assert "text/html" in portal_res.headers.get("content-type", "")
        html_text = portal_res.text
        assert "DigiLocker" in html_text
        assert "Mobile Number" in html_text
        assert "Sign In with Mobile" in html_text
        assert "DigiLocker Document Consent" in html_text

        test_mob1 = f"9{secrets.randbelow(900000000) + 100000000}"
        test_mob2 = f"8{secrets.randbelow(900000000) + 100000000}"

        # 2. Portal Step 1: Send Mobile OTP from Portal
        send_res = await ac.post("/api/kyc/digilocker/portal/send-otp", json={
            "mobile_number": test_mob1,
            "user_email": user1_email
        })
        assert send_res.status_code == 200, f"send_res failed: {send_res.text}"
        send_data = send_res.json()
        assert send_data["success"] is True
        ref_id = send_data["reference_id"]
        assert "+91" in send_data["masked_mobile"]

        # 3. Portal Step 2 & 3: Verify OTP & PIN and grant Consent
        verify_res = await ac.post("/api/kyc/digilocker/portal/verify-otp", json={
            "reference_id": ref_id,
            "otp": "123456",
            "security_pin": "123456",
            "state": "test_state_123",
            "user_email": user1_email
        })
        assert verify_res.status_code == 200, f"verify_res failed: {verify_res.text}"
        verify_data = verify_res.json()
        assert verify_data["success"] is True
        code = verify_data["code"]
        assert "dl_code_" in code
        assert "/api/kyc/digilocker/callback" in verify_data["redirect_url"]

        # 4. Portal Step 4: Follow redirect to /api/kyc/digilocker/callback
        # Test browser HTML callback
        callback_res = await ac.get(
            f"/api/kyc/digilocker/callback?code={code}&state=test_state_123",
            headers={"Accept": "text/html"}
        )
        assert callback_res.status_code == 200
        assert "text/html" in callback_res.headers.get("content-type", "")
        assert "Aadhaar Verified via DigiLocker!" in callback_res.text

        # 5. Verify database records for user1
        user1_doc = await db["users"].find_one({"email": user1_email})
        assert user1_doc["kyc_status"] == "VERIFIED"
        assert user1_doc["kyc_source"] == "DIGILOCKER"
        assert "XXXX" in user1_doc["kyc_aadhaar_masked"]

        kyc_record = await db["kyc_verifications"].find_one({"user_email": user1_email})
        assert kyc_record["status"] == "VERIFIED"
        assert kyc_record["verhoeff_valid"] is True

        # 6. Check KYC status endpoint
        status_res = await ac.get("/api/kyc/status", headers=headers1)
        assert status_res.status_code == 200
        assert status_res.json()["is_verified"] is True
        assert status_res.json()["source"] == "DIGILOCKER"

        # 7. Test Anti-Duplicate Detection: User2 tries with the same mobile number / Aadhaar
        dup_send_res = await ac.post("/api/kyc/digilocker/portal/send-otp", json={
            "mobile_number": test_mob1,
            "user_email": user2_email
        })
        assert dup_send_res.status_code == 409
        assert "already registered" in dup_send_res.json()["detail"]

        # 8. Test Direct Native Mobile API for App / SPA
        # User2 with a different unique mobile number
        mob_api_send = await ac.post("/api/kyc/digilocker/mobile/send-otp", json={
            "mobile_number": test_mob2
        }, headers=headers2)
        assert mob_api_send.status_code == 200
        mob_ref_id = mob_api_send.json()["reference_id"]

        mob_api_verify = await ac.post("/api/kyc/digilocker/mobile/verify-otp", json={
            "reference_id": mob_ref_id,
            "otp": "123456",
            "security_pin": "123456"
        }, headers=headers2)
        assert mob_api_verify.status_code == 200
        assert mob_api_verify.json()["kyc_status"] == "VERIFIED"

        user2_doc = await db["users"].find_one({"email": user2_email})
        assert user2_doc["kyc_status"] == "VERIFIED"

        # Clean up
        await db["users"].delete_many({"email": {"$in": [user1_email, user2_email]}})
        await db["kyc_verifications"].delete_many({"user_email": {"$in": [user1_email, user2_email]}})

