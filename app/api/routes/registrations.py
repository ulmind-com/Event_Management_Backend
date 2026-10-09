from fastapi import APIRouter, Depends, HTTPException
from typing import List
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import registration_collection, event_collection, competition_collection, course_collection
from app.schemas.action import RegistrationReq
from datetime import datetime, timezone

router = APIRouter()

@router.post("/")
async def register_activity(req: RegistrationReq, current_user: dict = Depends(get_current_user)):
    """Register for an event, competition, or course"""
    email = current_user["email"]
    
    # Check if already registered
    existing = await registration_collection.find_one({
        "email": email, 
        "activity_id": req.activity_id,
        "activity_type": req.activity_type
    })
    
    if existing:
        raise HTTPException(status_code=400, detail="Already registered for this activity")

    # Validate activity exists and eligibility
    try:
        obj_id = ObjectId(req.activity_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid activity ID")

    if req.activity_type == "event":
        activity = await event_collection.find_one({"_id": obj_id})
    elif req.activity_type == "competition":
        activity = await competition_collection.find_one({"_id": obj_id})
    elif req.activity_type == "course":
        activity = await course_collection.find_one({"_id": obj_id})
    else:
        raise HTTPException(status_code=400, detail="Invalid activity type")

    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    # Check capacity/deadlines (simplified logic for now)
    if "deadline" in activity:
        deadline = datetime.fromisoformat(activity["deadline"])
        if datetime.now(timezone.utc) > deadline:
            raise HTTPException(status_code=400, detail="Registration deadline has passed")
            
    if "capacity" in activity and "registered_count" in activity:
        if activity["registered_count"] >= activity["capacity"]:
            raise HTTPException(status_code=400, detail="Activity is full")

    # Register
    new_reg = {
        "email": email,
        "activity_id": req.activity_id,
        "activity_type": req.activity_type,
        "registered_at": datetime.now(timezone.utc),
        "status": "confirmed"
    }
    
    await registration_collection.insert_one(new_reg)
    
    # Update count
    if req.activity_type == "event":
        await event_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})
    elif req.activity_type == "competition":
        await competition_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})
    elif req.activity_type == "course":
        await course_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})

    return {"message": "Registration successful"}

@router.delete("/{registration_id}")
async def cancel_registration(registration_id: str, current_user: dict = Depends(get_current_user)):
    """Cancel a registration"""
    try:
        obj_id = ObjectId(registration_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid registration ID")

    reg = await registration_collection.find_one({"_id": obj_id, "email": current_user["email"]})
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")
        
    # Delete
    await registration_collection.delete_one({"_id": obj_id})
    
    # Decrement count
    act_obj_id = ObjectId(reg["activity_id"])
    if reg["activity_type"] == "event":
        await event_collection.update_one({"_id": act_obj_id}, {"$inc": {"registered_count": -1}})
    elif reg["activity_type"] == "competition":
        await competition_collection.update_one({"_id": act_obj_id}, {"$inc": {"registered_count": -1}})
    elif reg["activity_type"] == "course":
        await course_collection.update_one({"_id": act_obj_id}, {"$inc": {"registered_count": -1}})

    return {"message": "Registration cancelled successfully"}
