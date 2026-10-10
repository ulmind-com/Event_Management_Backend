from fastapi import APIRouter, Depends, HTTPException
from app.core.admin_auth import get_current_admin
from app.core.database import user_collection
from app.services.email import EmailService
from pydantic import BaseModel, EmailStr

router = APIRouter()

class VolunteerCreateReq(BaseModel):
    email: EmailStr

@router.get("/")
async def list_volunteers(admin: dict = Depends(get_current_admin)):
    """List all volunteers."""
    volunteers = await user_collection.find({"role": "VOLUNTEER"}).to_list(length=1000)
    for v in volunteers:
        v["_id"] = str(v["_id"])
    return volunteers

@router.post("/")
async def add_volunteer(req: VolunteerCreateReq, admin: dict = Depends(get_current_admin)):
    """Add a new volunteer and send invitation email."""
    existing = await user_collection.find_one({"email": req.email})
    if existing:
        if existing.get("role") == "VOLUNTEER":
            raise HTTPException(status_code=400, detail="User is already a volunteer.")
        
        # If normal user, upgrade to VOLUNTEER
        await user_collection.update_one(
            {"email": req.email},
            {"$set": {"role": "VOLUNTEER"}}
        )
    else:
        # Create new placeholder for Volunteer
        await user_collection.insert_one({
            "email": req.email,
            "role": "VOLUNTEER",
            "registration_complete": True,  # Allows immediate OTP login
        })
    
    EmailService.send_volunteer_invitation(req.email)
    return {"message": "Volunteer added successfully. Invitation email sent."}

@router.delete("/{email}")
async def remove_volunteer(email: str, admin: dict = Depends(get_current_admin)):
    """Remove volunteer role (demote to regular USER)."""
    user = await user_collection.find_one({"email": email, "role": "VOLUNTEER"})
    if not user:
        raise HTTPException(status_code=404, detail="Volunteer not found.")
        
    await user_collection.update_one(
        {"email": email},
        {"$set": {"role": "USER"}}
    )
    return {"message": "Volunteer removed successfully."}
