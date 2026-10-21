from pydantic import BaseModel
from fastapi import status, APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Form
from bson import ObjectId
from app.api.deps import get_current_user, get_current_user_allow_incomplete
from app.core.database import (
    competition_collection, 
    competition_registration_collection,
    competition_submission_collection,
    registration_collection
)
from app.schemas.booking import PaymentStatus
from app.schemas.competition import RegistrationStatus, SubmissionStatus
from app.services.payment import PaymentService
from datetime import datetime, timezone
import uuid

from app.services.audio_validator import AudioValidator
from app.services.cloudinary_srv import upload_image

router = APIRouter()

def calculate_age(dob_str: str) -> int:
    try:
        dob = datetime.strptime(dob_str, "%Y-%m-%d")
        today = datetime.now(timezone.utc)
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        return age
    except:
        return -1

@router.get("/v1/competitions/{comp_id}")
async def get_competition_by_id(comp_id: str):
    """Fetch single competition details."""
    try:
        c_id = ObjectId(comp_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid competition ID")
    comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    comp["_id"] = str(comp["_id"])
    return comp

@router.get("/v1/competitions/{comp_id}/eligibility")
async def check_eligibility(comp_id: str, user: dict = Depends(get_current_user)):
    """Checks user eligibility based on age."""
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id), "is_published": True})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    dob = user.get("date_of_birth")
    if not dob:
        return {"eligible": False, "status": "PROFILE_INCOMPLETE", "reason": "Date of birth is required for eligibility check."}
        
    age = calculate_age(dob)
    if age < 0:
        return {"eligible": False, "status": "INVALID_DOB", "reason": "Invalid date of birth format."}
        
    min_age = comp.get("min_age")
    max_age = comp.get("max_age")
    
    if min_age is not None and age < min_age:
        return {"eligible": False, "status": "NOT_ELIGIBLE", "reason": f"Minimum age requirement is {min_age}."}
    if max_age is not None and age > max_age:
        return {"eligible": False, "status": "NOT_ELIGIBLE", "reason": f"Maximum age requirement is {max_age}."}
        
    return {"eligible": True, "status": "ELIGIBLE", "reason": "User is eligible."}

