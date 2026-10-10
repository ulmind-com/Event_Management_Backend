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
    if not comp.get("results_published"):
        raise HTTPException(status_code=403, detail="Results have not been published yet.")
    
    results = await leaderboard_result_collection.find(
        {"competition_id": comp_id}
    ).sort("rank", 1).to_list(length=1000)
    for r in results:
        r["_id"] = str(r["_id"])
    return results

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
