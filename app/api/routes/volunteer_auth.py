from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.schemas.user import (
    EmailRequest,
    OTPVerifyRequest,
    UserLoginReq,
    SetPasswordReq,
    ChangePasswordReq,
)
from app.services.email import send_otp_email
from app.core.database import user_collection, otp_collection
from app.core.security import (
    create_access_token,
    decode_token,
    get_password_hash,
    verify_password,
)
import random
import string
from datetime import datetime, timedelta, timezone

router = APIRouter()
security = HTTPBearer()

def generate_otp(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))

@router.post("/login/step1")
async def volunteer_login_step1(req: EmailRequest):
    """Step 1: Check if Volunteer exists, generate and send OTP."""
    volunteer = await user_collection.find_one({"email": req.email, "role": "VOLUNTEER"})
    if not volunteer:
        raise HTTPException(status_code=404, detail="Volunteer account not found. Please contact administration.")
    
    otp_code = generate_otp()
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    
    await otp_collection.update_one(
        {"email": req.email},
        {"$set": {"otp": otp_code, "expireAt": expire_at, "verified": False}},
        upsert=True
    )
    
    send_otp_email(req.email, otp_code)
    return {"message": "OTP sent to your email successfully."}

@router.post("/login/step2")
async def volunteer_login_step2(req: OTPVerifyRequest):
    """Step 2: Verify OTP and login. Returns has_password flag to prompt password setup."""
    record = await otp_collection.find_one({"email": req.email})
    if not record or record.get("otp") != req.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
    
    volunteer = await user_collection.find_one({"email": req.email, "role": "VOLUNTEER"})
    if not volunteer:
        raise HTTPException(status_code=404, detail="Volunteer account not found. Please contact administration.")
    
    await otp_collection.delete_one({"email": req.email})
    
    has_password = bool(volunteer.get("password_hash"))
    access_token = create_access_token(data={"sub": req.email, "type": "user"})
    return {
        "message": "Login successful",
        "access_token": access_token,
        "token_type": "bearer",
        "user_email": req.email,
        "role": "VOLUNTEER",
        "has_password": has_password
    }

@router.post("/set-password")
async def volunteer_set_password(
    req: SetPasswordReq,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Set password for Volunteer after first login (or when authenticated)."""
    token = credentials.credentials
    payload = decode_token(token)
    if not payload or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
        
    email = payload.get("sub")
    volunteer = await user_collection.find_one({"email": email, "role": "VOLUNTEER"})
    if not volunteer:
        raise HTTPException(status_code=403, detail="Not authorized as Volunteer")
        
    if req.confirm_password is not None and req.password != req.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
        
    hashed_pwd = get_password_hash(req.password)
    await user_collection.update_one(
        {"email": email},
        {
            "$set": {
                "password_hash": hashed_pwd,
                "has_password": True,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    return {
        "message": "Password set successfully.",
        "has_password": True,
    }

@router.post("/change-password")
async def volunteer_change_password(
    req: ChangePasswordReq,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Change password for Volunteer by verifying current password."""
    token = credentials.credentials
    payload = decode_token(token)
    if not payload or not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
        
    email = payload.get("sub")
    volunteer = await user_collection.find_one({"email": email, "role": "VOLUNTEER"})
    if not volunteer:
        raise HTTPException(status_code=403, detail="Not authorized as Volunteer")
        
    if not volunteer.get("password_hash") or not verify_password(req.current_password, volunteer["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
        
    if req.confirm_password is not None and req.new_password != req.confirm_password:
        raise HTTPException(status_code=400, detail="New passwords do not match")
        
    if verify_password(req.new_password, volunteer["password_hash"]):
        raise HTTPException(status_code=400, detail="New password must be different from current password")
        
    hashed_pwd = get_password_hash(req.new_password)
    await user_collection.update_one(
        {"email": email},
        {
            "$set": {
                "password_hash": hashed_pwd,
                "has_password": True,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    return {"message": "Password changed successfully."}

@router.post("/login/password")
@router.post("/login")
async def volunteer_login_password(req: UserLoginReq):
    """Direct login with Email and Password for Volunteers."""
    volunteer = await user_collection.find_one({"email": req.email, "role": "VOLUNTEER"})
    if not volunteer:
        raise HTTPException(status_code=404, detail="Volunteer account not found. Please contact administration.")
        
    if not volunteer.get("password_hash"):
        raise HTTPException(
            status_code=400,
            detail="Password has not been set yet. Please log in using OTP first and set your password.",
        )
        
    if not verify_password(req.password, volunteer["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
        
    access_token = create_access_token(data={"sub": req.email, "type": "user"})
    return {
        "message": "Login successful",
        "access_token": access_token,
        "token_type": "bearer",
        "user_email": req.email,
        "role": "VOLUNTEER",
        "has_password": True,
    }
