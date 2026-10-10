from fastapi import APIRouter, HTTPException
from app.schemas.user import EmailRequest, OTPVerifyRequest
from app.services.email import send_otp_email
from app.core.database import user_collection, otp_collection
from app.core.security import create_access_token
import random
import string
from datetime import datetime, timedelta, timezone

router = APIRouter()

def generate_otp(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))

@router.post("/login/step1")
async def judge_login_step1(req: EmailRequest):
    """Step 1: Check if Judge exists, generate and send OTP."""
    judge = await user_collection.find_one({"email": req.email, "role": "JUDGE"})
    if not judge:
        raise HTTPException(status_code=404, detail="Judge account not found. Please contact administration.")
    
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
async def judge_login_step2(req: OTPVerifyRequest):
    """Step 2: Verify OTP and login."""
    record = await otp_collection.find_one({"email": req.email})
    if not record or record.get("otp") != req.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
    
    # Clean up OTP
    await otp_collection.delete_one({"email": req.email})
    
    access_token = create_access_token(data={"sub": req.email, "type": "user"})
    return {
        "message": "Login successful",
        "access_token": access_token,
        "token_type": "bearer",
        "user_email": req.email,
        "role": "JUDGE"
    }
