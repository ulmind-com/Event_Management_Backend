import random
import string
import logging
import re
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Tuple, List
from app.services.firebase_auth_service import FirebaseAuthService
from app.services.referral_service import ReferralService
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Header, status, Depends
from app.schemas.user import (
    EmailRequest,
    OTPVerifyRequest,
    UserLoginReq,
    Token,
    GoogleLoginReq,
    CompleteProfileReq,
    ForgotPasswordReq,
    ForgotPasswordVerifyReq,
    ResetPasswordWithTokenReq,
    DirectResetPasswordReq,
)
from app.services.email import (
    send_otp_email,
    send_password_reset_otp_email,
    send_password_reset_success_email,
    EmailService,
)
from app.services.cloudinary_srv import upload_image
from app.core.database import user_collection, otp_collection
from app.core.security import create_access_token, decode_token, get_password_hash, verify_password

from app.core.profile_lifecycle import (
    calculate_age,
    parse_date,
    check_profile_completion,
    get_profile_form_schema,
    enrich_user_with_profile_status,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def generate_otp(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


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
    phone_number: str = Form(...),
    date_of_birth: str = Form(..., description="Date of birth (YYYY-MM-DD)"),
    address: str = Form(...),
    parent_phone_number: str = Form(...),
    profile_photo: Optional[UploadFile] = File(None),
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

    cleaned_phone = re.sub(r"\D", "", str(phone_number).strip())
    if len(cleaned_phone) < 10:
        raise HTTPException(status_code=400, detail="Mobile number must be at least 10 digits")

    cleaned_parent_phone = re.sub(r"\D", "", str(parent_phone_number).strip())
    if len(cleaned_parent_phone) < 10:
        raise HTTPException(status_code=400, detail="Parent/Guardian phone number must be at least 10 digits")

    try:
        dob = parse_date(date_of_birth)
        if not dob:
            raise ValueError()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date format.")

    final_age = calculate_age(dob)

    # Check if user already completed KYC verification
    from app.core.database import kyc_verification_collection

    kyc_result = None
    kyc_doc_uri = None

    existing_kyc = await kyc_verification_collection.find_one({"user_email": email})

    if existing_kyc and existing_kyc.get("status") == "VERIFIED":
        aadhaar_masked = existing_kyc.get("aadhaar_masked")
        kyc_result = {
            "kyc_status": "VERIFIED",
            "aadhaar_masked": aadhaar_masked,
            "message": f"Govt ID ({aadhaar_masked}) verified. KYC complete.",
            "source": existing_kyc.get("verification_source", "AADHAAR_KYC"),
        }
        kyc_doc_uri = existing_kyc.get("kyc_doc_uri")

    profile_url = None
    if profile_photo:
        profile_content = await profile_photo.read()
        if len(profile_content) > 0:
            profile_url = upload_image(
                profile_content,
                folder="eventora/profiles",
                filename=profile_photo.filename,
            )
            if not profile_url:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to upload profile photo to cloud storage. Please try again."
                )

    # Hash the password!
    hashed_pwd = get_password_hash(password)

    update_data = {
        "password_hash": hashed_pwd,
        "name": name.strip(),
        "phone_number": cleaned_phone,
        "date_of_birth": dob.strftime("%Y-%m-%d"),
        "age": final_age,
        "address": address.strip(),
        "parent_phone_number": cleaned_parent_phone,
        "profile_photo": profile_url,
        "kyc_doc_uri": kyc_doc_uri,
        "role": "USER",
        "registration_complete": True,
        "is_profile_completed": True,
        "profile_completed": True,
        "first_time_login": False,
        "profile_completed_at": datetime.now(timezone.utc),
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

    safe_user = {**update_data}
    if "password_hash" in safe_user:
        del safe_user["password_hash"]

    response_data = {
        "message": "Registration complete",
        "access_token": access_token,
        "token_type": "bearer",
        "registration_complete": True,
        "is_profile_completed": True,
        "profile_completed": True,
        "requires_profile_completion": False,
        "missing_fields": [],
        "redirect_url": "/dashboard",
        "user": safe_user,
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
        
    if "password_hash" not in user:
        raise HTTPException(
            status_code=400,
            detail="Password not set for this account. If you signed in with Google, please use Google sign-in."
        )
        
    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
        
    try:
        await ReferralService.get_or_create_user_referral_code(req.email)
        await ReferralService.qualify_and_reward_referral(req.email)
    except Exception:
        pass

    is_completed, missing = check_profile_completion(user)
    form_schema = get_profile_form_schema(user)

    # Sync completion flag in database
    await user_collection.update_one(
        {"email": req.email},
        {"$set": {
            "registration_complete": is_completed,
            "is_profile_completed": is_completed,
            "profile_completed": is_completed,
            "first_time_login": not is_completed,
            "updated_at": datetime.now(timezone.utc)
        }}
    )

    access_token = create_access_token(data={"sub": req.email, "type": "user"})
    return {
        "message": "Login successful" if is_completed else "Login successful. Please complete your profile details.",
        "access_token": access_token,
        "token_type": "bearer",
        "user_email": req.email,
        "role": user.get("role", "USER"),
        "registration_complete": is_completed,
        "is_profile_completed": is_completed,
        "profile_completed": is_completed,
        "requires_profile_completion": not is_completed,
        "prompt_profile_form": not is_completed,
        "first_time_login": not is_completed,
        "missing_fields": missing,
        "next_step": "COMPLETE_PROFILE" if not is_completed else "DASHBOARD",
        "redirect_url": "/complete-profile" if not is_completed else "/dashboard",
        "profile_form": form_schema,
        "user": {
            "email": req.email,
            "name": user.get("name"),
            "role": user.get("role", "USER"),
            "phone_number": user.get("phone_number"),
            "date_of_birth": user.get("date_of_birth"),
            "address": user.get("address"),
            "parent_phone_number": user.get("parent_phone_number"),
            "profile_photo": user.get("profile_photo"),
            "registration_complete": is_completed,
            "is_profile_completed": is_completed,
            "profile_completed": is_completed,
            "requires_profile_completion": not is_completed,
            "prompt_profile_form": not is_completed,
            "first_time_login": not is_completed,
            "missing_fields": missing,
            "next_step": "COMPLETE_PROFILE" if not is_completed else "DASHBOARD",
            "redirect_url": "/complete-profile" if not is_completed else "/dashboard",
            "profile_form": form_schema,
        }
    }


# ══════════════════════════════════════════════
# USER FORGOT & RESET PASSWORD (USER ROLE ONLY)
# ══════════════════════════════════════════════

@router.post("/forgot-password")
@router.post("/forgot-password/request")
async def forgot_password_request(req: ForgotPasswordReq):
    """
    Step 1: Request password reset OTP.
    Sends an ultra-secure 6-digit verification passcode to the user's registered email.
    Strictly for standard USER accounts.
    """
    email = req.email.strip().lower()
    user = await user_collection.find_one({"email": email})

    if not user:
        raise HTTPException(
            status_code=404,
            detail="No account found with this email address. Please register or verify the email entered."
        )

    if user.get("is_deleted"):
        raise HTTPException(
            status_code=403,
            detail="Account is disabled or deleted. Please contact support."
        )

    # Security check: Ensure this is a regular user account
    if (user.get("role") or "USER").upper() != "USER":
        raise HTTPException(
            status_code=403,
            detail="Password reset on this portal is reserved for user accounts. Staff should use their administrative portal."
        )

    # Google-only accounts do not use local passwords
    if user.get("auth_provider") == "GOOGLE" and not user.get("password_hash"):
        raise HTTPException(
            status_code=400,
            detail="This account was registered using Google Sign-In and does not use a separate password. Please sign in using 'Continue with Google'."
        )

    otp_code = generate_otp()
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    # Save reset OTP with dedicated purpose tag
    await otp_collection.update_one(
        {"email": email, "purpose": "password_reset"},
        {"$set": {
            "email": email,
            "otp": otp_code,
            "purpose": "password_reset",
            "expireAt": expire_at,
            "verified": False,
            "attempts": 0,
            "created_at": datetime.now(timezone.utc)
        }},
        upsert=True
    )

    user_name = user.get("name") or user.get("full_name") or email.split("@")[0]
    email_sent = send_password_reset_otp_email(email, otp_code, user_name)

    logger.info(f"Password reset OTP requested for user '{email}' (email_sent={email_sent})")

    return {
        "message": "Password reset code sent to your email. It will expire in 10 minutes.",
        "email": email,
        "expires_in_minutes": 10
    }


@router.post("/forgot-password/verify-otp")
async def forgot_password_verify_otp(req: ForgotPasswordVerifyReq):
    """
    Step 2: Verify the 6-digit password reset OTP.
    Returns a secure, short-lived reset token (valid for 15 minutes)
    that must be presented to change the password.
    """
    email = req.email.strip().lower()
    otp_code = req.otp.strip()

    record = await otp_collection.find_one({"email": email, "purpose": "password_reset"})
    if not record:
        raise HTTPException(
            status_code=400,
            detail="No active password reset request found. Please request a new code."
        )

    # Check expiration
    expire_at = record.get("expireAt")
    if expire_at:
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expire_at:
            await otp_collection.delete_one({"email": email, "purpose": "password_reset"})
            raise HTTPException(
                status_code=400,
                detail="Reset code has expired. Please request a new code."
            )

    # Check brute force attempts
    attempts = record.get("attempts", 0)
    if attempts >= 5:
        await otp_collection.delete_one({"email": email, "purpose": "password_reset"})
        raise HTTPException(
            status_code=400,
            detail="Too many invalid attempts. For your security, this code has been revoked. Please request a new one."
        )

    if record.get("otp") != otp_code:
        await otp_collection.update_one(
            {"email": email, "purpose": "password_reset"},
            {"$inc": {"attempts": 1}}
        )
        remaining = 4 - attempts
        raise HTTPException(
            status_code=400,
            detail=f"Invalid verification code. {remaining} attempt(s) remaining."
        )

    # Mark as verified
    await otp_collection.update_one(
        {"email": email, "purpose": "password_reset"},
        {"$set": {"verified": True, "verified_at": datetime.now(timezone.utc)}}
    )

    # Generate temporary reset token
    reset_token = create_access_token(
        data={"sub": email, "type": "password_reset"},
        expires_delta=timedelta(minutes=15)
    )

    return {
        "message": "Verification code confirmed. You can now set your new password.",
        "email": email,
        "reset_token": reset_token,
        "token_type": "bearer",
        "expires_in_minutes": 15
    }


@router.post("/forgot-password/reset-password")
async def forgot_password_reset(req: ResetPasswordWithTokenReq):
    """
    Step 3: Submit new password using the verified reset token.
    Validates token, enforces password rules, hashes password with bcrypt,
    and sends a security alert email.
    """
    if req.new_password != req.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirm password do not match.")

    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")

    # Decode reset token
    payload = decode_token(req.reset_token)
    if not payload or payload.get("type") != "password_reset":
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired password reset token. Please restart the forgot password process."
        )

    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=401, detail="Malformed reset token.")

    user = await user_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User account not found.")

    if user.get("is_deleted"):
        raise HTTPException(status_code=403, detail="Account is disabled or deleted.")

    # Strictly enforce USER role only
    if (user.get("role") or "USER").upper() != "USER":
        raise HTTPException(
            status_code=403,
            detail="Password reset on this portal is reserved for user accounts. Staff should use their administrative portal."
        )

    # Verify that the OTP record was actually marked verified
    otp_record = await otp_collection.find_one({"email": email, "purpose": "password_reset", "verified": True})
    if not otp_record:
        raise HTTPException(
            status_code=400,
            detail="Reset authorization has already been used or expired. Please request a new code."
        )

    # Hash new password
    hashed_pwd = get_password_hash(req.new_password)
    now = datetime.now(timezone.utc)

    # Update user password in database
    await user_collection.update_one(
        {"email": email},
        {"$set": {
            "password_hash": hashed_pwd,
            "password_changed_at": now,
            "updated_at": now
        }}
    )

    # Clean up OTP record so token cannot be re-used
    await otp_collection.delete_one({"email": email, "purpose": "password_reset"})

    # Send confirmation email
    user_name = user.get("name") or user.get("full_name") or email.split("@")[0]
    try:
        send_password_reset_success_email(email, user_name)
    except Exception as e:
        logger.warning(f"Failed to send password reset confirmation email: {e}")

    logger.info(f"Password successfully reset for user '{email}'")

    return {
        "message": "Password has been reset successfully. You can now log in with your new password.",
        "email": email,
        "success": True,
        "redirect_url": "/login"
    }


@router.post("/forgot-password/confirm")
async def forgot_password_direct_confirm(req: DirectResetPasswordReq):
    """
    Combined single-screen reset: verifies email + OTP and resets password in a single atomic request.
    Ideal for modern single-page reset screens.
    """
    email = req.email.strip().lower()
    otp_code = req.otp.strip()

    if req.new_password != req.confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirm password do not match.")

    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long.")

    user = await user_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="No account found with this email address.")

    if user.get("is_deleted"):
        raise HTTPException(status_code=403, detail="Account is disabled or deleted.")

    # Strictly enforce USER role only
    if (user.get("role") or "USER").upper() != "USER":
        raise HTTPException(
            status_code=403,
            detail="Password reset on this portal is reserved for user accounts. Staff should use their administrative portal."
        )

    record = await otp_collection.find_one({"email": email, "purpose": "password_reset"})
    if not record:
        raise HTTPException(status_code=400, detail="No active password reset request found. Please request a new code.")

    # Check expiration
    expire_at = record.get("expireAt")
    if expire_at:
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expire_at:
            await otp_collection.delete_one({"email": email, "purpose": "password_reset"})
            raise HTTPException(status_code=400, detail="Reset code has expired. Please request a new one.")

    # Check attempts
    attempts = record.get("attempts", 0)
    if attempts >= 5:
        await otp_collection.delete_one({"email": email, "purpose": "password_reset"})
        raise HTTPException(status_code=400, detail="Too many invalid attempts. Code revoked. Please request a new one.")

    if record.get("otp") != otp_code:
        await otp_collection.update_one(
            {"email": email, "purpose": "password_reset"},
            {"$inc": {"attempts": 1}}
        )
        raise HTTPException(status_code=400, detail="Invalid verification code.")

    # Hash new password
    hashed_pwd = get_password_hash(req.new_password)
    now = datetime.now(timezone.utc)

    await user_collection.update_one(
        {"email": email},
        {"$set": {
            "password_hash": hashed_pwd,
            "password_changed_at": now,
            "updated_at": now
        }}
    )

    await otp_collection.delete_one({"email": email, "purpose": "password_reset"})

    user_name = user.get("name") or user.get("full_name") or email.split("@")[0]
    try:
        send_password_reset_success_email(email, user_name)
    except Exception as e:
        logger.warning(f"Failed to send password reset confirmation email: {e}")

    logger.info(f"Direct password reset successful for user '{email}'")

    return {
        "message": "Password has been reset successfully. You can now log in with your new password.",
        "email": email,
        "success": True,
        "redirect_url": "/login"
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

    is_new_user = False
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

        is_completed, missing = check_profile_completion(existing_user)

        # Update profile photo and verified status without overriding incomplete profile
        update_fields = {
            "updated_at": datetime.now(timezone.utc),
            "email_verified": True,
            "registration_complete": is_completed,
            "is_profile_completed": is_completed,
            "profile_completed": is_completed,
        }
        if picture and not existing_user.get("profile_photo"):
            update_fields["profile_photo"] = picture
        if not existing_user.get("name") and name:
            update_fields["name"] = name

        await user_collection.update_one({"email": email}, {"$set": update_fields})
        user_doc = {**existing_user, **update_fields}
    else:
        # 3. Auto-register new USER without marking registration complete
        # First-time Google sign-ins MUST complete their profile (phone, dob, address)
        is_new_user = True
        is_completed = False
        missing = ["phone_number", "date_of_birth", "address", "parent_phone_number"]
        new_user_data = {
            "email": email,
            "name": name,
            "profile_photo": picture,
            "role": "USER",
            "email_verified": True,
            "registration_complete": False,
            "is_profile_completed": False,
            "profile_completed": False,
            "first_time_login": True,
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

    form_schema = get_profile_form_schema(user_doc)

    return {
        "message": "Google sign-in successful. Welcome to Eventora!" if is_new_user else "Google login successful",
        "access_token": access_token,
        "token_type": "bearer",
        "user_email": email,
        "role": "USER",
        "is_new_user": is_new_user,
        "registration_complete": is_completed,
        "is_profile_completed": is_completed,
        "profile_completed": is_completed,
        "requires_profile_completion": not is_completed,
        "prompt_profile_form": not is_completed,
        "first_time_login": not is_completed,
        "missing_fields": missing,
        "next_step": "COMPLETE_PROFILE" if not is_completed else "DASHBOARD",
        "redirect_url": "/complete-profile" if not is_completed else "/dashboard",
        "profile_form": form_schema,
        "user": {
            "email": email,
            "name": user_doc.get("name", name),
            "role": "USER",
            "profile_photo": user_doc.get("profile_photo", picture),
            "phone_number": user_doc.get("phone_number"),
            "date_of_birth": user_doc.get("date_of_birth"),
            "address": user_doc.get("address"),
            "parent_phone_number": user_doc.get("parent_phone_number"),
            "registration_complete": is_completed,
            "is_profile_completed": is_completed,
            "profile_completed": is_completed,
            "requires_profile_completion": not is_completed,
            "prompt_profile_form": not is_completed,
            "first_time_login": not is_completed,
            "missing_fields": missing,
            "next_step": "COMPLETE_PROFILE" if not is_completed else "DASHBOARD",
            "redirect_url": "/complete-profile" if not is_completed else "/dashboard",
            "profile_form": form_schema,
        }
    }


# ══════════════════════════════════════════════
# PROFILE & COMPLETION STATUS
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

    return enrich_user_with_profile_status(user)


@router.get("/status")
async def get_auth_status(authorization: str = Header(...)):
    """Check current authentication and profile completion status."""
    token = authorization.replace("Bearer ", "").strip()
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = await user_collection.find_one({"email": payload["sub"]})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("is_deleted"):
        raise HTTPException(status_code=403, detail="Account is disabled or deleted.")

    is_completed, missing = check_profile_completion(user)
    form_schema = get_profile_form_schema(user)
    return {
        "authenticated": True,
        "email": user.get("email"),
        "role": user.get("role", "USER"),
        "registration_complete": is_completed,
        "is_profile_completed": is_completed,
        "profile_completed": is_completed,
        "requires_profile_completion": not is_completed,
        "prompt_profile_form": not is_completed,
        "first_time_login": not is_completed,
        "missing_fields": missing,
        "next_step": "COMPLETE_PROFILE" if not is_completed else "DASHBOARD",
        "redirect_url": "/complete-profile" if not is_completed else "/dashboard",
        "profile_form": form_schema,
    }


@router.post("/complete-profile")
async def auth_complete_profile(
    name: Optional[str] = Form(None),
    phone_number: Optional[str] = Form(None),
    date_of_birth: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    parent_phone_number: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    profile_photo: Optional[UploadFile] = File(None),
    authorization: str = Header(...)
):
    """
    Dedicated endpoint for completing initial user profile after Google sign-in or incomplete email registration.
    Sets registration_complete, is_profile_completed, and profile_completed to True once required fields are provided.
    """
    token = authorization.replace("Bearer ", "").strip()
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    email = payload.get("sub")
    user = await user_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = {}
    if name is not None and name.strip():
        update_data["name"] = name.strip()

    if phone_number is not None and str(phone_number).strip():
        cleaned_phone = re.sub(r"\D", "", str(phone_number).strip())
        try:
            update_data["phone_number"] = int(cleaned_phone)
        except Exception:
            update_data["phone_number"] = cleaned_phone

    if date_of_birth is not None and date_of_birth.strip():
        dob_str = date_of_birth.strip()
        try:
            parsed_dob = parse_date(dob_str)
            update_data["date_of_birth"] = parsed_dob.strftime("%Y-%m-%d")
            update_data["age"] = calculate_age(parsed_dob)
        except Exception:
            update_data["date_of_birth"] = dob_str

    if address is not None and address.strip():
        update_data["address"] = address.strip()

    if parent_phone_number is not None and str(parent_phone_number).strip():
        cleaned_p = re.sub(r"\D", "", str(parent_phone_number).strip())
        try:
            update_data["parent_phone_number"] = int(cleaned_p)
        except Exception:
            update_data["parent_phone_number"] = cleaned_p

    if password is not None and password.strip():
        if len(password.strip()) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
        update_data["password_hash"] = get_password_hash(password.strip())

    if profile_photo:
        p_bytes = await profile_photo.read()
        if len(p_bytes) > 0:
            url = upload_image(p_bytes, folder="eventora/profiles", filename=profile_photo.filename)
            if url:
                update_data["profile_photo"] = url
            else:
                raise HTTPException(status_code=500, detail="Failed to upload profile photo to cloud storage.")

    if not update_data:
        raise HTTPException(status_code=400, detail="No profile fields provided")

    merged = {**user, **update_data}
    is_completed, missing = check_profile_completion(merged)
    if is_completed:
        update_data["registration_complete"] = True
        update_data["is_profile_completed"] = True
        update_data["profile_completed"] = True
        update_data["first_time_login"] = False
        if not user.get("profile_completed_at"):
            update_data["profile_completed_at"] = datetime.now(timezone.utc)

    update_data["updated_at"] = datetime.now(timezone.utc)
    await user_collection.update_one({"email": email}, {"$set": update_data})

    updated = await user_collection.find_one({"email": email}, {"_id": 0, "password_hash": 0})
    updated = enrich_user_with_profile_status(updated)

    return {
        "message": "Profile setup completed successfully" if updated.get("is_profile_completed") else "Profile updated successfully",
        "is_profile_completed": updated.get("is_profile_completed"),
        "requires_profile_completion": updated.get("requires_profile_completion"),
        "prompt_profile_form": updated.get("prompt_profile_form"),
        "first_time_login": updated.get("first_time_login"),
        "missing_fields": updated.get("missing_fields", []),
        "next_step": updated.get("next_step"),
        "redirect_url": updated.get("redirect_url"),
        "profile_form": updated.get("profile_form"),
        "user": updated
    }


@router.post("/complete-profile-json")
async def auth_complete_profile_json(
    req: CompleteProfileReq,
    authorization: str = Header(...)
):
    """JSON version of profile completion endpoint."""
    token = authorization.replace("Bearer ", "").strip()
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    email = payload.get("sub")
    user = await user_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = {}
    if req.name and req.name.strip():
        update_data["name"] = req.name.strip()
    if req.phone_number and str(req.phone_number).strip():
        cleaned_phone = re.sub(r"\D", "", str(req.phone_number).strip())
        try:
            update_data["phone_number"] = int(cleaned_phone)
        except Exception:
            update_data["phone_number"] = cleaned_phone
    if req.date_of_birth and req.date_of_birth.strip():
        try:
            parsed_dob = parse_date(req.date_of_birth.strip())
            update_data["date_of_birth"] = parsed_dob.strftime("%Y-%m-%d")
            update_data["age"] = calculate_age(parsed_dob)
        except Exception:
            update_data["date_of_birth"] = req.date_of_birth.strip()
    if req.address and req.address.strip():
        update_data["address"] = req.address.strip()
    if req.parent_phone_number and str(req.parent_phone_number).strip():
        cleaned_p = re.sub(r"\D", "", str(req.parent_phone_number).strip())
        try:
            update_data["parent_phone_number"] = int(cleaned_p)
        except Exception:
            update_data["parent_phone_number"] = cleaned_p
    if req.password and req.password.strip():
        if len(req.password.strip()) < 6:
            raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
        update_data["password_hash"] = get_password_hash(req.password.strip())
    if req.profile_photo and req.profile_photo.strip():
        update_data["profile_photo"] = req.profile_photo.strip()

    if not update_data:
        raise HTTPException(status_code=400, detail="No profile fields provided")

    merged = {**user, **update_data}
    is_completed, missing = check_profile_completion(merged)
    if is_completed:
        update_data["registration_complete"] = True
        update_data["is_profile_completed"] = True
        update_data["profile_completed"] = True
        update_data["first_time_login"] = False
        if not user.get("profile_completed_at"):
            update_data["profile_completed_at"] = datetime.now(timezone.utc)

    update_data["updated_at"] = datetime.now(timezone.utc)
    await user_collection.update_one({"email": email}, {"$set": update_data})

    updated = await user_collection.find_one({"email": email}, {"_id": 0, "password_hash": 0})
    updated = enrich_user_with_profile_status(updated)

    return {
        "message": "Profile setup completed successfully" if updated.get("is_profile_completed") else "Profile updated successfully",
        "is_profile_completed": updated.get("is_profile_completed"),
        "requires_profile_completion": updated.get("requires_profile_completion"),
        "prompt_profile_form": updated.get("prompt_profile_form"),
        "first_time_login": updated.get("first_time_login"),
        "missing_fields": updated.get("missing_fields", []),
        "next_step": updated.get("next_step"),
        "redirect_url": updated.get("redirect_url"),
        "profile_form": updated.get("profile_form"),
        "user": updated
    }
