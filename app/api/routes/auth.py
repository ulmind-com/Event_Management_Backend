from app.services.firebase_auth_service import FirebaseAuthService
from app.services.referral_service import ReferralService
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Header, status
from app.schemas.user import EmailRequest, OTPVerifyRequest, UserLoginReq, Token, GoogleLoginReq
from app.services.email import send_otp_email
from app.services.cloudinary_srv import upload_image
from app.core.database import user_collection, otp_collection
from app.core.security import create_access_token, decode_token, get_password_hash, verify_password
import random
import string
import logging
from datetime import datetime, timedelta, timezone, date
from typing import Optional

logger = logging.getLogger(__name__)
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
    if existing_user and existing_user.get("is_deleted"):
        raise HTTPException(status_code=403, detail="Account has been deleted.")
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
    
    if req.referral_code:
        try:
            await ReferralService.attribute_referral(req.email, req.referral_code)
        except Exception:
            pass

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
    digilocker_verification_id: Optional[str] = Form(None, description="DigiLocker verification ID if verified via DigiLocker"),
    authorization: str = Header(...)
):
    """Step 3: Setup password, profile details, direct DigiLocker KYC verification, and complete registration."""
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

    # ══════════════════════════════════════════════
    # DIRECT DIGILOCKER KYC VERIFICATION (ONLY)
    # ══════════════════════════════════════════════
    from app.core.database import kyc_verification_collection

    kyc_result = None
    kyc_doc_uri = None

    # Check if user already verified directly via DigiLocker
    kyc_query = {"user_email": email}
    if digilocker_verification_id:
        kyc_query = {"$or": [{"user_email": email}, {"verification_id": digilocker_verification_id}]}

    existing_kyc = await kyc_verification_collection.find_one(kyc_query)

    if existing_kyc and existing_kyc.get("status") == "VERIFIED":
        # Successfully verified directly via DigiLocker!
        aadhaar_masked = existing_kyc.get("aadhaar_masked")
        kyc_result = {
            "kyc_status": "VERIFIED",
            "aadhaar_masked": aadhaar_masked,
            "message": f"Aadhaar ({aadhaar_masked}) verified via DigiLocker. KYC complete.",
            "source": existing_kyc.get("verification_source", "DIGILOCKER"),
        }
        kyc_doc_uri = existing_kyc.get("digilocker_doc_uri") or f"digilocker://org.uidai/aadhaar/{aadhaar_masked}"

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
        "kyc_doc_uri": kyc_doc_uri,
        "role": "USER",
        "registration_complete": True,
        "updated_at": datetime.now(timezone.utc),
    }

    # Attach KYC status to user profile
    if kyc_result:
        update_data["kyc_status"] = kyc_result.get("kyc_status", "PENDING")
        update_data["kyc_aadhaar_masked"] = kyc_result.get("aadhaar_masked")
        if kyc_result.get("kyc_status") == "VERIFIED":
            update_data["kyc_verified_at"] = datetime.now(timezone.utc)
    else:
        update_data["kyc_status"] = "NOT_VERIFIED"
        update_data["kyc_aadhaar_masked"] = None

    await user_collection.update_one({"email": email}, {"$set": update_data})
    await otp_collection.delete_one({"email": email})

    # Generate user referral code and qualify referral attribution
    try:
        await ReferralService.get_or_create_user_referral_code(email)
        await ReferralService.qualify_and_reward_referral(email)
    except Exception:
        pass

    # Authoritative trigger: REGISTRATION_CONFIRMATION
    try:
        from app.services.notification_service import NotificationService
        from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
        from app.schemas.notification import NotificationCreate

        await NotificationService.create_notification(NotificationCreate(
            recipient_user_id=email,
            notification_type=NotificationType.REGISTRATION_CONFIRMATION,
            title="Welcome to Eventora!",
            message=f"Hi {name}, your registration is complete. Welcome to the premier event management platform!",
            resource_type="user",
            resource_id=email,
            action_url="/profile",
            priority=NotificationPriority.HIGH,
            sound_category=SoundCategory.SUCCESS,
            deduplication_key=f"user_welcome_{email}",
            source_module="auth",
        ))
    except Exception as e:
        logger.warning(f"Failed to create registration confirmation notification: {e}")

    # Return final access token for login
    access_token = create_access_token(data={"sub": email, "type": "user"})

    response_data = {
        "message": "Registration complete",
        "access_token": access_token,
        "token_type": "bearer",
        "user": update_data,
    }

    # Include KYC verification result in response
    if kyc_result:
        response_data["kyc_verification"] = {
            "status": kyc_result.get("kyc_status"),
            "aadhaar_masked": kyc_result.get("aadhaar_masked"),
            "message": kyc_result.get("message"),
        }

    return response_data

