import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone
from bson import ObjectId
from app.main import app
from app.core.database import (
    competition_collection,
    competition_submission_collection,
    judge_score_collection,
    audience_vote_collection,
    user_collection,
    leaderboard_result_collection,
    db
)
from app.core.security import create_access_token


@pytest_asyncio.fixture
async def ac():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_leaderboard_points_aggregation_and_display(ac: AsyncClient):
    """
    End-to-end verification that user points, judge scores, audience votes,
    and profile data are properly calculated and returned in the leaderboard.
    """
    comp_id = str(ObjectId())
    comp_doc = {
        "_id": ObjectId(comp_id),
        "title": "National Dance Championship 2026",
        "description": "Grand live battle",
        "audience_voting_enabled": True,
        "judge_scoring_enabled": True,
        "audience_vote_weight": 50.0,
        "judge_score_weight": 50.0,
        "voting_status": "OPEN",
        "judging_status": "OPEN",
        "result_status": "PENDING",
        "is_published": True,
        "judging_criteria": [
            {"name": "Choreography", "max_score": 50, "weight": 1.0},
            {"name": "Execution", "max_score": 50, "weight": 1.0}
        ],
        "created_at": datetime.now(timezone.utc)
    }
    await competition_collection.insert_one(comp_doc)

    user1_email = f"contestant_alpha_{int(datetime.now(timezone.utc).timestamp())}@test.com"
    user2_email = f"contestant_beta_{int(datetime.now(timezone.utc).timestamp())}@test.com"

    await user_collection.insert_one({
        "email": user1_email,
        "full_name": "Alpha Superstar",
        "name": "Alpha Superstar",
        "profile_photo": "https://img.test/alpha.jpg",
        "created_at": datetime.now(timezone.utc)
    })
    await user_collection.insert_one({
        "email": user2_email,
        "full_name": "Beta Dancer",
        "name": "Beta Dancer",
        "profile_photo": "https://img.test/beta.jpg",
        "created_at": datetime.now(timezone.utc)
    })

    # Submissions
    sub1_res = await competition_submission_collection.insert_one({
        "competition_id": comp_id,
        "user_email": user1_email,
        "name": "Alpha Superstar",
        "status": "APPROVED",
        "submitted_at": datetime.now(timezone.utc)
    })
    sub1_id = str(sub1_res.inserted_id)

    sub2_res = await competition_submission_collection.insert_one({
        "competition_id": comp_id,
        "user_email": user2_email,
        "name": "Beta Dancer",
        "status": "APPROVED",
        "submitted_at": datetime.now(timezone.utc)
    })
    sub2_id = str(sub2_res.inserted_id)

    # 1. Judge Scores: Saved with submission_id (the exact format from judge app)
    await judge_score_collection.insert_one({
        "submission_id": sub1_id,
        "competition_id": comp_id,
        "judge_email": "judge1@test.com",
        "scores": {"Choreography": 45, "Execution": 45},
        "total_score": 90.0,
        "is_locked": True,
        "scored_at": datetime.now(timezone.utc)
    })
    await judge_score_collection.insert_one({
        "submission_id": sub2_id,
        "competition_id": comp_id,
        "judge_email": "judge1@test.com",
        "scores": {"Choreography": 35, "Execution": 35},
        "total_score": 70.0,
        "is_locked": True,
        "scored_at": datetime.now(timezone.utc)
    })

    # 2. Audience Votes
    await audience_vote_collection.insert_one({
        "competition_id": comp_id,
        "participant_id": user1_email,
        "voting_power": 100,
        "voter_email": "fan1@test.com",
        "created_at": datetime.now(timezone.utc)
    })
    await audience_vote_collection.insert_one({
        "competition_id": comp_id,
        "participant_id": user2_email,
        "voting_power": 60,
        "voter_email": "fan2@test.com",
        "created_at": datetime.now(timezone.utc)
    })

    # Query provisional leaderboard
    res = await ac.get(f"/api/v1/competitions/{comp_id}/leaderboard")
    assert res.status_code == 200
    data = res.json()
    assert "leaderboard" in data
    lb = data["leaderboard"]
    assert len(lb) == 2

    # User 1 should be rank 1
    assert lb[0]["participant_id"] == user1_email
    assert lb[0]["name"] == "Alpha Superstar"
    assert lb[0]["profile_photo"] == "https://img.test/alpha.jpg"
    assert lb[0]["audience_votes"] == 100
    assert lb[0]["judge_score"] == 90.0
    assert lb[0]["final_score"] > 0
    assert lb[0]["points"] > 0
    assert lb[0]["rank"] == 1

    # User 2 should be rank 2
    assert lb[1]["participant_id"] == user2_email
    assert lb[1]["name"] == "Beta Dancer"
    assert lb[1]["judge_score"] == 70.0
    assert lb[1]["audience_votes"] == 60
    assert lb[1]["rank"] == 2

    # Clean up
    await competition_collection.delete_one({"_id": ObjectId(comp_id)})
    await competition_submission_collection.delete_many({"competition_id": comp_id})
    await judge_score_collection.delete_many({"competition_id": comp_id})
    await audience_vote_collection.delete_many({"competition_id": comp_id})
    await user_collection.delete_many({"email": {"$in": [user1_email, user2_email]}})
