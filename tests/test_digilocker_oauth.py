"""
Comprehensive Test Suite for DigiLocker Official OAuth 2.0 Integration & Zero-File Registration
Covers:
  - PKCE S256 Cryptographic Generation & Verification
  - GET /api/digilocker/authorize (302 Redirect & JSON Mode)
  - GET /api/digilocker/callback (CSRF State Validation, Error Handling, Verification)
  - GET /api/digilocker/status (Status query, Masked Identity)
  - POST /api/digilocker/revoke (Revocation & Status Reset)
  - POST /api/auth/register/step3 (Zero manual govt_id_proof file upload)
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.database import user_collection, kyc_verification_collection
from app.core.security import create_access_token
from app.services.digilocker_oauth import DigiLockerOAuthService


@pytest.fixture(autouse=True)
async def cleanup_test_data():
    """Ensure test users are cleaned up before and after each test."""
    test_emails = [
        "oauth_tester@example.com",
        "step3_clean_user@example.com",
        "revoke_tester@example.com",
    ]
    await user_collection.delete_many({"email": {"$in": test_emails}})
    await kyc_verification_collection.delete_many({"user_email": {"$in": test_emails}})
    yield
    await user_collection.delete_many({"email": {"$in": test_emails}})
    await kyc_verification_collection.delete_many({"user_email": {"$in": test_emails}})


@pytest.mark.asyncio
async def test_pkce_cryptographic_generation():
    """Validates PKCE code_verifier and code_challenge (RFC 7636)."""
    verifier = DigiLockerOAuthService.generate_code_verifier()
    assert len(verifier) >= 43
    assert len(verifier) <= 128

    challenge = DigiLockerOAuthService.generate_code_challenge(verifier)
    assert "=" not in challenge  # Base64URL without padding
    assert len(challenge) > 20

    # Ensure deterministic challenge for same verifier
    challenge2 = DigiLockerOAuthService.generate_code_challenge(verifier)
    assert challenge == challenge2


@pytest.mark.asyncio
async def test_digilocker_authorize_endpoint():
    """Validates GET /api/digilocker/authorize endpoint behavior."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Test 302 Redirect mode
        res_redirect = await client.get(
            "/api/digilocker/authorize",
            params={"user_email": "oauth_tester@example.com"},
            follow_redirects=False,
        )
        assert res_redirect.status_code == 302
        loc = res_redirect.headers.get("location")
        assert loc is not None
        assert "response_type=code" in loc
        assert "code_challenge=" in loc
        assert "code_challenge_method=S256" in loc
        assert "state=" in loc
        assert "scope=" in loc

        # 2. Test JSON format mode (for Single Page Apps / Mobile Apps)
        res_json = await client.get(
            "/api/digilocker/authorize",
            params={"user_email": "oauth_tester@example.com", "format": "json"},
        )
        assert res_json.status_code == 200
        data = res_json.json()
        assert data.get("success") is True
        assert "authorization_url" in data
        assert "state" in data
        assert "redirect_uri" in data


@pytest.mark.asyncio
async def test_digilocker_callback_error_handling():
    """Validates callback error scenarios: access_denied, missing params, invalid state."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Scenario 1: User denies consent on DigiLocker
        res_denied = await client.get(
            "/api/digilocker/callback",
            params={"error": "access_denied", "error_description": "User cancelled authentication", "format": "json"},
        )
        assert res_denied.status_code == 400
        data_denied = res_denied.json()
        assert data_denied.get("success") is False
        assert data_denied.get("error") == "access_denied"

        # Scenario 2: Missing code or state
        res_missing = await client.get("/api/digilocker/callback", params={"format": "json"})
        assert res_missing.status_code == 400
        assert res_missing.json().get("error") == "invalid_request"

        # Scenario 3: Invalid / Forged CSRF state token
        res_csrf = await client.get(
            "/api/digilocker/callback",
            params={"code": "sample_auth_code", "state": "forged_non_existent_state", "format": "json"},
        )
        assert res_csrf.status_code == 400
        assert res_csrf.json().get("error") == "invalid_state"


@pytest.mark.asyncio
async def test_digilocker_callback_success_and_status():
    """Validates complete callback flow, database KYC update, and status query."""
    user_email = "oauth_tester@example.com"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Initiate session to obtain valid state
        auth_res = await client.get(
            "/api/digilocker/authorize",
            params={"user_email": user_email, "format": "json"},
        )
        valid_state = auth_res.json()["state"]

        # 2. Callback with valid state and code
        callback_res = await client.get(
            "/api/digilocker/callback",
            params={"code": "test_auth_code_123", "state": valid_state, "format": "json"},
        )
        assert callback_res.status_code == 200
        cb_data = callback_res.json()
        assert cb_data.get("success") is True
        assert cb_data.get("kyc_status") == "VERIFIED"
        assert cb_data.get("aadhaar_masked").startswith("XXXX-XXXX-")
        assert "digilocker://" in cb_data.get("doc_uri")

        # 3. Query status endpoint
        status_res = await client.get("/api/digilocker/status", params={"user_email": user_email})
        assert status_res.status_code == 200
        st_data = status_res.json()
        assert st_data.get("kyc_status") == "VERIFIED"
        assert st_data.get("verified") is True
        assert st_data.get("verification_source") == "DIGILOCKER_OAUTH"
        assert "access_token" not in st_data
        assert "client_secret" not in st_data


@pytest.mark.asyncio
async def test_digilocker_revoke_endpoint():
    """Validates POST /api/digilocker/revoke resets KYC status."""
    user_email = "revoke_tester@example.com"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Setup initial verified state
        auth_res = await client.get("/api/digilocker/authorize", params={"user_email": user_email, "format": "json"})
        state = auth_res.json()["state"]
        await client.get("/api/digilocker/callback", params={"code": "code_xyz", "state": state, "format": "json"})

        # Confirm verified
        st_before = await client.get("/api/digilocker/status", params={"user_email": user_email})
        assert st_before.json().get("kyc_status") == "VERIFIED"

        # Revoke
        rev_res = await client.post("/api/digilocker/revoke", json={"user_email": user_email})
        assert rev_res.status_code == 200
        assert rev_res.json().get("success") is True

        # Confirm reset
        st_after = await client.get("/api/digilocker/status", params={"user_email": user_email})
        assert st_after.json().get("kyc_status") == "NOT_VERIFIED"
        assert st_after.json().get("verified") is False


@pytest.mark.asyncio
async def test_register_step3_zero_file_upload_success():
    """
    Validates user registration Step 3:
    - No govt_id_proof file upload is required.
    - User can register profile cleanly.
    - KYC remains NOT_VERIFIED until completed via DigiLocker.
    """
    user_email = "step3_clean_user@example.com"
    token = create_access_token(data={"sub": user_email, "type": "registration"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        form_data = {
            "password": "StrongPassword@123",
            "confirm_password": "StrongPassword@123",
            "name": "Clean Test User",
            "phone_number": "9876543210",
            "date_of_birth": "2001-05-15",
            "address": "Kolkata, West Bengal, 700001",
        }

        # POST without any govt_id_proof file!
        res = await client.post(
            "/api/auth/register/step3",
            data=form_data,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("message") == "Registration complete"
        assert "access_token" in data
        assert data["user"]["registration_complete"] is True
        assert data["user"]["kyc_status"] == "NOT_VERIFIED"
