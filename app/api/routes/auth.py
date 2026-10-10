from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Header, status
from app.schemas.user import EmailRequest, OTPVerifyRequest, UserLoginReq, Token
from app.services.email import send_otp_email
from app.services.cloudinary_srv import upload_image
from app.core.database import user_collection, otp_collection
from app.core.security import create_access_token, decode_token, get_password_hash, verify_password
import random
import string
from datetime import datetime, timedelta, timezone, date
from typing import Optional

router = APIRouter()

def generate_otp(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))

def calculate_age(dob: date) -> int:
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

def parse_date(date_str: str) -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            pass
    raise ValueError("Invalid date format")

# ══════════════════════════════════════════════
# NEW USER REGISTRATION (3 Steps)
# ══════════════════════════════════════════════

@router.post("/register/step1")
async def register_step1(req: EmailRequest):
    """Step 1: Check if user exists, if not send OTP."""
    existing_user = await user_collection.find_one({"email": req.email})
    if existing_user and existing_user.get("registration_complete"):
        raise HTTPException(status_code=400, detail="User already exists. Please login.")
    
    otp_code = generate_otp()
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    
    await otp_collection.update_one(
        {"email": req.email},
        {"$set": {"otp": otp_code, "expireAt": expire_at, "verified": False}},
        upsert=True
    )
    
    # Create placeholder user if doesn't exist at all
    await user_collection.update_one(
        {"email": req.email},
        {"$setOnInsert": {"email": req.email, "email_verified": False, "registration_complete": False, "created_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    
    send_otp_email(req.email, otp_code)
    return {"message": "OTP sent successfully."}

@router.post("/register/step2")
async def register_step2(req: OTPVerifyRequest):
    """Step 2: Verify OTP and return a temporary registration token."""
    record = await otp_collection.find_one({"email": req.email})
    if not record or record.get("otp") != req.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
    
    await otp_collection.update_one({"email": req.email}, {"$set": {"verified": True}})
    await user_collection.update_one({"email": req.email}, {"$set": {"email_verified": True}})
    
    reg_token = create_access_token(
        data={"sub": req.email, "type": "registration"},
        expires_delta=timedelta(minutes=30)
    )
    return {"message": "OTP verified.", "registration_token": reg_token}

@router.post("/register/step3")
async def register_step3(
    password: str = Form(...),
    confirm_password: str = Form(...),
    name: str = Form(...),
    phone_number: int = Form(...),
    date_of_birth: str = Form(..., description="Date of birth (YYYY-MM-DD)"),
    address: str = Form(...),
    parent_phone_number: Optional[int] = Form(None),
    profile_photo: Optional[UploadFile] = File(None),
    govt_id_proof: UploadFile = File(...),
    authorization: str = Header(...)
):
    """Step 3: Setup password, profile details, and complete registration."""
    token = authorization.replace("Bearer ", "").strip()
    payload = decode_token(token)
    if not payload or payload.get("type") != "registration":
        raise HTTPException(status_code=401, detail="Invalid registration token")
    email = payload["sub"]
    
    if password != confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    try:
        dob = parse_date(date_of_birth)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format.")

    final_age = calculate_age(dob)

    govt_id_content = await govt_id_proof.read()
    govt_id_url = upload_image(govt_id_content, folder="eventora/gov_ids")
    if not govt_id_url:
        raise HTTPException(status_code=500, detail="Failed to upload ID")

    profile_url = None
    if profile_photo:
        profile_content = await profile_photo.read()
        if len(profile_content) > 0:
            profile_url = upload_image(profile_content, folder="eventora/profiles")

    # Hash the password!
    hashed_pwd = get_password_hash(password)

    update_data = {
        "password_hash": hashed_pwd,
        "name": name,
        "phone_number": phone_number,
        "date_of_birth": dob.strftime("%Y-%m-%d"),
        "age": final_age,
        "address": address,
        "parent_phone_number": parent_phone_number,
        "profile_photo": profile_url,
        "govt_id_proof": govt_id_url,
        "role": "USER",
        "registration_complete": True,
        "updated_at": datetime.now(timezone.utc),
    }

    await user_collection.update_one({"email": email}, {"$set": update_data})
    await otp_collection.delete_one({"email": email})

    # Return final access token for login
    access_token = create_access_token(data={"sub": email, "type": "user"})
    return {
        "message": "Registration complete",
        "access_token": access_token,
        "token_type": "bearer",
        "user": update_data
    }

# ══════════════════════════════════════════════
# EXISTING USER LOGIN (Email + Password)
# ══════════════════════════════════════════════

@router.post("/login")
async def user_login(req: UserLoginReq):
    """Login for existing users using Email and Password. No OTP needed."""
    user = await user_collection.find_one({"email": req.email})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if not user.get("registration_complete"):
        raise HTTPException(status_code=400, detail="Registration incomplete. Please register first.")
        
    if "password_hash" not in user:
        raise HTTPException(status_code=400, detail="Password not set for this account. Contact support.")
        
    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
        
    access_token = create_access_token(data={"sub": req.email, "type": "user"})
    return {
        "message": "Login successful",
        "access_token": access_token,
        "token_type": "bearer",
        "user_email": req.email,
        "role": user.get("role", "USER")
    }

# ══════════════════════════════════════════════
# PROFILE
# ══════════════════════════════════════════════

@router.get("/me")
async def get_user_profile(authorization: str = Header(...)):
    token = authorization.replace("Bearer ", "").strip()
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = await user_collection.find_one({"email": payload["sub"]}, {"_id": 0, "password_hash": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
