import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.security import create_access_token
from app.services.ranking_service import RankingService
from app.main import app

@pytest.mark.asyncio
async def test_ranking_audience_only():
    comp_settings = {
        "audience_voting_enabled": True,
        "judge_scoring_enabled": False,
        "audience_vote_weight": 0.0,
        "judge_score_weight": 0.0
    }
    participants = [{"participant_id": "u1", "audience_votes": 100, "judge_score": 90}]
    leaderboard = RankingService.calculate_leaderboard(participants, 100, 100, comp_settings)
    assert leaderboard[0]["ranking_mode"] == "AUDIENCE_VOTING_ONLY"
    assert leaderboard[0]["final_score"] == 100.0  # 100/100 votes = 100%

@pytest.mark.asyncio
async def test_ranking_judge_only():
    comp_settings = {
        "audience_voting_enabled": False,
        "judge_scoring_enabled": True
    }
    participants = [{"participant_id": "u1", "audience_votes": 100, "judge_score": 80}]
    leaderboard = RankingService.calculate_leaderboard(participants, 100, 100, comp_settings)
    assert leaderboard[0]["ranking_mode"] == "JUDGES_SCORE_ONLY"
    assert leaderboard[0]["final_score"] == 80.0  # 80/100 score = 80%

@pytest.mark.asyncio
async def test_ranking_combined():
    comp_settings = {
        "audience_voting_enabled": True,
        "judge_scoring_enabled": True,
        "audience_vote_weight": 0.40,
        "judge_score_weight": 0.60
    }
    participants = [{"participant_id": "u1", "audience_votes": 100, "judge_score": 80}]
    # Audience: 100% * 0.40 = 40.0
    # Judge: 80% * 0.60 = 48.0
    # Total = 88.0
    leaderboard = RankingService.calculate_leaderboard(participants, 100, 100, comp_settings)
    assert leaderboard[0]["ranking_mode"] == "COMBINED_AUDIENCE_AND_JUDGES"
    assert leaderboard[0]["final_score"] == 88.0

@pytest.mark.asyncio
async def test_lifecycle_and_finalization():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db
    
    admin_token = create_access_token({"sub": "admin@eventora.com", "type": "admin"})
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Clean previous
    await db["competitions"].delete_many({"title": "Test Lifecycle Comp"})
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create Comp
        create_res = await ac.post("/api/admin/advanced-competitions/", json={
            "title": "Test Lifecycle Comp",
            "description": "Desc",
            "category": "Coding",
            "start_date": "2026-10-01T00:00:00Z",
            "end_date": "2026-10-30T00:00:00Z",
            "registration_start_date": "2026-10-01T00:00:00Z",
            "registration_deadline": "2026-10-05T00:00:00Z",
            "submission_deadline": "2026-10-10T00:00:00Z",
            "audience_voting_enabled": True,
            "judge_scoring_enabled": True,
            "audience_vote_weight": 0.5,
            "judge_score_weight": 0.5
        }, headers=headers)
        
        assert create_res.status_code == 200
        comp_id = create_res.json()["_id"]
        
        # Test start finalization fails because voting/judging not closed
        fin_res_1 = await ac.post(f"/api/admin/advanced-competitions/{comp_id}/finalization/start", headers=headers)
        assert fin_res_1.status_code == 400
        assert "must be closed" in fin_res_1.text
        
        # Close Voting and Judging
        await ac.post(f"/api/admin/advanced-competitions/{comp_id}/voting/close", headers=headers)
        await ac.post(f"/api/admin/advanced-competitions/{comp_id}/judging/close", headers=headers)
        
        # Finalization should work now
        fin_res_2 = await ac.post(f"/api/admin/advanced-competitions/{comp_id}/finalization/start", headers=headers)
        assert fin_res_2.status_code == 200
        
        # Approve and Publish
        app_res = await ac.post(f"/api/admin/advanced-competitions/{comp_id}/results/approve", headers=headers)
        assert app_res.status_code == 200
        
        pub_res = await ac.post(f"/api/admin/advanced-competitions/{comp_id}/results/publish", headers=headers)
        assert pub_res.status_code == 200

        # Public API
        pub_api = await ac.get(f"/api/public/competitions/{comp_id}/results")
        assert pub_api.status_code == 200
        assert pub_api.json()["is_published"] == True
