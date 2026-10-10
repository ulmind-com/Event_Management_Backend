from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId
from app.core.admin_auth import get_current_admin
from app.core.database import user_collection
from app.services.audit import log_admin_action
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
async def list_active_users(role: Optional[str] = None, admin=Depends(get_current_admin)):
    """List all ACTIVE users (not in recycle bin)."""
    q = {"is_deleted": {"$ne": True}}
    if role:
        q["role"] = role
    docs = await user_collection.find(q).sort("created_at", -1).to_list(length=500)
    return [clean(d) for d in docs]

@router.delete("/{email}")
async def soft_delete_user(email: str, admin=Depends(get_current_admin)):
    """Soft delete user (Send to Recycle Bin) and free up the email."""
    user = await user_collection.find_one({"email": email, "is_deleted": {"$ne": True}})
    if not user:
        raise HTTPException(status_code=404, detail="User not found or already deleted")
        
    # Mangle the email so the user can register a new account with the original email
    timestamp = int(datetime.now(timezone.utc).timestamp())
    mangled_email = f"deleted_{timestamp}_{email}"
    
    result = await user_collection.update_one(
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
        
    # Check if a new account has taken this email
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
