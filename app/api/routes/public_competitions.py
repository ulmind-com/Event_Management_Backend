from fastapi import APIRouter, HTTPException
from app.core.database import judge_feedback_collection, competition_collection, leaderboard_result_collection
from bson import ObjectId

router = APIRouter()

@router.get("/competitions/{comp_id}/results")
async def public_results(comp_id: str):
    """Public API: Returns official leaderboard ONLY if results are published."""
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    if comp.get("result_status") != "PUBLISHED":
        raise HTTPException(status_code=403, detail="Results have not been published yet.")
        
    from app.core.database import db
    result_snapshots_collection = db["result_snapshots"]
    snapshot = await result_snapshots_collection.find_one({"competition_id": comp_id, "is_published": True})
    
    if not snapshot:
        raise HTTPException(status_code=404, detail="Official result snapshot not found.")
        
    snapshot["_id"] = str(snapshot["_id"])
    return snapshot

@router.get("/competitions/{comp_id}/contestants/{submission_id}/feedbacks")
async def public_feedbacks(comp_id: str, submission_id: str):
    """Public API: Returns judge feedbacks for a contestant ONLY after results are published."""
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    if not comp.get("results_published"):
        raise HTTPException(status_code=403, detail="Feedbacks available after results are published.")
    
    feedbacks = await judge_feedback_collection.find({
        "competition_id": comp_id, "submission_id": submission_id
    }).to_list(length=100)
    for f in feedbacks:
        f["_id"] = str(f["_id"])
    return feedbacks

from app.api.deps import get_current_user
from app.core.database import audience_vote_collection, competition_submission_collection
from app.schemas.competition import VoteReq
from datetime import datetime, timezone

@router.get("/users/me/home/live-voting")
async def get_live_voting(user: dict = Depends(get_current_user)):
    """Returns competitions where audience voting is enabled and currently OPEN."""
    comps = await competition_collection.find({
        "audience_voting_enabled": True,
        "voting_status": "OPEN",
        "is_published": True
    }).to_list(length=50)
    
    res = []
    for c in comps:
        res.append({
            "competition_id": str(c["_id"]),
            "title": c.get("title"),
            "image_url": c.get("image_url"),
            "voting_status": c.get("voting_status"),
            "audience_voting_enabled": True
        })
    return res

@router.post("/competitions/{comp_id}/vote")
async def submit_audience_vote(comp_id: str, req: VoteReq, user: dict = Depends(get_current_user)):
    """Allows an authenticated user to submit a vote if voting is OPEN."""
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp: raise HTTPException(status_code=404, detail="Competition not found")
    
    if not comp.get("audience_voting_enabled"):
        raise HTTPException(status_code=400, detail="Audience voting is not enabled for this competition.")
        
    if comp.get("voting_status", "OPEN") != "OPEN":
        raise HTTPException(status_code=400, detail="Voting is currently closed.")
        
    # Check if participant is a valid approved contestant
    submission = await competition_submission_collection.find_one({
        "competition_id": comp_id, 
        "user_email": req.participant_id, 
        "status": {"$in": ["APPROVED", "SUBMITTED"]}
    })
    if not submission:
        raise HTTPException(status_code=400, detail="Participant is not an eligible contestant.")
        
    # Prevent duplicate voting if required (e.g. 1 vote per user per contestant)
    existing_vote = await audience_vote_collection.find_one({
        "competition_id": comp_id,
        "participant_id": req.participant_id,
        "voter_email": user["email"]
    })
    if existing_vote:
        raise HTTPException(status_code=400, detail="You have already voted for this contestant.")
        
    voting_power = 1
    if getattr(req, "is_paid_pack", False):
        voting_power = 10
    elif getattr(req, "source", "WEBSITE") == "MOBILE_APP":
        voting_power = 5
    else:
        voting_power = 1

    vote_doc = {
        "competition_id": comp_id,
        "participant_id": req.participant_id,
        "voter_email": user["email"],
        "source": getattr(req, "source", "WEBSITE"),
        "is_paid_pack": getattr(req, "is_paid_pack", False),
        "voting_power": voting_power,
        "created_at": datetime.now(timezone.utc)
    }
    await audience_vote_collection.insert_one(vote_doc)
    
    # In a real system, you'd emit a WS message here to update the leaderboard
    from app.core.websocket import ws_manager
    await ws_manager.broadcast_to_competition(comp_id, {"type": "LEADERBOARD_UPDATED"})
    
    return {"message": "Vote submitted successfully"}

@router.get("/competitions/{comp_id}/leaderboard")
async def provisional_leaderboard(comp_id: str):
    """Returns the real-time provisional leaderboard calculated by the ranking service."""
    from app.services.ranking_service import RankingService
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp: raise HTTPException(status_code=404, detail="Competition not found")
    
    submissions = await competition_submission_collection.find({"competition_id": comp_id, "status": {"$in": ["APPROVED", "SUBMITTED"]}}).to_list(length=5000)
    
    max_judge_score = sum(crit.get("max_score", 0) for crit in comp.get("judging_criteria", []))
    participants_data = []
    max_audience_votes = 0
    from app.core.database import judge_score_collection
    
    for sub in submissions:
        user_email = sub["user_email"]
        # Sum voting power
        pipeline = [
            {"$match": {"competition_id": comp_id, "participant_id": user_email}},
            {"$group": {"_id": None, "total": {"$sum": "$voting_power"}}}
        ]
        agg_res = await audience_vote_collection.aggregate(pipeline).to_list(1)
        votes = agg_res[0]["total"] if agg_res else 0
        
        if votes > max_audience_votes: max_audience_votes = votes
        
        # Calculate avg judge score for this user
        scores = await judge_score_collection.find({"competition_id": comp_id, "participant_id": user_email}).to_list(length=100)
        total_judge = 0
        if scores:
            for s in scores:
                total_judge += sum(s.get("scores", {}).values())
            total_judge = total_judge / len(scores)
            
        participants_data.append({
            "participant_id": user_email,
            "name": sub.get("name", user_email),
            "audience_votes": votes,
            "judge_score": total_judge
        })
        
    final_leaderboard = RankingService.calculate_leaderboard(participants_data, max_audience_votes, max_judge_score, comp)
    return {"ranking_mode": final_leaderboard[0]["ranking_mode"] if final_leaderboard else "UNKNOWN", "leaderboard": final_leaderboard}
