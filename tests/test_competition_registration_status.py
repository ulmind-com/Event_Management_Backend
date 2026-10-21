import pytest
from datetime import datetime, timezone
from bson import ObjectId
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.database import (
    competition_collection,
    competition_registration_collection,
    user_collection,
)
from app.core.security import create_access_token


@pytest.mark.asyncio
async def test_competition_registration_status_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        test_email = f"status_test_{int(datetime.now().timestamp())}@example.com"
        token = create_access_token(data={"sub": test_email, "type": "user"})
        headers = {"Authorization": f"Bearer {token}"}

        await user_collection.update_one(
            {"email": test_email},
            {"$set": {
                "email": test_email,
                "role": "attendee",
                "full_name": "Status Tester",
                "date_of_birth": "2000-01-01",
                "registration_complete": True
            }},
            upsert=True
        )

        # 1. Create a competition
        comp_doc = {
            "title": "Status Test Solo Dance",
            "name": "Status Test Solo Dance",
            "category": "dance",
            "participation_fee": 199.0,
            "status": "PUBLISHED",
            "is_published": True,
            "registered_count": 0,
            "min_age": 10,
            "max_age": 50,
            "created_at": datetime.now(timezone.utc)
        }
        res_comp = await competition_collection.insert_one(comp_doc)
        comp_id = str(res_comp.inserted_id)

        # 2. User just views/checks status WITHOUT registering
        status_res = await client.get(
            f"/api/v1/competitions/{comp_id}/my-registration",
            headers=headers
        )
        assert status_res.status_code == 200
        status_data = status_res.json()
        assert status_data["is_registered"] is False
        assert status_data["is_confirmed"] is False
        assert status_data["status"] == "NOT_REGISTERED"

        # 3. User initiates registration (simulating opening payment modal), but doesn't complete payment
        reg_init_res = await client.post(
            f"/api/v1/competitions/{comp_id}/register?rules_accepted=true",
            headers=headers
        )
        assert reg_init_res.status_code == 200
        assert reg_init_res.json()["status"] == "PENDING"

        # 4. User navigates back and opens the competition page again
        status_res_after_back = await client.get(
            f"/api/v1/competitions/{comp_id}/my-registration",
            headers=headers
        )
        assert status_res_after_back.status_code == 200
        data_after_back = status_res_after_back.json()
        assert data_after_back["is_registered"] is True
        assert data_after_back["is_confirmed"] is False  # NOT confirmed! Cannot submit yet
        assert data_after_back["status"] == "PENDING"

        # 5. User can re-call register to get payment order again without being blocked
        retry_reg = await client.post(
            f"/api/v1/competitions/{comp_id}/register?rules_accepted=true",
            headers=headers
        )
        assert retry_reg.status_code == 200
        assert retry_reg.json()["status"] == "PENDING"
        assert "payment_info" in retry_reg.json()

        # 6. Simulate payment confirmation
        await competition_registration_collection.update_one(
            {"competition_id": comp_id, "user_email": test_email},
            {"$set": {"status": "CONFIRMED", "payment_status": "PAID"}}
        )

        # 7. Check status after confirmation
        confirmed_status = await client.get(
            f"/api/v1/competitions/{comp_id}/my-registration",
            headers=headers
        )
        assert confirmed_status.status_code == 200
        conf_data = confirmed_status.json()
        assert conf_data["is_registered"] is True
        assert conf_data["is_confirmed"] is True
        assert conf_data["status"] == "CONFIRMED"

        # Cleanup
        await competition_collection.delete_one({"_id": ObjectId(comp_id)})
        await competition_registration_collection.delete_many({"competition_id": comp_id})
        await user_collection.delete_one({"email": test_email})