# ══════════════════════════════════════════════
# EXISTING USER LOGIN (Email + Password)
# ══════════════════════════════════════════════

@router.post("/login")
async def user_login(req: UserLoginReq):
    """Login for existing users using Email and Password. No OTP needed."""
    user = await user_collection.find_one({"email": req.email})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    if user.get("is_deleted"):
        raise HTTPException(status_code=403, detail="Account is disabled or deleted. Contact support.")
        
    if not user.get("registration_complete"):
        raise HTTPException(status_code=400, detail="Registration incomplete. Please register first.")
        
    if "password_hash" not in user:
        raise HTTPException(status_code=400, detail="Password not set for this account. Contact support.")
        
    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
        
    try:
        await ReferralService.get_or_create_user_referral_code(req.email)
        await ReferralService.qualify_and_reward_referral(req.email)
    except Exception:
        pass

    access_token = create_access_token(data={"sub": req.email, "type": "user"})
    return {
        "message": "Login successful",
        "access_token": access_token,
        "token_type": "bearer",
        "user_email": req.email,
        "role": user.get("role", "USER")
    }


# ══════════════════════════════════════════════
# GOOGLE AUTHENTICATION (Firebase ID Token)
# STRICTLY FOR 'USER' ROLE ONLY
# ══════════════════════════════════════════════

@router.post("/google-login")
async def google_login(req: GoogleLoginReq):
    """
    Login or register regular users via Google (Firebase ID Token).
    SECURITY: Only accounts with role 'USER' are permitted.
    Admin, Super Admin, and Coordinator roles are strictly prohibited from using Google Auth.
    """
    # 1. Verify Google Firebase ID Token
    google_user = await FirebaseAuthService.verify_firebase_id_token(req.id_token)
    email = google_user["email"]
    name = google_user.get("name") or email.split("@")[0]
    picture = google_user.get("picture")

    # 2. Check if user already exists
    existing_user = await user_collection.find_one({"email": email})

    if existing_user:
        # Check deletion status
        if existing_user.get("is_deleted"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is disabled or deleted. Contact support."
            )

        # STRICT ROLE CHECK: Only standard USER role permitted
        role = existing_user.get("role", "USER")
        if role != "USER":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Google authentication is only permitted for regular User accounts. Staff and Admin accounts must log in with their credentials."
            )

        # Update profile photo if missing and ensure registration is complete
        update_fields = {"updated_at": datetime.now(timezone.utc), "email_verified": True}
        if not existing_user.get("registration_complete"):
            update_fields["registration_complete"] = True
        if picture and not existing_user.get("profile_photo"):
            update_fields["profile_photo"] = picture

        await user_collection.update_one({"email": email}, {"$set": update_fields})
        user_doc = {**existing_user, **update_fields}
    else:
        # 3. Auto-register new USER
        new_user_data = {
            "email": email,
            "name": name,
            "profile_photo": picture,
            "role": "USER",
            "email_verified": True,
            "registration_complete": True,
            "auth_provider": "GOOGLE",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        await user_collection.insert_one(new_user_data)
        user_doc = new_user_data

        # Attribute referral if provided
        if req.referral_code:
            try:
                await ReferralService.attribute_referral(email, req.referral_code)
                await ReferralService.qualify_and_reward_referral(email)
            except Exception:
                pass

    # Ensure referral code exists for this user
    try:
        await ReferralService.get_or_create_user_referral_code(email)
        await ReferralService.qualify_and_reward_referral(email)
    except Exception:
        pass

    # 4. Generate Eventora JWT access token
    access_token = create_access_token(data={"sub": email, "type": "user"})

    return {
        "message": "Google login successful",
        "access_token": access_token,
        "token_type": "bearer",
        "user_email": email,
        "role": "USER",
        "user": {
            "email": email,
            "name": user_doc.get("name", name),
            "role": "USER",
            "profile_photo": user_doc.get("profile_photo", picture),
            "registration_complete": True
        }
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
        
    if user.get("is_deleted"):
        raise HTTPException(status_code=403, detail="Account is disabled or deleted. Contact support.")
    return user
