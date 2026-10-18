import pytest
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.security import create_access_token
from app.main import app

@pytest.mark.asyncio
async def test_competition_age_limit_create_and_update():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db

    admin_token = create_access_token({"sub": "admin@eventora.com", "type": "admin"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Clean up previous test competitions
    await db["competitions"].delete_many({"title": "Age Limit Test Competition"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Create Competition with initial age limit: min 12, max 18
        create_payload = {
            "title": "Age Limit Test Competition",
            "description": "Testing updating of age limits",
            "category": "Singing",
            "type": "General",
            "start_date": "2026-11-01T00:00:00Z",
            "end_date": "2026-11-10T00:00:00Z",
            "registration_start_date": "2026-10-01T00:00:00Z",
            "registration_deadline": "2026-10-30T00:00:00Z",
            "submission_deadline": "2026-11-05T00:00:00Z",
            "min_age": 12,
            "max_age": 18,
            "audience_voting_enabled": True,
            "audience_vote_weight": 1.0,
            "is_published": True
        }

        res_create = await ac.post("/api/admin/advanced-competitions/", json=create_payload, headers=admin_headers)
        assert res_create.status_code == 200, f"Create failed: {res_create.text}"
        comp = res_create.json()
        comp_id = comp["_id"]
        assert comp["min_age"] == 12
        assert comp["max_age"] == 18

        # 2. Fetch single competition for Admin and check age limit
        res_get_admin = await ac.get(f"/api/admin/advanced-competitions/{comp_id}", headers=admin_headers)
        assert res_get_admin.status_code == 200
        admin_data = res_get_admin.json()
        assert admin_data["min_age"] == 12
        assert admin_data["max_age"] == 18

        # 3. Edit competition via PATCH to update age limit: min 16, max 26
        patch_payload = {
            "min_age": 16,
            "max_age": 26
        }
        res_patch = await ac.patch(f"/api/admin/advanced-competitions/{comp_id}", json=patch_payload, headers=admin_headers)
        assert res_patch.status_code == 200, f"Patch failed: {res_patch.text}"
        patch_data = res_patch.json()
        assert patch_data["competition"]["min_age"] == 16
        assert patch_data["competition"]["max_age"] == 26

        # 4. Fetch again via Admin GET single
        res_get_admin2 = await ac.get(f"/api/admin/advanced-competitions/{comp_id}", headers=admin_headers)
        assert res_get_admin2.status_code == 200
        updated_data = res_get_admin2.json()
        assert updated_data["min_age"] == 16
        assert updated_data["max_age"] == 26

        # 5. Fetch via public v1 competition endpoint
        res_get_v1 = await ac.get(f"/api/v1/competitions/{comp_id}")
        assert res_get_v1.status_code == 200
        v1_data = res_get_v1.json()
        assert v1_data["min_age"] == 16
        assert v1_data["max_age"] == 26

        # 6. Fetch via activities competition endpoint
        res_get_act = await ac.get(f"/api/activities/competitions/{comp_id}")
        assert res_get_act.status_code == 200
        act_data = res_get_act.json()
        assert act_data["min_age"] == 16
        assert act_data["max_age"] == 26

        # 7. Also verify PUT endpoint works for saving edits
        put_payload = {
            "min_age": 20,
            "max_age": 35
        }
        res_put = await ac.put(f"/api/admin/advanced-competitions/{comp_id}", json=put_payload, headers=admin_headers)
        assert res_put.status_code == 200
        put_data = res_put.json()
        assert put_data["competition"]["min_age"] == 20
        assert put_data["competition"]["max_age"] == 35

        # 8. Fetch again to confirm PUT saved min_age 20 and max_age 35
        res_get_final = await ac.get(f"/api/admin/advanced-competitions/{comp_id}", headers=admin_headers)
        assert res_get_final.status_code == 200
        final_data = res_get_final.json()
        assert final_data["min_age"] == 20
        assert final_data["max_age"] == 35

        # 9. Verify invalid age limit (min > max) returns 400
        invalid_payload = {
            "min_age": 40,
            "max_age": 20
        }
        res_invalid = await ac.patch(f"/api/admin/advanced-competitions/{comp_id}", json=invalid_payload, headers=admin_headers)
        assert res_invalid.status_code == 400

        # 10. Verify eligibility check with user DOB reflects the edited limits
        # Create user with age ~22 (born 2004-01-01)
        test_user_email = "age_tester@eventora.com"
        await db["users"].delete_many({"email": test_user_email})
        await db["users"].insert_one({
            "email": test_user_email,
            "name": "Age Test User",
            "date_of_birth": "2004-01-01",
            "role": "user",
            "registration_complete": True
        })
        user_token = create_access_token({"sub": test_user_email, "type": "user"})
        user_headers = {"Authorization": f"Bearer {user_token}"}

        # Currently min_age is 20, max_age is 35 -> User is ~22, should be ELIGIBLE
        res_elig1 = await ac.get(f"/api/v1/competitions/{comp_id}/eligibility", headers=user_headers)
        assert res_elig1.status_code == 200
        assert res_elig1.json()["eligible"] is True

        # Now edit competition: min_age 25, max_age 35 -> User (age ~22) should now be NOT_ELIGIBLE!
        res_patch_restrict = await ac.patch(
            f"/api/admin/advanced-competitions/{comp_id}",
            json={"min_age": 25, "max_age": 35},
            headers=admin_headers
        )
        assert res_patch_restrict.status_code == 200

        res_elig2 = await ac.get(f"/api/v1/competitions/{comp_id}/eligibility", headers=user_headers)
        assert res_elig2.status_code == 200
        assert res_elig2.json()["eligible"] is False
        assert "Minimum age requirement is 25" in res_elig2.json()["reason"]

        # Clean up
        await db["users"].delete_many({"email": test_user_email})
        await db["competitions"].delete_one({"_id": comp["_id"]})
