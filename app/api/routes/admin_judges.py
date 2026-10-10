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
    existing = await user_collection.find_one({"email": req.email})
    if existing:
        raise HTTPException(status_code=409, detail="User with this email already exists.")
        
    doc = req.model_dump()
    doc["role"] = "JUDGE"
    doc["status"] = "ACTIVE"
    doc["is_verified"] = True
    doc["registration_complete"] = True
    doc["created_at"] = datetime.now(timezone.utc)
    
    await user_collection.insert_one(doc)
    return {"message": "Judge created successfully", "email": req.email}

@router.get("/")
async def list_judges(admin: dict = Depends(get_current_admin)):
    """List all judges."""
    judges = await user_collection.find({"role": "JUDGE"}).to_list(length=100)
    for j in judges:
        j["_id"] = str(j["_id"])
    return judges

@router.post("/{email}/send-invitation")
async def send_invitation(email: str, bg_tasks: BackgroundTasks, admin: dict = Depends(get_current_admin)):
    """Sends a welcome email instructing the judge to log in via OTP."""
    judge = await user_collection.find_one({"email": email, "role": "JUDGE"})
    if not judge:
        raise HTTPException(status_code=404, detail="Judge not found")
        
    bg_tasks.add_task(EmailService.send_judge_invitation, email, judge.get("full_name", "Judge"))
    return {"message": "Invitation sent successfully."}

@router.post("/assignments")
async def assign_judge(req: JudgeAssignmentReq, admin: dict = Depends(get_current_admin)):
    """Assigns an active judge to a competition."""
    judge = await user_collection.find_one({"email": req.judge_email, "role": "JUDGE"})
    if not judge:
        raise HTTPException(status_code=404, detail="Judge not found")
        
    existing_assignment = await judge_assignment_collection.find_one({
        "judge_email": req.judge_email,
        "competition_id": req.competition_id
    })
    if existing_assignment:
        raise HTTPException(status_code=409, detail="Judge is already assigned to this competition.")
        
    doc = {
        "judge_email": req.judge_email,
        "competition_id": req.competition_id,
        "assigned_by": admin.get("email"),
        "assigned_at": datetime.now(timezone.utc)
    }
    
    await judge_assignment_collection.insert_one(doc)
    return {"message": "Judge assigned successfully"}
    
@router.delete("/assignments")
async def remove_judge_assignment(req: JudgeAssignmentReq, admin: dict = Depends(get_current_admin)):
    """Removes a judge from a competition."""
    res = await judge_assignment_collection.delete_one({
        "judge_email": req.judge_email,
        "competition_id": req.competition_id
    })
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return {"message": "Assignment removed successfully"}
