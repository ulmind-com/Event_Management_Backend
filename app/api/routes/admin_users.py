from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, List
from datetime import datetime, timezone
from bson import ObjectId
from app.core.admin_auth import get_current_admin
from app.core.database import user_collection
from app.services.audit import log_admin_action
from app.services.user_activity_service import UserActivityService
import pymongo

router = APIRouter()

def clean(doc):
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

def oid(id_str):
    try:
        return ObjectId(id_str)
    except:
        raise HTTPException(status_code=400, detail="Invalid ID format")

async def ensure_ttl_index():
    try:
        await user_collection.create_index(
            [("deleted_at", pymongo.ASCENDING)],
            expireAfterSeconds=604800,
            partialFilterExpression={"is_deleted": True}
        )
    except Exception as e:
        pass

@router.get("/")
async def list_active_users(
    role: Optional[str] = None,
    search: Optional[str] = None,
    admin=Depends(get_current_admin)
):
    """List all ACTIVE users (not in recycle bin) with optional search."""
    q = {"is_deleted": {"$ne": True}}
    if role:
        q["role"] = role
    if search:
        q["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}}
        ]
    docs = await user_collection.find(q).sort("created_at", -1).to_list(length=500)
    return [clean(d) for d in docs]

@router.delete("/{email}")
async def soft_delete_user(email: str, admin=Depends(get_current_admin)):
    """Soft delete user (Send to Recycle Bin) and free up the email."""
    user = await user_collection.find_one({"email": email, "is_deleted": {"$ne": True}})
    if not user:
        raise HTTPException(status_code=404, detail="User not found or already deleted")
        
    timestamp = int(datetime.now(timezone.utc).timestamp())
    mangled_email = f"deleted_{timestamp}_{email}"
    
    await user_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "email": mangled_email,
            "original_email": email, 
            "is_deleted": True, 
            "deleted_at": datetime.now(timezone.utc)
        }}
    )
    
    await log_admin_action(admin["email"], "soft_delete_user", "user", email)
    return {"message": "User moved to recycle bin. Their email has been freed for new registration."}

@router.get("/recycle-bin")
async def list_deleted_users(admin=Depends(get_current_admin)):
    """List users currently in the recycle bin."""
    await ensure_ttl_index()
    docs = await user_collection.find({"is_deleted": True}).sort("deleted_at", -1).to_list(length=500)
    return [clean(d) for d in docs]

@router.post("/recycle-bin/{user_id}/restore")
async def restore_user(user_id: str, admin=Depends(get_current_admin)):
    """Restore a user from the recycle bin."""
    user = await user_collection.find_one({"_id": oid(user_id), "is_deleted": True})
    if not user:
        raise HTTPException(status_code=404, detail="User not found in recycle bin")
        
    original_email = user.get("original_email")
    if not original_email:
        raise HTTPException(status_code=400, detail="Original email unknown, cannot restore.")
        
    conflict = await user_collection.find_one({"email": original_email, "is_deleted": {"$ne": True}})
    if conflict:
        raise HTTPException(
            status_code=400, 
            detail="The user has already created a NEW active account with this email. You cannot restore the old data unless the new account is deleted."
        )
        
    await user_collection.update_one(
        {"_id": oid(user_id)},
        {"$set": {"email": original_email, "is_deleted": False}, "$unset": {"deleted_at": "", "original_email": ""}}
    )
        
    await log_admin_action(admin["email"], "restore_user", "user", original_email)
    return {"message": "User successfully restored from recycle bin. They can now log in to their old account."}

@router.delete("/recycle-bin/{user_id}/force")
async def force_delete_user(user_id: str, admin=Depends(get_current_admin)):
    """Permanently delete a user from the recycle bin immediately."""
    result = await user_collection.delete_one({"_id": oid(user_id), "is_deleted": True})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found in recycle bin")
        
    await log_admin_action(admin["email"], "force_delete_user", "user", user_id)
    return {"message": "User permanently deleted."}

# ── Complete Admin User 360° Activity & Profile Endpoints ──

@router.get("/{user_id}/profile-summary")
async def get_admin_user_profile_summary(user_id: str, admin=Depends(get_current_admin)):
    """Complete 360° profile summary with registration, participation, and completion metrics."""
    res = await UserActivityService.get_user_profile_summary(user_id)
    if not res:
        raise HTTPException(status_code=404, detail="User not found")
    return res