@router.get("/v1/competitions/{comp_id}/my-registration")
async def get_my_competition_registration_status(
    comp_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Authoritative check for the current user's registration status in a competition.
    Returns whether the user has actually registered, payment status, and submission state.
    """
    user_email = user.get("email")
    if not user_email:
        return {"is_registered": False, "is_confirmed": False, "status": "NOT_REGISTERED"}

    # 1. Check advanced competition registration
    existing = await competition_registration_collection.find_one({
        "competition_id": comp_id,
        "user_email": user_email
    })
    
    # 2. Check legacy registration collection fallback
    if not existing:
        existing = await registration_collection.find_one({
            "activity_id": comp_id,
            "email": user_email,
            "activity_type": "competition"
        })
        
    if not existing:
        return {
            "is_registered": False,
            "is_confirmed": False,
            "status": "NOT_REGISTERED"
        }

    is_confirmed = (
        existing.get("status") == RegistrationStatus.CONFIRMED.value
        or existing.get("payment_status") in (PaymentStatus.PAID.value, PaymentStatus.NOT_REQUIRED.value)
        or existing.get("is_confirmed") is True
    )
    
    sub = await competition_submission_collection.find_one({
        "competition_id": comp_id,
        "user_email": user_email
    })
    
    return {
        "is_registered": True,
        "is_confirmed": is_confirmed,
        "status": existing.get("status", "PENDING"),
        "payment_status": existing.get("payment_status", "PENDING"),
        "registration_reference": existing.get("registration_reference") or existing.get("order_reference"),
        "has_submitted": bool(sub),
        "submission_id": str(sub["_id"]) if sub else None,
        "registered_at": existing.get("registered_at") or existing.get("created_at")
    }

@router.post("/v1/competitions/{comp_id}/register")
async def register_competition(comp_id: str, rules_accepted: bool = False, user: dict = Depends(get_current_user)):
    """Registers the user for a competition."""
    if not rules_accepted:
        raise HTTPException(status_code=400, detail="You must accept the competition rules.")
        
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id), "is_published": True})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    # Check registration window
    now = datetime.now(timezone.utc)
    # Basic date checks (could add more robust parsing if needed)
    
    # Check eligibility again
    eligibility = await check_eligibility(comp_id, user)
    if not eligibility.get("eligible"):
        raise HTTPException(status_code=403, detail=eligibility.get("reason"))
        
    # Check duplicate or pending registration
    existing = await competition_registration_collection.find_one({"competition_id": comp_id, "user_email": user["email"]})
    if existing:
        if existing.get("status") == RegistrationStatus.CONFIRMED.value or existing.get("payment_status") in (PaymentStatus.PAID.value, PaymentStatus.NOT_REQUIRED.value):
            raise HTTPException(status_code=409, detail="Already registered and confirmed for this competition.")
        
        fee = float(comp.get("participation_fee", existing.get("fee", 0.0)))
        reg_ref = existing.get("registration_reference") or f"CRG-{uuid.uuid4().hex[:10].upper()}"

        if fee == 0.0:
            await competition_registration_collection.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "status": RegistrationStatus.CONFIRMED.value,
                    "payment_status": PaymentStatus.NOT_REQUIRED.value,
                    "registered_at": datetime.now(timezone.utc)
                }}
            )
            try:
                await competition_collection.update_one(
                    {"_id": ObjectId(comp_id)},
                    {"$inc": {"registered_count": 1}}
                )
            except Exception:
                pass
            return {"message": "Registration confirmed.", "status": RegistrationStatus.CONFIRMED.value, "reference": reg_ref}
        
        # If registration is pending payment, re-initiate payment gateway order
        payment_info = await PaymentService.initiate_payment(
            booking_reference=reg_ref,
            amount=fee,
            currency="INR",
            notes={
                "type": "competition_registration",
                "competition_id": comp_id,
                "user_email": user["email"],
                "registration_reference": reg_ref
            }
        )
        return {
            "message": "Registration pending payment. Please proceed to payment.",
            "status": RegistrationStatus.PENDING.value,
            "payment_info": payment_info,
            "reference": reg_ref
        }
        
    fee = float(comp.get("participation_fee", 0.0))
    reg_ref = f"CRG-{uuid.uuid4().hex[:10].upper()}"
    
    doc = {
        "registration_reference": reg_ref,
        "competition_id": comp_id,
        "user_email": user["email"],
        "eligibility_status": "ELIGIBLE",
        "rules_accepted": rules_accepted,
        "fee": fee,
        "registered_at": datetime.now(timezone.utc)
    }
    
    if fee == 0.0:
        doc["status"] = RegistrationStatus.CONFIRMED.value
        doc["payment_status"] = PaymentStatus.NOT_REQUIRED.value
        await competition_registration_collection.insert_one(doc)
        try:
            await competition_collection.update_one(
                {"_id": ObjectId(comp_id)},
                {"$inc": {"registered_count": 1}}
            )
        except Exception:
            pass
        return {"message": "Registration confirmed.", "status": RegistrationStatus.CONFIRMED.value, "reference": reg_ref}
    else:
        doc["status"] = RegistrationStatus.PENDING.value
        doc["payment_status"] = PaymentStatus.PENDING.value
        await competition_registration_collection.insert_one(doc)
        payment_info = await PaymentService.initiate_payment(
            booking_reference=reg_ref,
            amount=fee,
            currency="INR",
            notes={
                "type": "competition_registration",
                "competition_id": comp_id,
                "user_email": user["email"],
                "registration_reference": reg_ref
            }
        )
        return {"message": "Registration pending payment.", "status": RegistrationStatus.PENDING.value, "payment_info": payment_info, "reference": reg_ref}

@router.post("/v1/competitions/{comp_id}/submissions")
async def submit_files(comp_id: str, files: list[UploadFile] = File(...), field_names: str = Form(...), user: dict = Depends(get_current_user)):
    """
    Accepts multipart uploads. field_names is a comma separated string matching the dynamic fields.
    e.g. "Vocal Audio, Performance Video"
    """
    comp = await competition_collection.find_one({"_id": ObjectId(comp_id)})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
        
    # Verify confirmed registration
    reg = await competition_registration_collection.find_one({
        "competition_id": comp_id, 
        "user_email": user["email"],
        "status": RegistrationStatus.CONFIRMED.value
    })
    if not reg:
        raise HTTPException(status_code=403, detail="Confirmed registration required to submit.")
        
    # Dynamic Fields Check
    fields = comp.get("dynamic_submission_fields", [])
    expected_fields = [f["name"] for f in fields if f.get("required")]
    
    provided_fields = [f.strip() for f in field_names.split(",")]
    
    missing = set(expected_fields) - set(provided_fields)
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {missing}")
        
    if len(files) != len(provided_fields):
        raise HTTPException(status_code=400, detail="Number of files does not match number of field names provided.")
        
    # Process files: enforce strict audio validation for uploads & extract links (YouTube/Drive)
    uploaded_urls = {}
    for i, file_obj in enumerate(files):
        field_name = provided_fields[i]

        # 1. Check if submission is a YouTube / Google Drive / web link
        link_url = await AudioValidator.validate_and_extract_link(file_obj, field_name)
        if link_url:
            uploaded_urls[field_name] = link_url
            continue

        # 2. Strict audio file validation (extension, MIME, magic numbers)
        await AudioValidator.validate_audio_file(file_obj, field_name)

        # 3. Stream to Cloudinary
        await file_obj.seek(0)
        file_bytes = await file_obj.read()
        if not file_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded file for '{field_name}' is empty."
            )

        cloud_url = upload_image(
            file_bytes,
            folder="eventora/submissions",
            filename=file_obj.filename,
        )
        if not cloud_url:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload submission file for '{field_name}' to cloud storage. Please try again."
            )

        uploaded_urls[field_name] = cloud_url
        
    doc = {
        "competition_id": comp_id,
        "user_email": user["email"],
        "files": uploaded_urls,
        "status": SubmissionStatus.SUBMITTED.value,
        "submitted_at": datetime.now(timezone.utc)
    }
    
    res = await competition_submission_collection.insert_one(doc)
    return {"message": "Submission successful", "submission_id": str(res.inserted_id), "files": uploaded_urls}


class CompetitionPaymentVerifyReq(BaseModel):
    registration_reference: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/v1/competitions/{comp_id}/verify-payment")
async def verify_competition_payment(
    comp_id: str,
    req: CompetitionPaymentVerifyReq,
    user: dict = Depends(get_current_user)
):
    """
    Client-side verification for paid competition registration checkout.
    Verifies Razorpay cryptographic HMAC signature and marks registration as CONFIRMED.
    """
    is_valid = PaymentService.verify_payment_signature(
        razorpay_order_id=req.razorpay_order_id,
        razorpay_payment_id=req.razorpay_payment_id,
        razorpay_signature=req.razorpay_signature
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Razorpay payment signature. Verification failed."
        )

    reg = await competition_registration_collection.find_one({
        "competition_id": comp_id,
        "registration_reference": req.registration_reference
    })
    if not reg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competition registration not found")

    if reg.get("user_email") != user.get("email") and user.get("role") != "super_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized")

    if reg.get("status") == RegistrationStatus.CONFIRMED.value and reg.get("payment_status") == PaymentStatus.PAID.value:
        return {
            "status": RegistrationStatus.CONFIRMED.value,
            "message": "Registration already confirmed.",
            "reference": req.registration_reference
        }

    # Update registration to confirmed & paid
    await competition_registration_collection.update_one(
        {"_id": reg["_id"]},
        {"$set": {
            "status": RegistrationStatus.CONFIRMED.value,
            "payment_status": PaymentStatus.PAID.value,
            "payment_reference": req.razorpay_payment_id,
            "provider_order_id": req.razorpay_order_id,
            "payment_provider": "RAZORPAY",
            "paid_at": datetime.now(timezone.utc)
        }}
    )

    # Increment registered_count on competition
    try:
        await competition_collection.update_one(
            {"_id": ObjectId(comp_id)},
            {"$inc": {"registered_count": 1}}
        )
    except Exception:
        pass

    return {
        "status": RegistrationStatus.CONFIRMED.value,
        "message": "Payment verified and competition registration confirmed successfully.",
        "reference": req.registration_reference,
        "payment_id": req.razorpay_payment_id
    }
