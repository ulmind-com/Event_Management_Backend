import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from app.main import app
from app.core.database import user_collection
from app.services.firebase_auth_service import FirebaseAuthService

@pytest.mark.asyncio
async def test_google_login_new_user_success():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        test_email = "googlenewuser@example.com"
        await user_collection.delete_one({"email": test_email})

        mock_payload = {
            "email": test_email,
            "name": "Google New User",
            "picture": "https://example.com/photo.jpg",
            "sub": "firebase_uid_123",
            "firebase_uid": "firebase_uid_123"
        }

        with patch.object(FirebaseAuthService, "verify_firebase_id_token", return_value=mock_payload):
            res = await ac.post("/api/auth/google-login", json={"id_token": "valid_mock_token"})
            assert res.status_code == 200
            data = res.json()
            assert "access_token" in data
            assert data["role"] == "USER"
            assert data["user_email"] == test_email

            # Check DB
            user = await user_collection.find_one({"email": test_email})
            assert user is not None
            assert user["role"] == "USER"
            assert user["auth_provider"] == "GOOGLE"

@pytest.mark.asyncio
async def test_google_login_existing_user_success():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        test_email = "existinguser@example.com"
        await user_collection.update_one(
            {"email": test_email},
            {"$set": {"role": "USER", "name": "Existing User", "registration_complete": True}},
            upsert=True
        )

        mock_payload = {
            "email": test_email,
            "name": "Existing User",
            "picture": None,
            "sub": "firebase_uid_456",
            "firebase_uid": "firebase_uid_456"
        }

        with patch.object(FirebaseAuthService, "verify_firebase_id_token", return_value=mock_payload):
            res = await ac.post("/api/auth/google-login", json={"id_token": "valid_mock_token"})
            assert res.status_code == 200
            data = res.json()
            assert "access_token" in data
            assert data["role"] == "USER"

@pytest.mark.asyncio
async def test_google_login_admin_role_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        admin_email = "adminuser@example.com"
        await user_collection.update_one(
            {"email": admin_email},
            {"$set": {"role": "ADMIN", "name": "Admin User", "registration_complete": True}},
            upsert=True
        )

        mock_payload = {
            "email": admin_email,
            "name": "Admin User",
            "picture": None,
            "sub": "firebase_uid_admin",
            "firebase_uid": "firebase_uid_admin"
        }

        with patch.object(FirebaseAuthService, "verify_firebase_id_token", return_value=mock_payload):
            res = await ac.post("/api/auth/google-login", json={"id_token": "valid_mock_token"})
            assert res.status_code == 403
            assert "only permitted for regular User accounts" in res.json()["detail"]

@pytest.mark.asyncio
async def test_google_login_invalid_token_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post("/api/auth/google-login", json={"id_token": "definitely_invalid_token"})
        assert res.status_code in [401, 503]
