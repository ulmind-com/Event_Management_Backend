from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone
from app.core.admin_auth import get_current_admin
from app.core.database import user_collection
from app.services.audit import log_admin_action
import pymongo

router = APIRouter()

def clean(doc):
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

# Ensure TTL index is created for recycle bin expiration (7 days = 604800 seconds)
# In production, this might run once on startup, but it's safe to call create_index repeatedly.

# We will create the index lazily on the first request to the recycle bin,
# or it should be added to the app's startup event in main.py.
# For safety in this module:
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
async def list_active_users(role: Optional[str] = None, admin=Depends(get_current_admin)):
    """List all ACTIVE users (not in recycle bin)."""
    q = {"is_deleted": {"$ne": True}}
    if role:
        q["role"] = role
    docs = await user_collection.find(q).sort("created_at", -1).to_list(length=500)
    return [clean(d) for d in docs]

@router.delete("/{email}")
async def soft_delete_user(email: str, admin=Depends(get_current_admin)):
    """Soft delete user (Send to Recycle Bin)."""
    result = await user_collection.update_one(
        {"email": email, "is_deleted": {"$ne": True}},
        {"$set": {"is_deleted": True, "deleted_at": datetime.now(timezone.utc)}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found or already in recycle bin")
        
    await log_admin_action(admin["email"], "soft_delete_user", "user", email)
    return {"message": "User moved to recycle bin. They will be permanently deleted in 7 days."}

@router.get("/recycle-bin")
async def list_deleted_users(admin=Depends(get_current_admin)):
    """List users currently in the recycle bin."""
    await ensure_ttl_index()
    docs = await user_collection.find({"is_deleted": True}).sort("deleted_at", -1).to_list(length=500)
    return [clean(d) for d in docs]

@router.post("/recycle-bin/{email}/restore")
async def restore_user(email: str, admin=Depends(get_current_admin)):
    """Restore a user from the recycle bin."""
    result = await user_collection.update_one(
        {"email": email, "is_deleted": True},
        {"$set": {"is_deleted": False}, "$unset": {"deleted_at": ""}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found in recycle bin")
        
    await log_admin_action(admin["email"], "restore_user", "user", email)
    return {"message": "User successfully restored from recycle bin. They can now log in normally."}

@router.delete("/recycle-bin/{email}/force")
async def force_delete_user(email: str, admin=Depends(get_current_admin)):
    """Permanently delete a user from the recycle bin immediately."""
    result = await user_collection.delete_one({"email": email, "is_deleted": True})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found in recycle bin")
        
    await log_admin_action(admin["email"], "force_delete_user", "user", email)
    return {"message": "User permanently deleted."}
