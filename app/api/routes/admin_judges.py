import re
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from app.core.admin_auth import get_current_admin
from app.core.database import user_collection, judge_assignment_collection
from app.schemas.judge import JudgeCreateReq, JudgeUpdateReq, JudgeAssignmentReq
from app.services.email import EmailService
from datetime import datetime, timezone
from app.core.config import settings

router = APIRouter()

@router.post("/")
async def create_judge(req: JudgeCreateReq, admin: dict = Depends(get_current_admin)):
    """Creates a new judge and stores them in the users collection with role JUDGE."""
    clean_email = req.email.strip().lower()
    existing = await user_collection.find_one({"email": {"$regex": f"^{re.escape(clean_email)}$", "$options": "i"}})
    if existing:
        raise HTTPException(status_code=409, detail="User with this email already exists.")
        
    doc = req.model_dump()
    doc["email"] = clean_email
    doc["name"] = req.full_name
    doc["full_name"] = req.full_name
    doc["role"] = "JUDGE"
    doc["status"] = "ACTIVE"
    doc["is_active"] = True
    doc["is_verified"] = True
    doc["registration_complete"] = True
    doc["created_at"] = datetime.now(timezone.utc)
    
    await user_collection.insert_one(doc)
    return {"message": "Judge created successfully", "email": clean_email}

@router.get("/")
async def list_judges(admin: dict = Depends(get_current_admin)):
    """List all judges enriched with their competition assignment count."""
    judges = await user_collection.find({"role": "JUDGE"}).to_list(length=200)
    
    # Pre-fetch all judge assignments for rapid mapping
    all_assignments = await judge_assignment_collection.find().to_list(length=1000)
    assignments_by_email: dict[str, list[str]] = {}
    for a in all_assignments:
        j_email = str(a.get("judge_email", "")).strip().lower()
        c_id = str(a.get("competition_id", ""))
        if j_email and c_id:
            assignments_by_email.setdefault(j_email, []).append(c_id)

    for j in judges:
        j["_id"] = str(j["_id"])
        email_key = str(j.get("email", "")).strip().lower()
        assigned_comp_ids = assignments_by_email.get(email_key, [])
        j["name"] = j.get("name") or j.get("full_name") or "Judge"
        j["full_name"] = j.get("full_name") or j.get("name") or "Judge"
        j["assigned_competitions"] = assigned_comp_ids
        j["assigned_count"] = len(assigned_comp_ids)
        
    return judges

@router.get("/{email}/assignments")
async def get_judge_assignments(email: str, admin: dict = Depends(get_current_admin)):
    """Returns list of competition IDs assigned to a judge."""
    clean_email = email.strip()
    cursor = judge_assignment_collection.find({
        "judge_email": {"$regex": f"^{re.escape(clean_email)}$", "$options": "i"}
    })
    assignments = await cursor.to_list(length=500)
    return [str(a["competition_id"]) for a in assignments]

@router.post("/{email}/send-invitation")
async def send_invitation(email: str, bg_tasks: BackgroundTasks, admin: dict = Depends(get_current_admin)):
    """Sends a welcome email instructing the judge to log in via OTP."""
    clean_email = email.strip()
    judge = await user_collection.find_one({
        "email": {"$regex": f"^{re.escape(clean_email)}$", "$options": "i"},
        "role": "JUDGE"
    })
    if not judge:
        raise HTTPException(status_code=404, detail="Judge not found")
        
    judge_email = judge.get("email") or clean_email
    judge_name = judge.get("full_name") or judge.get("name") or "Judge"
    bg_tasks.add_task(EmailService.send_judge_invitation, judge_email, judge_name)
    return {"message": "Invitation sent successfully."}

@router.post("/assignments")
async def assign_judge(req: JudgeAssignmentReq, admin: dict = Depends(get_current_admin)):
    """Assigns an active judge to a competition."""
    clean_email = req.judge_email.strip()
    clean_comp_id = req.competition_id.strip()
    
    judge = await user_collection.find_one({
        "email": {"$regex": f"^{re.escape(clean_email)}$", "$options": "i"},
        "role": "JUDGE"
    })
    if not judge:
        raise HTTPException(status_code=404, detail="Judge not found")
        
    comp_matches = [clean_comp_id]
    if ObjectId.is_valid(clean_comp_id):
        comp_matches.append(ObjectId(clean_comp_id))

    existing_assignment = await judge_assignment_collection.find_one({
        "judge_email": {"$regex": f"^{re.escape(clean_email)}$", "$options": "i"},
        "competition_id": {"$in": comp_matches}
    })
    if existing_assignment:
        raise HTTPException(status_code=409, detail="Judge is already assigned to this competition.")
        
    doc = {
        "judge_email": judge.get("email") or clean_email,
        "competition_id": clean_comp_id,
        "assigned_by": admin.get("email"),
        "assigned_at": datetime.now(timezone.utc)
    }
    
    await judge_assignment_collection.insert_one(doc)
    return {"message": "Judge assigned successfully"}
    
@router.delete("/assignments")
async def remove_judge_assignment(req: JudgeAssignmentReq, admin: dict = Depends(get_current_admin)):
    """Removes a judge from a competition."""
    clean_email = req.judge_email.strip()
    clean_comp_id = req.competition_id.strip()
    
    comp_matches = [clean_comp_id]
    if ObjectId.is_valid(clean_comp_id):
        comp_matches.append(ObjectId(clean_comp_id))

    res = await judge_assignment_collection.delete_many({
        "judge_email": {"$regex": f"^{re.escape(clean_email)}$", "$options": "i"},
        "competition_id": {"$in": comp_matches}
    })
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return {"message": "Assignment removed successfully"}
