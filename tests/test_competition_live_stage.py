import pytest
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from datetime import datetime, timezone

from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from app.core.database import (
    competition_collection,
    competition_registration_collection,
    competition_submission_collection,
    judge_assignment_collection,
    judge_score_collection,
    competition_stage_collection,
    user_collection,
    admin_collection,
)
from app.main import app


@pytest.mark.asyncio
async def test_live_stage_end_to_end_workflow():
    """
    End-to-End Test for Competition Live Stage Performance:
    1. Admin creates competition with criteria.
    2. Register 3 participants into the stage queue.
    3. Admin checks stage dashboard (/stage) and sets performer 1 on stage.
    4. Auto-submission is verified.
    5. Public live stage endpoints verify stage status.
    6. Judge checks live stage card and submits marks directly (/live-stage/score).
    7. Single scoring enforcement verified.
    8. Admin advances to next performer (performer 1 completed, performer 2 on stage).
    9. Admin ends/clears performance to IDLE.
    10. Enriched contestants list verified.
    """
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db

    admin_email = "admin_stage_test@eventora.com"
    judge_email = "judge_stage_test@eventora.com"
    p1_email = "p1_stage@test.com"
    p2_email = "p2_stage@test.com"
    p3_email = "p3_stage@test.com"

    # Clean up test collections
    test_emails = [admin_email, judge_email, p1_email, p2_email, p3_email]
    await admin_collection.delete_many({"email": admin_email})
    await user_collection.delete_many({"email": {"$in": test_emails}})
    await competition_collection.delete_many({"title": "Live Stage Championship 2026"})

    # Setup admin
    await admin_collection.insert_one({
        "email": admin_email,
        "password_hash": get_password_hash("adminpass123"),
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc),
    })

    # Setup judge user with complete profile
    await user_collection.insert_one({
        "email": judge_email,
        "name": "Live Stage Judge",
        "full_name": "Live Stage Judge",
        "phone_number": "9876543210",
        "date_of_birth": "1988-05-15",
        "address": "456 Judge Boulevard, Kolkata",
        "parent_phone_number": "9876543211",
        "role": "JUDGE",
        "status": "ACTIVE",
        "is_verified": True,
        "registration_complete": True,
        "created_at": datetime.now(timezone.utc),
    })


    # Setup participant users with complete profiles
    for i, em in enumerate([p1_email, p2_email, p3_email], 1):
        await user_collection.insert_one({
            "email": em,
            "name": f"Contestant {i}",
            "full_name": f"Contestant {i} Full",
            "phone_number": f"910000000{i}",
            "date_of_birth": "2000-01-01",
            "address": f"Stage Street {i}",
            "role": "USER",
            "status": "ACTIVE",
            "is_verified": True,
            "registration_complete": True,
            "profile_photo": f"https://example.com/photos/c{i}.jpg",
            "created_at": datetime.now(timezone.utc),
        })

    admin_token = create_access_token({"sub": admin_email, "type": "admin", "role": "super_admin"})
    judge_token = create_access_token({"sub": judge_email, "type": "user"})

    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    judge_headers = {"Authorization": f"Bearer {judge_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # ── 1. Create Competition ──
        comp_payload = {
            "title": "Live Stage Championship 2026",
            "description": "Live onstage performance contest",
            "category": "Music & Dance",
            "start_date": "2026-10-01T00:00:00Z",
            "end_date": "2026-12-31T23:59:59Z",
            "registration_start_date": "2026-10-01T00:00:00Z",
            "registration_deadline": "2026-11-30T23:59:59Z",
            "submission_deadline": "2026-12-01T23:59:59Z",
            "is_published": True,
            "judge_scoring_enabled": True,
            "audience_voting_enabled": False,
            "judging_status": "OPEN",
            "judging_criteria": [
                {"name": "Stage Presence", "max_score": 10.0, "weight": 1.0},
                {"name": "Skill & Technique", "max_score": 10.0, "weight": 1.0}
            ]
        }
        create_res = await ac.post("/api/admin/advanced-competitions/", json=comp_payload, headers=admin_headers)
        assert create_res.status_code == 200, f"Create competition failed: {create_res.text}"
        comp_id = create_res.json()["_id"]

        # Clean any prior stage documents for this comp
        await competition_stage_collection.delete_many({"competition_id": str(comp_id)})
        await judge_score_collection.delete_many({"competition_id": str(comp_id)})
        await judge_assignment_collection.delete_many({"competition_id": str(comp_id)})

        # ── 2. Register Participants in Registration Collection ──
        p1_reg = {
            "competition_id": comp_id,
            "user_email": p1_email,
            "participant_name": "Contestant 1 Full",
            "user_phone": "9100000001",
            "registration_reference": "CRG-STAGE-001",
            "registration_number": "CRG-STAGE-001",
            "entry_title": "Classical Fusion Dance",
            "status": "CONFIRMED",
            "created_at": datetime.now(timezone.utc)
        }
        p2_reg = {
            "competition_id": comp_id,
            "user_email": p2_email,
            "participant_name": "Contestant 2 Full",
            "user_phone": "9100000002",
            "registration_reference": "CRG-STAGE-002",
            "registration_number": "CRG-STAGE-002",
            "entry_title": "Acoustic Solo Guitar",
            "status": "CONFIRMED",
            "created_at": datetime.now(timezone.utc)
        }
        p3_reg = {
            "competition_id": comp_id,
            "user_email": p3_email,
            "participant_name": "Contestant 3 Full",
            "user_phone": "9100000003",
            "registration_reference": "CRG-STAGE-003",
            "registration_number": "CRG-STAGE-003",
            "entry_title": "Beatboxing Showcase",
            "status": "CONFIRMED",
            "created_at": datetime.now(timezone.utc)
        }
        await competition_registration_collection.insert_many([p1_reg, p2_reg, p3_reg])

        # ── 3. Assign Judge to Competition ──
        assign_res = await ac.post("/api/admin/judges/assignments", json={
            "judge_email": judge_email,
            "competition_id": comp_id
        }, headers=admin_headers)
        assert assign_res.status_code == 200, f"Judge assignment failed: {assign_res.text}"

        # ── 4. Admin Stage Dashboard View ──
        stage_dash = await ac.get(f"/api/admin/advanced-competitions/{comp_id}/stage", headers=admin_headers)
        assert stage_dash.status_code == 200
        dash_data = stage_dash.json()
        assert dash_data["stage_status"] == "IDLE"
        assert dash_data["total_participants"] == 3
        assert dash_data["completed_count"] == 0
        assert dash_data["current_performer"] is None
        assert len(dash_data["participants"]) == 3

        # ── 5. Admin Sets Performer 1 on Stage ──
        set_res = await ac.post(
            f"/api/admin/advanced-competitions/{comp_id}/stage/set-performer",
            json={"identifier": "CRG-STAGE-001", "notes": "Performer is on stage, lights up"},
            headers=admin_headers
        )
        assert set_res.status_code == 200
        set_data = set_res.json()
        assert set_data["stage_status"] in ["LIVE", "ON_STAGE"]
        assert set_data["current_performer"]["registration_reference"] == "CRG-STAGE-001"
        assert set_data["current_performer"]["user_email"] == p1_email
        assert bool(set_data["current_performer"]["submission_id"]) is True

        p1_sub_id = set_data["current_performer"]["submission_id"]

        # Check admin participants route enriches is_current_performer
        p_list_res = await ac.get(f"/api/admin/advanced-competitions/{comp_id}/participants", headers=admin_headers)
        assert p_list_res.status_code == 200
        p_list = p_list_res.json().get("participants", [])
        p1_item = next(p for p in p_list if p.get("user_email") == p1_email)
        assert p1_item["is_current_performer"] is True
        assert p1_item["stage_status"] in ["LIVE", "ON_STAGE"]

        # ── 6. Public Stage APIs (Users, Competitors, Audience) ──
        # Check /api/v1/competitions/{comp_id}/live-stage
        pub1 = await ac.get(f"/api/v1/competitions/{comp_id}/live-stage")
        assert pub1.status_code == 200
        pub1_data = pub1.json()
        assert pub1_data["stage_status"] in ["LIVE", "ON_STAGE"]
        assert pub1_data["current_performer"]["registration_reference"] == "CRG-STAGE-001"
        assert pub1_data["current_performer"]["user_name"] == "Contestant 1 Full"

        # Check /api/public/competitions/{comp_id}/current-performer
        pub2 = await ac.get(f"/api/public/competitions/{comp_id}/current-performer")
        assert pub2.status_code == 200
        assert pub2.json()["stage_status"] in ["LIVE", "ON_STAGE"]

        # Check /api/v1/live/competitions/{comp_id}/live-stage
        pub3 = await ac.get(f"/api/v1/live/competitions/{comp_id}/live-stage")
        assert pub3.status_code == 200
        assert pub3.json()["stage_status"] in ["LIVE", "ON_STAGE"]

        # ── 7. Judge Live Stage Card ──
        judge_card = await ac.get(f"/api/judge/{comp_id}/live-stage", headers=judge_headers)
        assert judge_card.status_code == 200
        card_data = judge_card.json()
        assert card_data["stage_status"] in ["LIVE", "ON_STAGE"]
        assert card_data["has_current_performer"] is True
        assert card_data["current_performer"]["user_email"] == p1_email
        assert card_data["current_performer"]["submission_id"] == p1_sub_id
        assert card_data["can_score"] is True
        assert card_data["my_scoring_status"]["has_scored"] is False
        assert len(card_data["judging_criteria"]) == 2

        # ── 8. Judge Scores Current Performer Directly from Stage Card ──
        score_res = await ac.post(
            f"/api/judge/{comp_id}/live-stage/score",
            json={
                "scores": {"Stage Presence": 9.5, "Skill & Technique": 9.0},
                "comments": "Superb choreography and stage energy!"
            },
            headers=judge_headers
        )
        assert score_res.status_code == 200, f"Direct stage score failed: {score_res.text}"
        score_data = score_res.json()
        assert score_data["total_score"] == 18.5
        assert score_data["is_locked"] is True

        # Verify Judge Card reflects the submitted score
        judge_card_after = await ac.get(f"/api/judge/{comp_id}/live-stage", headers=judge_headers)
        assert judge_card_after.status_code == 200
        card_after = judge_card_after.json()
        assert card_after["can_score"] is False
        assert card_after["my_scoring_status"]["has_scored"] is True
        assert card_after["my_scoring_status"]["total_score"] == 18.5
        assert card_after["my_scoring_status"]["is_locked"] is True

        # Strict Single-Score Rule: Scoring again must fail with 400
        rescore = await ac.post(
            f"/api/judge/{comp_id}/live-stage/score",
            json={"scores": {"Stage Presence": 8.0, "Skill & Technique": 8.0}},
            headers=judge_headers
        )
        assert rescore.status_code == 400

        # Also verify judge contestants list shows is_current_performer and scored status
        contestants_res = await ac.get(f"/api/judge/{comp_id}/contestants", headers=judge_headers)
        assert contestants_res.status_code == 200
        contestants = contestants_res.json()
        p1_cont = next(c for c in contestants if c.get("user_email") == p1_email)
        assert p1_cont["is_current_performer"] is True
        assert p1_cont["on_stage"] is True
        assert p1_cont["stage_status"] in ["LIVE", "ON_STAGE"]
        assert p1_cont["is_scored"] is True

        # ── 9. Admin Advances to Next Performer ──
        next_res = await ac.post(
            f"/api/admin/advanced-competitions/{comp_id}/stage/next-performer",
            headers=admin_headers
        )
        assert next_res.status_code == 200
        next_data = next_res.json()
        assert next_data["stage_status"] in ["LIVE", "ON_STAGE"]
        assert next_data["current_performer"]["registration_reference"] == "CRG-STAGE-002"
        assert next_data["current_performer"]["user_email"] == p2_email


        # Judge card should now show Performer 2 and can_score = True
        judge_card_p2 = await ac.get(f"/api/judge/{comp_id}/live-stage", headers=judge_headers)
        assert judge_card_p2.status_code == 200
        card_p2 = judge_card_p2.json()
        assert card_p2["current_performer"]["user_email"] == p2_email
        assert card_p2["can_score"] is True
        assert card_p2["my_scoring_status"]["has_scored"] is False

        # ── 10. Admin Advances to Performer 3 ──
        next_res2 = await ac.post(
            f"/api/admin/advanced-competitions/{comp_id}/stage/next-performer",
            headers=admin_headers
        )
        assert next_res2.status_code == 200
        assert next_res2.json()["current_performer"]["user_email"] == p3_email

        # ── 11. Admin Ends Performance / Clears Stage ──
        end_res = await ac.post(
            f"/api/admin/advanced-competitions/{comp_id}/stage/end-performance",
            headers=admin_headers
        )
        assert end_res.status_code == 200
        end_data = end_res.json()
        assert end_data["stage_status"] == "IDLE"
        assert end_data["current_performer"] is None

        # Verify public view shows IDLE
        pub_idle = await ac.get(f"/api/v1/competitions/{comp_id}/live-stage")
        assert pub_idle.status_code == 200
        assert pub_idle.json()["stage_status"] == "IDLE"
        assert pub_idle.json()["current_performer"] is None

    # Clean up test data
    await competition_collection.delete_one({"_id": ObjectId(comp_id)})
    await competition_registration_collection.delete_many({"competition_id": comp_id})
    await competition_submission_collection.delete_many({"competition_id": comp_id})
    await competition_stage_collection.delete_many({"competition_id": comp_id})
    await judge_assignment_collection.delete_many({"competition_id": comp_id})
    await judge_score_collection.delete_many({"competition_id": comp_id})
    await admin_collection.delete_many({"email": admin_email})
    await user_collection.delete_many({"email": {"$in": test_emails}})
