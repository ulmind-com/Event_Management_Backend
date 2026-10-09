from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from bson import ObjectId
from app.core.admin_auth import get_current_admin
from app.core.database import (
    competition_collection, 
    competition_registration_collection,
    competition_submission_collection,
    judge_score_collection,
    audience_vote_collection,
    leaderboard_result_collection
)
from app.schemas.competition import CompetitionCreateReq, CompetitionUpdateReq
from datetime import datetime, timezone

router = APIRouter()

@router.post("/")
async def create_competition(req: CompetitionCreateReq, admin: dict = Depends(get_current_admin)):
    """Creates a new advanced competition with rules, criteria, and dynamic fields."""
    comp_dict = req.model_dump()
    comp_dict["created_at"] = datetime.now(timezone.utc)
    comp_dict["updated_at"] = datetime.now(timezone.utc)
    
    res = await competition_collection.insert_one(comp_dict)
    comp_dict["_id"] = str(res.inserted_id)
    return comp_dict

@router.get("/")
async def list_competitions(admin: dict = Depends(get_current_admin)):
    """List all competitions for Admin."""
    comps = await competition_collection.find().sort("created_at", -1).to_list(length=100)
    for c in comps:
        c["_id"] = str(c["_id"])
    return comps

@router.patch("/{comp_id}")
async def update_competition(comp_id: str, req: CompetitionUpdateReq, admin: dict = Depends(get_current_admin)):
    """Updates competition fields."""
    update_data = {k: v for k, v in req.model_dump().items() if v is not None}
    if not update_data:
        return {"message": "No fields to update"}
        
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    res = await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": update_data}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    return {"message": "Competition updated successfully"}

@router.post("/{comp_id}/publish")
async def publish_competition(comp_id: str, admin: dict = Depends(get_current_admin)):
    res = await competition_collection.update_one(
        {"_id": ObjectId(comp_id)}, {"$set": {"is_published": True, "updated_at": datetime.now(timezone.utc)}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")
    return {"message": "Competition published"}

@router.post("/{comp_id}/unpublish")
async def unpublish_competition(comp_id: str, admin: dict = Depends(get_current_admin)):
    res = await competition_collection.update_one(
        {"_id": ObjectId(comp_id)}, {"$set": {"is_published": False, "updated_at": datetime.now(timezone.utc)}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")
    return {"message": "Competition unpublished"}

@router.delete("/{comp_id}")
async def delete_competition(comp_id: str, admin: dict = Depends(get_current_admin)):
    """
    16. COMPLETE COMPETITION DELETION
    Secure deletion workflow:
    - Cascades to registrations, submissions, scores, votes, leaderboards.
    - (Future: Call Cloudinary to delete assets mapped in submissions).
    """
    try:
        c_id = ObjectId(comp_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid ID")
        
    comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    # Cascade Deletes
    await competition_submission_collection.delete_many({"competition_id": comp_id})
    await competition_registration_collection.delete_many({"competition_id": comp_id})
    await judge_score_collection.delete_many({"competition_id": comp_id})
    await audience_vote_collection.delete_many({"competition_id": comp_id})
    await leaderboard_result_collection.delete_many({"competition_id": comp_id})
    
    await competition_collection.delete_one({"_id": c_id})
    
    return {"message": "Competition and all associated records deleted securely."}

@router.post("/{comp_id}/recalculate-rankings")
async def recalculate_rankings(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Aggregates scores and updates the leaderboard."""
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    # Group by submission_id
    pipeline = [
        {"$match": {"competition_id": comp_id}},
        {"$group": {
            "_id": "$submission_id",
            "total_judge_score": {"$sum": "$total_score"},
            "judge_count": {"$sum": 1}
        }}
    ]
    
    score_agg = await judge_score_collection.aggregate(pipeline).to_list(length=1000)
    
    # Clean previous leaderboard
    await leaderboard_result_collection.delete_many({"competition_id": comp_id})
    
    docs = []
    for s in score_agg:
        # Here we could fetch audience votes and apply audience_vote_weight
        # For simplicity in this abstraction, we just use judge scores
        
        docs.append({
            "competition_id": comp_id,
            "submission_id": s["_id"],
            "total_score": s["total_judge_score"],
            "rank": 0 # Will be assigned
        })
        
    if not docs:
        return {"message": "No scores to calculate."}
        
    # Sort and rank
    docs.sort(key=lambda x: x["total_score"], reverse=True)
    current_rank = 1
    for i, doc in enumerate(docs):
        if i > 0 and docs[i]["total_score"] == docs[i-1]["total_score"]:
            doc["rank"] = docs[i-1]["rank"]
        else:
            doc["rank"] = current_rank
        current_rank += 1
            
    await leaderboard_result_collection.insert_many(docs)
    return {"message": "Rankings recalculated successfully", "total_ranked": len(docs)}

@router.post("/{comp_id}/judging/lock")
async def lock_scores(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Locks all scores for a competition so judges cannot casually edit them."""
    res = await judge_score_collection.update_many(
        {"competition_id": comp_id, "is_locked": {"$ne": True}},
        {"$set": {
            "is_locked": True, 
            "locked_by": admin.get("email"),
            "locked_at": datetime.now(timezone.utc)
        }}
    )
    return {"message": "Scores locked successfully.", "locked_count": res.modified_count}

@router.post("/{comp_id}/results/approve")
async def approve_results(comp_id: str, admin: dict = Depends(get_current_admin)):
    """
    Recalculates the leaderboard ONLY from LOCKED scores.
    This acts as the official provisional ranking.
    """
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    pipeline = [
        {"$match": {"competition_id": comp_id, "is_locked": True}},
        {"$group": {
            "_id": "$submission_id",
            "total_judge_score": {"$sum": "$total_score"},
            "judge_count": {"$sum": 1}
        }}
    ]
    
    score_agg = await judge_score_collection.aggregate(pipeline).to_list(length=1000)
    
    await leaderboard_result_collection.delete_many({"competition_id": comp_id})
    
    docs = []
    for s in score_agg:
        docs.append({
            "competition_id": comp_id,
            "submission_id": s["_id"],
            "total_score": s["total_judge_score"],
            "rank": 0,
            "status": "APPROVED"
        })
        
    if not docs:
        return {"message": "No locked scores available to calculate."}
        
    docs.sort(key=lambda x: x["total_score"], reverse=True)
    current_rank = 1
    for i, doc in enumerate(docs):
        if i > 0 and docs[i]["total_score"] == docs[i-1]["total_score"]:
            doc["rank"] = docs[i-1]["rank"]
        else:
            doc["rank"] = current_rank
        current_rank += 1
            
    await leaderboard_result_collection.insert_many(docs)
    return {"message": "Results approved and Leaderboard generated successfully.", "total_ranked": len(docs)}

@router.post("/{comp_id}/results/publish")
async def publish_results(comp_id: str, admin: dict = Depends(get_current_admin)):
    """Publishes the approved leaderboard to the public."""
    # In a real app, this might just flip a boolean on the Competition.
    res = await competition_collection.update_one(
        {"_id": ObjectId(comp_id)},
        {"$set": {"results_published": True, "results_published_at": datetime.now(timezone.utc)}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Competition not found")
    return {"message": "Results published successfully."}
