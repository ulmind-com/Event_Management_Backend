import pytest
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone, timedelta
from bson import ObjectId
import jwt

from app.main import app
from app.core.config import settings
from app.core.database import db
from app.core.security import create_access_token

@pytest.mark.asyncio
async def test_competition_registration_edge_cases():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Create test user
        user_email = "comp_edge_test_user@example.com"
        await db["users"].delete_many({"email": user_email})
        await db["users"].insert_one({
            "email": user_email,
            "name": "Edge Test User",
            "phone_number": "9876543210",
            "address": "123 Edge Lane",
            "date_of_birth": "2000-01-15T00:00:00.000Z",  # ISO format string
            "registration_complete": True,
            "is_profile_completed": True,
            "profile_completed": True,
            "created_at": datetime.now(timezone.utc)
        })

        user_token = create_access_token({"sub": user_email})
        user_headers = {"Authorization": f"Bearer {user_token}"}

        # 1. TEST INVALID OBJECT ID (Should return 400 Bad Request, NOT 500 crash)
        r_invalid_id = await ac.get("/api/v1/competitions/not-a-valid-id/eligibility", headers=user_headers)
        assert r_invalid_id.status_code == 400
        assert "Invalid competition ID" in r_invalid_id.json()["detail"]

        r_invalid_reg = await ac.post("/api/v1/competitions/not-a-valid-id/register", headers=user_headers)
        assert r_invalid_reg.status_code == 400

        # 2. CREATE A FREE COMPETITION WITH participation_fee = None (Explicit None in DB)
        comp_doc = {
            "title": "Edge Case None Fee Competition",
            "description": "Testing None fee handling",
            "is_published": True,
            "participation_fee": None,  # EXPLICIT NONE
            "min_age": 10,
            "max_age": 50,
            "start_date": datetime.now(timezone.utc) - timedelta(days=1),
            "end_date": datetime.now(timezone.utc) + timedelta(days=30),
            "registration_start_date": datetime.now(timezone.utc) - timedelta(days=2),
            "registration_deadline": datetime.now(timezone.utc) + timedelta(days=10),
            "submission_deadline": datetime.now(timezone.utc) + timedelta(days=15),
            "dynamic_submission_fields": []
        }
        comp_res = await db["competitions"].insert_one(comp_doc)
        comp_id = str(comp_res.inserted_id)

        # 3. TEST ELIGIBILITY WITH ISO DOB STRING
        r_el = await ac.get(f"/api/v1/competitions/{comp_id}/eligibility", headers=user_headers)
        assert r_el.status_code == 200
        assert r_el.json()["eligible"] is True

        # 4. TEST RULES NOT ACCEPTED
        r_no_rules = await ac.post(f"/api/v1/competitions/{comp_id}/register", headers=user_headers)
        assert r_no_rules.status_code == 400
        assert "must accept the competition rules" in r_no_rules.json()["detail"]

        # 5. TEST REGISTRATION WITH participation_fee = None via Query Param
        r_reg_query = await ac.post(
            f"/api/v1/competitions/{comp_id}/register?rules_accepted=true",
            headers=user_headers
        )
        assert r_reg_query.status_code == 200
        assert r_reg_query.json()["status"] == "CONFIRMED"

        # 6. TEST ALREADY REGISTERED CONFLICT
        r_dup = await ac.post(
            f"/api/v1/competitions/{comp_id}/register?rules_accepted=true",
            headers=user_headers
        )
        assert r_dup.status_code == 409
        assert "Already registered and confirmed" in r_dup.json()["detail"]

        # 7. TEST MY REGISTRATION STATUS
        r_status = await ac.get(f"/api/v1/competitions/{comp_id}/my-registration", headers=user_headers)
        assert r_status.status_code == 200
        st_data = r_status.json()
        assert st_data["is_registered"] is True
        assert st_data["is_confirmed"] is True

        # Clean up
        await db["competitions"].delete_one({"_id": ObjectId(comp_id)})
        await db["competition_registrations"].delete_many({"competition_id": comp_id})
        await db["users"].delete_many({"email": user_email})
