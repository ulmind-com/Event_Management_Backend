from fastapi import APIRouter, HTTPException, status, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.schemas.admin import AdminLoginReq, AdminChangePasswordReq, AdminChangeEmailReq
from app.core.database import admin_collection
from app.core.config import settings
from app.core.security import get_password_hash, verify_password, create_access_token
from app.core.admin_auth import get_current_admin
from app.services.audit import log_admin_action
from fastapi import Depends
from datetime import datetime, timezone, timedelta

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

@router.post("/bootstrap")
@limiter.limit("3/hour")
async def bootstrap_admin(request: Request):
    """One-time Super Admin initialization from .env credentials."""
    email = settings.SUPER_ADMIN_EMAIL
    password = settings.SUPER_ADMIN_PASSWORD

    if not email or not password:
        raise HTTPException(status_code=500, detail="SUPER_ADMIN_EMAIL and SUPER_ADMIN_PASSWORD must be set in .env")

    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # Check if admin already exists
    existing = await admin_collection.find_one({"role": "super_admin"})
    if existing:
        raise HTTPException(status_code=409, detail="Super Admin already initialized. Use sync procedure to update.")

    hashed_pw = get_password_hash(password)
    admin_doc = {
        "email": email,
        "password_hash": hashed_pw,
        "role": "super_admin",
        "created_at": datetime.now(timezone.utc),
    }
    await admin_collection.insert_one(admin_doc)
    await log_admin_action(email, "bootstrap", "admin", outcome="success")
    return {"message": "Super Admin initialized successfully."}

@router.post("/login")
@limiter.limit("5/minute")
async def admin_login(request: Request, req: AdminLoginReq):
    """Admin login with email + password. Rate-limited."""
    admin = await admin_collection.find_one({"email": req.email, "role": "super_admin"})
    if not admin:
        await log_admin_action(req.email, "login_attempt", "admin", outcome="failure", metadata={"reason": "not_found"})
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(req.password, admin["password_hash"]):
        await log_admin_action(req.email, "login_attempt", "admin", outcome="failure", metadata={"reason": "wrong_password"})
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(
        data={"sub": req.email, "type": "admin", "role": "super_admin"},
        expires_delta=timedelta(minutes=settings.ADMIN_TOKEN_EXPIRE_MINUTES)
    )
    await log_admin_action(req.email, "login", "admin", outcome="success")
    return {"access_token": token, "token_type": "bearer"}

@router.get("/me")
async def get_admin_info(admin: dict = Depends(get_current_admin)):
    """Get current admin session info."""
    return {"email": admin["email"], "role": admin["role"], "created_at": str(admin.get("created_at", ""))}

@router.post("/logout")
async def admin_logout(admin: dict = Depends(get_current_admin)):
    """Logout (client should discard token)."""
    await log_admin_action(admin["email"], "logout", "admin", outcome="success")
    return {"message": "Logged out successfully. Discard your token."}


@router.post("/change-password")
@limiter.limit("5/minute")
async def admin_change_password(
    request: Request,
    req: AdminChangePasswordReq,
    admin: dict = Depends(get_current_admin),
):
    """Change the signed-in admin's password after verifying the current one."""
    if not verify_password(req.current_password, admin["password_hash"]):
        await log_admin_action(admin["email"], "change_password", "admin", outcome="failure", metadata={"reason": "wrong_current"})
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    if verify_password(req.new_password, admin["password_hash"]):
        raise HTTPException(status_code=400, detail="New password must be different from the current one")

    await admin_collection.update_one(
        {"_id": admin["_id"]},
        {"$set": {"password_hash": get_password_hash(req.new_password), "updated_at": datetime.now(timezone.utc)}},
    )
    await log_admin_action(admin["email"], "change_password", "admin", outcome="success")
    return {"message": "Password changed successfully."}


@router.post("/change-email")
@limiter.limit("5/minute")
async def admin_change_email(
    request: Request,
    req: AdminChangeEmailReq,
    admin: dict = Depends(get_current_admin),
):
    """Change the signed-in admin's login email; returns a fresh token (email is the JWT subject)."""
    if not verify_password(req.current_password, admin["password_hash"]):
        await log_admin_action(admin["email"], "change_email", "admin", outcome="failure", metadata={"reason": "wrong_current"})
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    new_email = req.new_email.strip().lower()
    if new_email == admin["email"].lower():
        raise HTTPException(status_code=400, detail="New email must be different from the current one")

    if await admin_collection.find_one({"email": new_email}):
        raise HTTPException(status_code=409, detail="That email is already in use")

    await admin_collection.update_one(
        {"_id": admin["_id"]},
        {"$set": {"email": new_email, "updated_at": datetime.now(timezone.utc)}},
    )
    await log_admin_action(admin["email"], "change_email", "admin", outcome="success", metadata={"new_email": new_email})

    token = create_access_token(
        data={"sub": new_email, "type": "admin", "role": admin.get("role", "super_admin")},
        expires_delta=timedelta(minutes=settings.ADMIN_TOKEN_EXPIRE_MINUTES),
    )
    return {"message": "Email changed successfully.", "access_token": token, "token_type": "bearer", "email": new_email}