@router.get("/{user_id}/activity")
async def get_admin_user_activity_timeline(
    user_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    admin=Depends(get_current_admin)
):
    """Unified chronological activity timeline across all Eventora activities."""
    timeline = await UserActivityService.get_user_activity_timeline(user_id, limit=limit)
    return {"user_id": user_id, "timeline": timeline, "total": len(timeline)}

@router.get("/{user_id}/events")
async def get_admin_user_events_history(user_id: str, admin=Depends(get_current_admin)):
    """Complete event registration and attendance history."""
    events = await UserActivityService.get_user_events(user_id)
    return {"user_id": user_id, "events": events, "total": len(events)}

@router.get("/{user_id}/competitions")
async def get_admin_user_competitions_history(user_id: str, admin=Depends(get_current_admin)):
    """Complete competition registration, submission, official rank, and award history."""
    competitions = await UserActivityService.get_user_competitions(user_id)
    return {"user_id": user_id, "competitions": competitions, "total": len(competitions)}

@router.get("/{user_id}/courses")
async def get_admin_user_courses_history(user_id: str, admin=Depends(get_current_admin)):
    """Complete course enrollment and completion history."""
    courses = await UserActivityService.get_user_courses(user_id)
    return {"user_id": user_id, "courses": courses, "total": len(courses)}

@router.get("/{user_id}/workshops")
async def get_admin_user_workshops_history(user_id: str, admin=Depends(get_current_admin)):
    """Complete workshop enrollment and completion history."""
    workshops = await UserActivityService.get_user_workshops(user_id)
    return {"user_id": user_id, "workshops": workshops, "total": len(workshops)}

@router.get("/{user_id}/badges")
async def get_admin_user_badges_history(user_id: str, admin=Depends(get_current_admin)):
    """Complete badges earned by the user including Top Participation qualification."""
    badges = await UserActivityService.get_user_badges(user_id)
    return {"user_id": user_id, "badges": badges, "total": len(badges)}

@router.get("/{user_id}/certificates")
async def get_admin_user_certificates_history(user_id: str, admin=Depends(get_current_admin)):
    """Complete certificates issued to the user (Participation and Award separate)."""
    certs = await UserActivityService.get_user_certificates(user_id)
    return {"user_id": user_id, "certificates": certs, "total": len(certs)}

@router.get("/{user_id}/achievements")
async def get_admin_user_achievements(user_id: str, admin=Depends(get_current_admin)):
    """Consolidated achievements, awards, and milestones."""
    achievements = await UserActivityService.get_user_achievements(user_id)
    return {"user_id": user_id, "achievements": achievements}

@router.get("/{user_id}/activity-summary")
async def get_admin_user_activity_summary(user_id: str, admin=Depends(get_current_admin)):
    """Aggregated activity summary statistics."""
    res = await UserActivityService.get_user_profile_summary(user_id)
    if not res:
        raise HTTPException(status_code=404, detail="User not found")
    return res.get("activity_summary", {})

@router.get("/{user_id}/vip-bookings")
async def get_admin_user_vip_bookings(user_id: str, admin=Depends(get_current_admin)):
    """
    Super Admin inspects user's VIP bookings and passes history.
    """
    from app.core.database import vip_booking_collection, vip_pass_collection
    from app.services.vip_service import serialize_mongo
    from app.services.user_activity_service import UserActivityService

    user = await UserActivityService.get_user_doc(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    email = user.get("email")

    bookings = await vip_booking_collection.find({"user_email": email}).sort("booked_at", -1).to_list(length=100)
    passes = await vip_pass_collection.find({"user_email": email}).sort("issued_at", -1).to_list(length=100)
    
    for b in bookings:
        b["booking_id"] = str(b["_id"])
        b["_id"] = str(b["_id"])
    for p in passes:
        p["pass_id"] = str(p["_id"])
        p["_id"] = str(p["_id"])

    return serialize_mongo({
        "user_id": user_id,
        "email": email,
        "total_bookings": len(bookings),
        "confirmed_bookings": sum(1 for b in bookings if b.get("booking_status") == "CONFIRMED"),
        "pending_bookings": sum(1 for b in bookings if b.get("booking_status") == "PENDING_PAYMENT"),
        "cancelled_bookings": sum(1 for b in bookings if b.get("booking_status") == "CANCELLED"),
        "active_passes": sum(1 for p in passes if p.get("status") == "VALID"),
        "revoked_passes": sum(1 for p in passes if p.get("status") == "REVOKED"),
        "expired_passes": sum(1 for p in passes if p.get("status") == "EXPIRED"),
        "bookings": bookings,
        "passes": passes
    })
