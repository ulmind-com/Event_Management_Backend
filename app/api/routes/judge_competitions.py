from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import (
    competition_collection,
    competition_submission_collection,
    judge_score_collection
)
from app.schemas.competition import ScoreReq, SubmissionStatus
from datetime import datetime, timezone

router = APIRouter()

# Assuming a simple role check, in a real app, you'd have a `get_current_judge` dependency.
# We will use `get_current_user` and assume all logged-in users with a specific role can judge.
# For this abstraction, we will just use get_current_user and check a hypothetical role.

async def get_current_judge(user: dict = Depends(get_current_user)):
    # if user.get("role") != "JUDGE":
    #    raise HTTPException(status_code=403, detail="Not authorized as judge")
    return user

@router.post("/submissions/{submission_id}/scores")
async def submit_score(submission_id: str, req: ScoreReq, judge: dict = Depends(get_current_judge)):
    """Allows a judge to score an approved submission."""
    try:
        sub_id = ObjectId(submission_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid submission ID")
        
    submission = await competition_submission_collection.find_one({"_id": sub_id})
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
        
    if submission.get("status") not in [SubmissionStatus.SUBMITTED.value, SubmissionStatus.APPROVED.value]:
        raise HTTPException(status_code=400, detail="Submission is not in a scorable state.")
        
    comp = await competition_collection.find_one({"_id": ObjectId(submission["competition_id"])})
    
    # Validate scores against criteria
    criteria = comp.get("judging_criteria", [])
    valid_names = {c["name"]: c["max_score"] for c in criteria}
    
    total_score = 0.0
    for crit_name, score_val in req.scores.items():
        if crit_name not in valid_names:
            raise HTTPException(status_code=400, detail=f"Unknown criterion: {crit_name}")
        if score_val < 0 or score_val > valid_names[crit_name]:
            raise HTTPException(status_code=400, detail=f"Score {score_val} for {crit_name} is out of bounds (0 - {valid_names[crit_name]}).")
        
        # Multiply by weight (assuming criteria dict in DB has weight, default 1.0)
        weight = next((c.get("weight", 1.0) for c in criteria if c["name"] == crit_name), 1.0)
        total_score += (score_val * weight)

    doc = {
        "submission_id": submission_id,
        "competition_id": submission["competition_id"],
        "judge_email": judge["email"],
        "scores": req.scores,
        "total_score": total_score,
        "comments": req.comments,
        "scored_at": datetime.now(timezone.utc)
    }
    
    # Upsert to prevent duplicate scoring by the same judge
    await judge_score_collection.update_one(
        {"submission_id": submission_id, "judge_email": judge["email"]},
        {"$set": doc},
        upsert=True
    )
    
    return {"message": "Score submitted successfully", "total_score": total_score}
