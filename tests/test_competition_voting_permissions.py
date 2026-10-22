import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone
from bson import ObjectId
from app.main import app
from app.core.database import (
    competition_collection,
    competition_submission_collection,
    competition_registration_collection,
    audience_vote_collection,
    user_collection,
    db
)
from app.core.security import create_access_token


@pytest_asyncio.fixture
async def ac():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_competition_self_voting_and_participant_voting_rules(ac: AsyncClient):
    """
    End-to-end verification that:
    1. Contestants cannot vote for their own submission (self-voting blocked).
    2. Registered participants in a competition cannot vote in that competition.
    3. Non-participant audience members can vote.
    4. Duplicate voting by audience is prevented.
    """
    comp_id = str(ObjectId())
    contestant1_email = f"contestant1_{comp_id[:6]}@test.com"
    contestant2_email = f"contestant2_{comp_id[:6]}@test.com"
    audience_email = f"fan_{comp_id[:6]}@test.com"

    try:
        # 1. Setup Competition with audience voting OPEN
        comp_doc = {
            "_id": ObjectId(comp_id),
            "title": "National Dance Battle 2026",
            "description": "Audience voting competition",
            "is_published": True,
            "audience_voting_enabled": True,
            "voting_status": "OPEN",
            "created_at": datetime.now(timezone.utc)
        }
        await competition_collection.insert_one(comp_doc)

        # 2. Setup Users
        c1_user = await user_collection.insert_one({
            "email": contestant1_email,
            "name": "Contestant One",
            "role": "user",
            "registration_complete": True,
            "created_at": datetime.now(timezone.utc)
        })
        c2_user = await user_collection.insert_one({
            "email": contestant2_email,
            "name": "Contestant Two",
            "role": "user",
            "registration_complete": True,
            "created_at": datetime.now(timezone.utc)
        })
        aud_user = await user_collection.insert_one({
            "email": audience_email,
            "name": "Audience Fan",
            "role": "user",
            "registration_complete": True,
            "created_at": datetime.now(timezone.utc)
        })

        # 3. Setup Submissions & Registrations for Contestant 1 and 2
        sub1_res = await competition_submission_collection.insert_one({
            "competition_id": comp_id,
            "user_email": contestant1_email,
            "user_id": str(c1_user.inserted_id),
            "status": "APPROVED",
            "created_at": datetime.now(timezone.utc)
        })
        sub1_id = str(sub1_res.inserted_id)

        await competition_registration_collection.insert_one({
            "competition_id": comp_id,
            "user_email": contestant1_email,
            "status": "CONFIRMED",
            "created_at": datetime.now(timezone.utc)
        })

        sub2_res = await competition_submission_collection.insert_one({
            "competition_id": comp_id,
            "user_email": contestant2_email,
            "user_id": str(c2_user.inserted_id),
            "status": "APPROVED",
            "created_at": datetime.now(timezone.utc)
        })
        sub2_id = str(sub2_res.inserted_id)

        await competition_registration_collection.insert_one({
            "competition_id": comp_id,
            "user_email": contestant2_email,
            "status": "CONFIRMED",
            "created_at": datetime.now(timezone.utc)
        })

        # Tokens
        c1_headers = {"Authorization": f"Bearer {create_access_token({'sub': contestant1_email})}"}
        c2_headers = {"Authorization": f"Bearer {create_access_token({'sub': contestant2_email})}"}
        aud_headers = {"Authorization": f"Bearer {create_access_token({'sub': audience_email})}"}

        # ── Test 1: Self-Voting by Email (Contestant 1 votes for Contestant 1 email) ──
        res_self_email = await ac.post(
            f"/api/v1/competitions/{comp_id}/vote",
            json={"participant_id": contestant1_email, "source": "WEBSITE"},
            headers=c1_headers
        )
        assert res_self_email.status_code == 400
        assert "cannot vote for their own submission" in res_self_email.json()["detail"].lower()

        # ── Test 2: Self-Voting by Submission ID (Contestant 1 votes for their own sub1_id) ──
        res_self_sub = await ac.post(
            f"/api/v1/competitions/{comp_id}/vote",
            json={"participant_id": sub1_id, "source": "WEBSITE"},
            headers=c1_headers
        )
        assert res_self_sub.status_code == 400
        assert "cannot vote for their own submission" in res_self_sub.json()["detail"].lower()

        # ── Test 3: Participant Voting for Another Contestant in the Same Competition ──
        # Contestant 1 attempts to vote for Contestant 2
        res_part_vote = await ac.post(
            f"/api/v1/competitions/{comp_id}/vote",
            json={"participant_id": contestant2_email, "source": "WEBSITE"},
            headers=c1_headers
        )
        assert res_part_vote.status_code == 400
        assert "participants of this competition are not eligible to vote" in res_part_vote.json()["detail"].lower()

        # ── Test 4: Legitimate Audience Member Votes for Contestant 2 ──
        res_aud_vote = await ac.post(
            f"/api/v1/competitions/{comp_id}/vote",
            json={"participant_id": contestant2_email, "source": "WEBSITE"},
            headers=aud_headers
        )
        assert res_aud_vote.status_code == 200
        assert res_aud_vote.json()["message"] == "Vote submitted successfully"

        # ── Test 5: Duplicate Vote by Audience Member for Same Contestant is Blocked ──
        res_aud_dup = await ac.post(
            f"/api/v1/competitions/{comp_id}/vote",
            json={"participant_id": contestant2_email, "source": "WEBSITE"},
            headers=aud_headers
        )
        assert res_aud_dup.status_code == 400
        assert "already voted" in res_aud_dup.json()["detail"].lower()

        # Duplicate vote by submission ID instead of email should also be blocked
        res_aud_dup_sub = await ac.post(
            f"/api/v1/competitions/{comp_id}/vote",
            json={"participant_id": sub2_id, "source": "WEBSITE"},
            headers=aud_headers
        )
        assert res_aud_dup_sub.status_code == 400
        assert "already voted" in res_aud_dup_sub.json()["detail"].lower()

        # ── Test 6: Verify Provisional Leaderboard Reflects the Audience Vote ──
        lb_res = await ac.get(f"/api/v1/competitions/{comp_id}/leaderboard")
        assert lb_res.status_code == 200
        lb_data = lb_res.json()["leaderboard"]
        c2_entry = next((e for e in lb_data if e["participant_id"] == contestant2_email), None)
        assert c2_entry is not None
        assert c2_entry["audience_votes"] >= 1

    finally:
        # Cleanup
        await competition_collection.delete_one({"_id": ObjectId(comp_id)})
        await competition_submission_collection.delete_many({"competition_id": comp_id})
        await competition_registration_collection.delete_many({"competition_id": comp_id})
        await audience_vote_collection.delete_many({"competition_id": comp_id})
        await user_collection.delete_many({"email": {"$in": [contestant1_email, contestant2_email, audience_email]}})
