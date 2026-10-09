from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import (
    competition_collection,
    competition_submission_collection,
    judge_score_collection,
    judge_assignment_collection
)
from app.schemas.competition import ScoreReq, SubmissionStatus
from datetime import datetime, timezone

router = APIRouter()

async def get_current_judge(user: dict = Depends(get_current_user)):
    """Verifies that the current user has the JUDGE role."""
    if user.get("role") != "JUDGE":
        raise HTTPException(status_code=403, detail="Not authorized as judge")
    if user.get("status") != "ACTIVE":
        raise HTTPException(status_code=403, detail="Judge account is not active")
    return user

async def verify_judge_assignment(judge_email: str, competition_id: str):
    """Verifies that the judge is strictly assigned to this competition."""
    assignment = await judge_assignment_collection.find_one({
        "judge_email": judge_email,
        "competition_id": competition_id
    })
    if not assignment:
        raise HTTPException(status_code=403, detail="You are not authorized to view or score this competition.")

@router.get("/")
async def list_assigned_competitions(judge: dict = Depends(get_current_judge)):
    """Returns only competitions the judge is assigned to."""
    assignments = await judge_assignment_collection.find({"judge_email": judge["email"]}).to_list(length=100)
    comp_ids = [ObjectId(a["competition_id"]) for a in assignments]
    
    comps = await competition_collection.find({"_id": {"$in": comp_ids}}).to_list(length=100)
    for c in comps:
        c["_id"] = str(c["_id"])
    return comps

@router.get("/{comp_id}/contestants")
async def list_contestants(comp_id: str, judge: dict = Depends(get_current_judge)):
    """Returns approved submissions for this competition."""
    await verify_judge_assignment(judge["email"], comp_id)
    
    subs = await competition_submission_collection.find({
        "competition_id": comp_id,
        "status": {"$in": [SubmissionStatus.SUBMITTED.value, SubmissionStatus.APPROVED.value]}
    }).to_list(length=1000)
    for s in subs:
        s["_id"] = str(s["_id"])
    return subs

@router.post("/submissions/{submission_id}/scores")
async def submit_score(submission_id: str, req: ScoreReq, judge: dict = Depends(get_current_judge)):
    """Allows a judge to score an approved submission, unless it's locked."""
    try:
        sub_id = ObjectId(submission_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid submission ID")
        
    submission = await competition_submission_collection.find_one({"_id": sub_id})
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
        
    comp_id = submission["competition_id"]
    await verify_judge_assignment(judge["email"], comp_id)
        
    if submission.get("status") not in [SubmissionStatus.SUBMITTED.value, SubmissionStatus.APPROVED.value]:
        raise HTTPException(status_code=400, detail="Submission is not in a scorable state.")
        
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    
    # Check Score Lock Status
    existing_score = await judge_score_collection.find_one({
        "submission_id": submission_id,
        "judge_email": judge["email"]
    })
    
    if existing_score and existing_score.get("is_locked"):
        raise HTTPException(status_code=403, detail="Scores for this submission are locked and cannot be modified.")
    
    # Validate scores against criteria
    criteria = comp.get("judging_criteria", [])
    valid_names = {c["name"]: c["max_score"] for c in criteria}
    
    total_score = 0.0
    for crit_name, score_val in req.scores.items():
        if crit_name not in valid_names:
            raise HTTPException(status_code=400, detail=f"Unknown criterion: {crit_name}")
        if score_val < 0 or score_val > valid_names[crit_name]:
            raise HTTPException(status_code=400, detail=f"Score {score_val} for {crit_name} is out of bounds (0 - {valid_names[crit_name]}).")
        
        weight = next((c.get("weight", 1.0) for c in criteria if c["name"] == crit_name), 1.0)
        total_score += (score_val * weight)

    doc = {
        "submission_id": submission_id,
        "competition_id": comp_id,
        "judge_email": judge["email"],
        "scores": req.scores,
        "total_score": total_score,
        "comments": req.comments,
        "scored_at": datetime.now(timezone.utc),
        "is_locked": False
    }
    
    await judge_score_collection.update_one(
        {"submission_id": submission_id, "judge_email": judge["email"]},
        {"$set": doc},
        upsert=True
    )
    
    return {"message": "Score submitted successfully", "total_score": total_score}
