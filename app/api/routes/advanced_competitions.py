from pydantic import BaseModel
from fastapi import status, APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Form, Query, Body
from bson import ObjectId
from app.api.deps import get_current_user, get_current_user_allow_incomplete
from app.core.database import (
    competition_collection, 
    competition_registration_collection,
    competition_submission_collection,
    registration_collection,
    user_collection,
)
from app.schemas.booking import PaymentStatus
from app.schemas.competition import (
    RegistrationStatus,
    SubmissionStatus,
    CompetitionRegisterReq,
    parse_age_limit,
)
from app.services.payment import PaymentService
from datetime import datetime, timezone, date
from typing import Optional, Any, Dict, List
import uuid
import os
import json
import re

from app.services.audio_validator import AudioValidator, VIDEO_EXTENSIONS
from app.services.cloudinary_srv import upload_image
from app.core.profile_lifecycle import check_profile_completion, assert_dob_modification_allowed
from app.services.competition_lifecycle import (
    assert_registration_allowed,
    evaluate_competition_registration_status,
)
from app.services.competition_submission_config import enrich_competition_submission_config
from app.services.share_service import ShareService
from app.schemas.share import ShareLinkResponse

router = APIRouter()


def safe_object_id(id_str: str) -> Optional[ObjectId]:
    """Safely converts a string to a BSON ObjectId, returning None on invalid format."""
    if not id_str:
        return None
    try:
        return ObjectId(str(id_str).strip())
    except Exception:
        return None


def extract_fee(comp_dict: Optional[dict], fallback_fee: float = 0.0) -> float:
    """Safely extracts the fee/price from a competition dictionary without crashing on None."""
    if not comp_dict:
        return max(0.0, float(fallback_fee or 0.0))
    for k in ("participation_fee", "fee", "entry_fee", "price"):
        v = comp_dict.get(k)
        if v is not None:
            try:
                f_val = float(v)
                return max(0.0, f_val)
            except (ValueError, TypeError):
                continue
    return max(0.0, float(fallback_fee or 0.0))


def calculate_age(dob_val: Any) -> int:
    """Calculates age safely from string (YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY, or ISO), date, or datetime object."""
    if not dob_val:
        return -1
    try:
        dob = None
        if isinstance(dob_val, datetime):
            dob = dob_val
        elif isinstance(dob_val, date):
            dob = datetime(dob_val.year, dob_val.month, dob_val.day)
        else:
            s = str(dob_val).strip()
            iso_match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', s)
            if iso_match:
                y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
                dob = datetime(y, m, d)
            else:
                dmy_match = re.search(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', s)
                if dmy_match:
                    d, m, y = int(dmy_match.group(1)), int(dmy_match.group(2)), int(dmy_match.group(3))
                    dob = datetime(y, m, d)
                else:
                    return -1
        today = datetime.now(timezone.utc)
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        return age
    except Exception:
        return -1


@router.get("/v1/competitions/by-category")
async def get_competitions_grouped_by_category():
    """Returns all published competitions grouped by category."""
    comps = await competition_collection.find({"is_published": True}).sort("created_at", -1).to_list(length=200)
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for c in comps:
        c["_id"] = str(c["_id"])
        reg_eval = evaluate_competition_registration_status(c)
        c["registration_status"] = reg_eval["registration_status"]
        c["is_registration_open"] = reg_eval["is_registration_open"]
        c["can_register"] = reg_eval["can_register"]
        enrich_competition_submission_config(c)
        ShareService.enrich_competition(c)
        
        cats = [cat.get("name") for cat in c.get("categories", []) if isinstance(cat, dict) and cat.get("name")]
        if not cats and c.get("category"):
            cats = [c["category"]]
        if not cats:
            cats = ["General"]
            
        for cat_name in cats:
            by_cat.setdefault(cat_name, []).append(c)
            
    return {
        "categories": list(by_cat.keys()),
        "by_category": by_cat,
        "grouped": [{"category": k, "count": len(v), "competitions": v} for k, v in by_cat.items()]
    }


@router.get("/v1/competitions/{comp_id}")
async def get_competition_by_id(comp_id: str):
    """Fetch single competition details with real-time registration status and configured submission fields."""
    c_id = safe_object_id(comp_id)
    if not c_id:
        raise HTTPException(status_code=400, detail="Invalid competition ID format.")
    comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    comp["_id"] = str(comp["_id"])
    
    # Enrich with real-time dynamic registration lifecycle status
    lifecycle_status = evaluate_competition_registration_status(comp)
    comp["registration_status"] = lifecycle_status["registration_status"]
    comp["is_registration_open"] = lifecycle_status["is_registration_open"]
    comp["can_register"] = lifecycle_status["can_register"]
    comp["registration_reason"] = lifecycle_status["reason"]

    # Enrich with explicit submission field configuration
    comp = enrich_competition_submission_config(comp)
    ShareService.enrich_competition(comp)
    return comp


@router.get("/v1/competitions/{comp_id}/share", response_model=ShareLinkResponse)
async def get_competition_v1_share_links(comp_id: str):
    """
    Public/User endpoint: returns shareable deep-link, direct registration link,
    pre-composed share text, and social share links (WhatsApp, Facebook, Twitter, LinkedIn, Telegram).
    """
    c_id = safe_object_id(comp_id)
    comp = None
    if c_id:
        comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        comp = await competition_collection.find_one({"_id": comp_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")
    return ShareService.build_competition_share_data(comp)


@router.get("/v1/competitions/{comp_id}/registration-status")
async def get_competition_registration_status(comp_id: str):
    """
    Public/User endpoint: Returns authoritative real-time registration status,
    open/close window dates, capacity limits, and whether registration is allowed right now.
    """
    c_id = safe_object_id(comp_id)
    if not c_id:
        raise HTTPException(status_code=400, detail="Invalid competition ID format.")
    comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    status_info = evaluate_competition_registration_status(comp)
    return {
        "competition_id": str(comp["_id"]),
        "title": comp.get("title"),
        **status_info,
    }

@router.get("/v1/competitions/{comp_id}/eligibility")
@router.get("/v1/competitions/{comp_id}/categories/{category_name}/eligibility")
async def check_eligibility(
    comp_id: str,
    category: Optional[str] = Query(None),
    category_name: Optional[str] = None,
    date_of_birth: Optional[str] = Query(None),
    user: dict = Depends(get_current_user_allow_incomplete)
):
    """Checks user eligibility based on age, supporting specific category requirements and date of birth calculation."""
    c_id = safe_object_id(comp_id)
    if not c_id:
        raise HTTPException(status_code=400, detail="Invalid competition ID format.")

    comp = await competition_collection.find_one({"_id": c_id, "is_published": True})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    # First check if competition registration is currently open
    reg_lifecycle = evaluate_competition_registration_status(comp)
    if not reg_lifecycle["can_register"]:
        return {
            "eligible": False,
            "status": "REGISTRATION_CLOSED",
            "reason": reg_lifecycle["reason"],
            "registration_status": reg_lifecycle["registration_status"],
            "can_register": False,
        }

    target_cat_query = category_name or category
    comp_categories = comp.get("categories") or []
    target_category = None

    if target_cat_query and target_cat_query.strip():
        c_clean = target_cat_query.strip()
        target_category = next(
            (c for c in comp_categories if isinstance(c, dict) and c.get("name", "").strip().lower() == c_clean.lower()),
            None
        )
        if not target_category and comp_categories:
            valid_names = [c.get("name") for c in comp_categories if isinstance(c, dict) and c.get("name")]
            return {
                "eligible": False,
                "status": "INVALID_CATEGORY",
                "reason": f"Category '{c_clean}' does not exist for this competition. Available categories: {', '.join(valid_names)}",
                "registration_status": reg_lifecycle["registration_status"],
                "can_register": False,
                "available_categories": valid_names,
            }
    elif len(comp_categories) == 1:
        target_category = comp_categories[0]

    min_age = None
    max_age = None
    age_limit_str = None

    if target_category:
        min_age = target_category.get("min_age")
        max_age = target_category.get("max_age")
        age_limit_str = target_category.get("age_limit")
        if (min_age is None or max_age is None) and age_limit_str:
            p_min, p_max = parse_age_limit(age_limit_str)
            if min_age is None:
                min_age = p_min
            if max_age is None:
                max_age = p_max
        if not age_limit_str:
            if min_age is not None and max_age is not None:
                age_limit_str = f"{min_age}-{max_age} years"
            elif min_age is not None:
                age_limit_str = f"{min_age}+ years"
            elif max_age is not None:
                age_limit_str = f"Up to {max_age} years"
            else:
                age_limit_str = "Open for all ages"
    else:
        min_age = comp.get("min_age")
        max_age = comp.get("max_age")
        age_limit_str = comp.get("age_limit")
        if (min_age is None or max_age is None) and age_limit_str:
            p_min, p_max = parse_age_limit(age_limit_str)
            if min_age is None:
                min_age = p_min
            if max_age is None:
                max_age = p_max
        if not age_limit_str:
            if min_age is not None and max_age is not None:
                age_limit_str = f"{min_age}-{max_age} years"
            elif min_age is not None:
                age_limit_str = f"{min_age}+ years"
            elif max_age is not None:
                age_limit_str = f"Up to {max_age} years"
            else:
                age_limit_str = "Open for all ages"

    cat_display_name = target_category.get("name") if target_category else comp.get("category", "General")

    if min_age is None and max_age is None and not comp_categories:
        return {
            "eligible": True,
            "status": "ELIGIBLE",
            "reason": "User is eligible.",
            "category": cat_display_name,
            "age_limit": "Open for all ages",
            "registration_status": reg_lifecycle["registration_status"],
            "can_register": True,
        }

    raw_dob = date_of_birth or user.get("date_of_birth") or user.get("dob")
    age = calculate_age(raw_dob)
    if age < 0 and user.get("age") is not None:
        try:
            age = int(user["age"])
        except (ValueError, TypeError):
            pass

    dob_display = str(raw_dob)[:10] if raw_dob else None

    if age < 0:
        return {
            "eligible": False,
            "status": "PROFILE_INCOMPLETE",
            "reason": f"Date of birth or age is required in your profile for eligibility verification for category '{cat_display_name}'.",
            "category": cat_display_name,
            "age_limit": age_limit_str,
            "registration_status": reg_lifecycle["registration_status"],
            "can_register": True,
        }

    cat_name_str = f" for category '{cat_display_name}'" if cat_display_name else ""
    if min_age is not None and age < min_age:
        return {
            "eligible": False,
            "status": "NOT_ELIGIBLE",
            "reason": f"Minimum age requirement{cat_name_str} is {min_age}. This category requires age {age_limit_str}, but your calculated age is {age} years (Date of birth: {dob_display}).",
            "category": cat_display_name,
            "calculated_age": age,
            "date_of_birth": dob_display,
            "min_age": min_age,
            "max_age": max_age,
            "age_limit": age_limit_str,
            "registration_status": reg_lifecycle["registration_status"],
            "can_register": False,
        }
    if max_age is not None and age > max_age:
        return {
            "eligible": False,
            "status": "NOT_ELIGIBLE",
            "reason": f"Maximum age requirement{cat_name_str} is {max_age}. This category requires age {age_limit_str}, but your calculated age is {age} years (Date of birth: {dob_display}).",
            "category": cat_display_name,
            "calculated_age": age,
            "date_of_birth": dob_display,
            "min_age": min_age,
            "max_age": max_age,
            "age_limit": age_limit_str,
            "registration_status": reg_lifecycle["registration_status"],
            "can_register": False,
        }

    eligible_categories = []
    for cat in comp_categories:
        if not isinstance(cat, dict):
            continue
        c_min = cat.get("min_age")
        c_max = cat.get("max_age")
        c_lim = cat.get("age_limit")
        if (c_min is None or c_max is None) and c_lim:
            p_min, p_max = parse_age_limit(c_lim)
            if c_min is None:
                c_min = p_min
            if c_max is None:
                c_max = p_max
        if (c_min is None or age >= c_min) and (c_max is None or age <= c_max):
            eligible_categories.append(cat.get("name"))

    return {
        "eligible": True,
        "status": "ELIGIBLE",
        "reason": "User is eligible.",
        "category": cat_display_name,
        "calculated_age": age,
        "date_of_birth": dob_display,
        "age_limit": age_limit_str,
        "eligible_categories": eligible_categories,
        "categories": [c.get("name") for c in comp_categories if isinstance(c, dict) and c.get("name")],
        "registration_status": reg_lifecycle["registration_status"],
        "can_register": True,
    }

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
        "competition_id": str(comp_id),
        "user_email": user_email
    })

    # 2. Check legacy registration collection fallback
    if not existing:
        existing = await registration_collection.find_one({
            "activity_id": str(comp_id),
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
        "competition_id": str(comp_id),
        "user_email": user_email
    })

    return {
        "is_registered": True,
        "is_confirmed": is_confirmed,
        "status": existing.get("status", "PENDING"),
        "payment_status": existing.get("payment_status", "PENDING"),
        "payment_method": existing.get("payment_method", "ONLINE"),
        "category": existing.get("category") or "General",
        "cash_payment": bool(existing.get("cash_payment", False)),
        "cash_status": existing.get("cash_status", ""),
        "registration_reference": existing.get("registration_reference") or existing.get("order_reference"),
        "has_submitted": bool(sub),
        "submission_id": str(sub["_id"]) if sub else None,
        "registered_at": existing.get("registered_at") or existing.get("created_at")
    }

@router.post("/v1/competitions/{comp_id}/register")
@router.post("/v1/competitions/{comp_id}/participate")
@router.post("/v1/competitions/{comp_id}/categories/{category_name}/participate")
@router.post("/v1/competitions/{comp_id}/categories/{category_name}/register")
async def register_competition(
    comp_id: str,
    category_name: Optional[str] = None,
    rules_accepted: Optional[bool] = Query(None),
    payment_method: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    date_of_birth: Optional[str] = Query(None),
    body: Optional[CompetitionRegisterReq] = Body(None),
    user: dict = Depends(get_current_user_allow_incomplete)
):
    """Registers / participates the user for a competition with age limit, fee safety, cash, and category verification."""
    is_rules_accepted = (
        rules_accepted if rules_accepted is not None
        else (body.rules_accepted if body and body.rules_accepted is not None else False)
    )
    pay_method = (
        payment_method if payment_method is not None
        else (body.payment_method if body and body.payment_method is not None else "ONLINE")
    ).strip().upper()
    if pay_method not in ["ONLINE", "CASH", "OFFLINE"]:
        pay_method = "ONLINE"

    selected_category = (
        category_name if category_name is not None
        else (category if category is not None
        else (body.category if body and body.category is not None else None))
    )
    if selected_category:
        selected_category = selected_category.strip()

    dob_input = (
        date_of_birth if date_of_birth is not None
        else (body.date_of_birth if body and body.date_of_birth is not None
        else (body.dob if body and body.dob is not None else None))
    )

    if not is_rules_accepted:
        raise HTTPException(status_code=400, detail="You must accept the competition rules.")

    c_id = safe_object_id(comp_id)
    if not c_id:
        raise HTTPException(status_code=400, detail="Invalid competition ID format.")

    comp = await competition_collection.find_one({"_id": c_id, "is_published": True})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    # Real-time registration deadline, capacity, and lifecycle enforcement
    assert_registration_allowed(comp)

    # Resolve category selection
    comp_categories = comp.get("categories") or []
    if not selected_category:
        if comp_categories and len(comp_categories) == 1:
            selected_category = comp_categories[0].get("name")
        elif comp.get("category"):
            selected_category = comp.get("category")
        elif comp_categories:
            selected_category = comp_categories[0].get("name")
        else:
            selected_category = "General"
    else:
        if comp_categories:
            cat_match = next(
                (c for c in comp_categories if isinstance(c, dict) and c.get("name", "").strip().lower() == selected_category.lower()),
                None
            )
            if not cat_match:
                valid_names = [c.get("name") for c in comp_categories if isinstance(c, dict) and c.get("name")]
                raise HTTPException(status_code=400, detail=f"Category '{selected_category}' is invalid. Available categories: {', '.join(valid_names)}")
            selected_category = cat_match.get("name")

    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=401, detail="User email not found in session.")

    # If date_of_birth provided in registration request, verify verified user DOB immutability
    if dob_input:
        assert_dob_modification_allowed(user, dob_input)
        if not user.get("date_of_birth"):
            user["date_of_birth"] = dob_input
            try:
                await user_collection.update_one(
                    {"email": user_email},
                    {"$set": {"date_of_birth": dob_input}}
                )
            except Exception:
                pass

    user_role = str(user.get("role") or "").upper().strip()
    if user_role not in ["JUDGE", "ADMIN", "SUPER_ADMIN", "VOLUNTEER"]:
        is_completed, missing = check_profile_completion(user)
        if not is_completed:
            raise HTTPException(
                status_code=403,
                detail=f"Registration incomplete. Please complete your profile details first (Missing: {', '.join(missing)})."
            )

    # Check eligibility for this specific category based on date_of_birth
    eligibility = await check_eligibility(
        comp_id=comp_id,
        category=selected_category,
        date_of_birth=dob_input,
        user=user
    )
    if not eligibility.get("eligible"):
        raise HTTPException(status_code=403, detail=eligibility.get("reason"))

    calc_age = eligibility.get("calculated_age") if eligibility.get("calculated_age") is not None else user.get("age")
    resolved_dob = eligibility.get("date_of_birth") or dob_input or user.get("date_of_birth") or user.get("dob") or ""

    # Check duplicate or pending registration
    existing = await competition_registration_collection.find_one({
        "competition_id": str(comp_id),
        "user_email": user_email
    })
    if not existing:
        legacy = await registration_collection.find_one({
            "activity_id": str(comp_id),
            "email": user_email,
            "activity_type": "competition"
        })
        if legacy:
            existing = legacy

    if existing:
        is_already_confirmed = (
            existing.get("status") == RegistrationStatus.CONFIRMED.value
            or existing.get("payment_status") in (PaymentStatus.PAID.value, PaymentStatus.NOT_REQUIRED.value)
            or existing.get("is_confirmed") is True
        )
        if is_already_confirmed:
            raise HTTPException(status_code=409, detail="Already registered and confirmed for this competition.")

        fee = extract_fee(comp, existing.get("fee", 0.0))
        reg_ref = (
            existing.get("registration_reference")
            or existing.get("order_reference")
            or f"CRG-{uuid.uuid4().hex[:10].upper()}"
        )

        if not existing.get("registration_reference"):
            await competition_registration_collection.update_one(
                {"_id": existing["_id"]},
                {"$set": {"registration_reference": reg_ref}}
            )

        if fee == 0.0:
            await competition_registration_collection.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "status": RegistrationStatus.CONFIRMED.value,
                    "payment_status": PaymentStatus.NOT_REQUIRED.value,
                    "payment_status_display": "SUCCESS",
                    "payment_method": pay_method,
                    "category": selected_category,
                    "date_of_birth": resolved_dob,
                    "age": calc_age,
                    "eligibility_status": "ELIGIBLE",
                    "registered_at": datetime.now(timezone.utc)
                }}
            )
            try:
                await competition_collection.update_one(
                    {"_id": c_id},
                    {"$inc": {"registered_count": 1}}
                )
            except Exception:
                pass
            return {"message": "Registration confirmed.", "status": RegistrationStatus.CONFIRMED.value, "payment_status": PaymentStatus.NOT_REQUIRED.value, "payment_method": pay_method, "category": selected_category, "reference": reg_ref}

        if pay_method in ["CASH", "OFFLINE"]:
            await competition_registration_collection.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "status": RegistrationStatus.CONFIRMED.value,
                    "payment_status": PaymentStatus.PENDING.value,
                    "payment_status_display": "PENDING",
                    "payment_method": "CASH",
                    "category": selected_category,
                    "date_of_birth": resolved_dob,
                    "age": calc_age,
                    "eligibility_status": "ELIGIBLE",
                    "cash_payment": True,
                    "cash_status": "PENDING",
                    "registered_at": datetime.now(timezone.utc)
                }}
            )
            try:
                await competition_collection.update_one(
                    {"_id": c_id},
                    {"$inc": {"registered_count": 1}}
                )
            except Exception:
                pass
            return {
                "message": f"Registration updated to Cash on Venue! Cash payment of ₹{fee:.2f} is pending.",
                "status": RegistrationStatus.CONFIRMED.value,
                "payment_status": PaymentStatus.PENDING.value,
                "payment_status_display": "PENDING",
                "payment_method": "CASH",
                "category": selected_category,
                "reference": reg_ref
            }

        # If registration is pending payment, update category & re-initiate payment gateway order
        await competition_registration_collection.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "category": selected_category,
                "date_of_birth": resolved_dob,
                "age": calc_age,
                "eligibility_status": "ELIGIBLE"
            }}
        )
        payment_info = await PaymentService.initiate_payment(
            booking_reference=reg_ref,
            amount=fee,
            currency="INR",
            notes={
                "type": "competition_registration",
                "competition_id": str(comp_id),
                "user_email": user_email,
                "category": selected_category,
                "registration_reference": reg_ref
            }
        )
        return {
            "message": "Registration pending payment. Please proceed to payment.",
            "status": RegistrationStatus.PENDING.value,
            "payment_status": PaymentStatus.PENDING.value,
            "payment_status_display": "PENDING",
            "payment_method": "ONLINE",
            "category": selected_category,
            "payment_info": payment_info,
            "reference": reg_ref
        }

    fee = extract_fee(comp, 0.0)
    reg_ref = f"CRG-{uuid.uuid4().hex[:10].upper()}"

    doc = {
        "registration_reference": reg_ref,
        "competition_id": str(comp_id),
        "user_email": user_email,
        "user_name": user.get("name") or user.get("full_name") or "",
        "user_phone": user.get("phone_number") or user.get("phone") or "",
        "user_id": str(user.get("_id")) if user.get("_id") else "",
        "date_of_birth": resolved_dob,
        "age": calc_age,
        "address": user.get("address") or user.get("full_address") or "",
        "parent_phone_number": user.get("parent_phone_number") or user.get("parent_phone") or user.get("emergency_contact") or "",
        "profile_photo": user.get("profile_photo") or user.get("avatar") or "",
        "govt_id_number": user.get("govt_id_number") or "",
        "govt_id_type": user.get("govt_id_type") or "AADHAAR",
        "is_verified": bool(user.get("is_verified") or user.get("verified_badge")),
        "verified_badge": bool(user.get("is_verified") or user.get("verified_badge")),
        "category": selected_category,
        "eligibility_status": "ELIGIBLE",
        "rules_accepted": is_rules_accepted,
        "fee": fee,
        "payment_method": pay_method,
        "registered_at": datetime.now(timezone.utc)
    }

    if fee == 0.0:
        doc["status"] = RegistrationStatus.CONFIRMED.value
        doc["payment_status"] = PaymentStatus.NOT_REQUIRED.value
        doc["payment_status_display"] = "SUCCESS"
        await competition_registration_collection.insert_one(doc)
        try:
            await competition_collection.update_one(
                {"_id": c_id},
                {"$inc": {"registered_count": 1}}
            )
        except Exception:
            pass
        return {"message": "Registration confirmed.", "status": RegistrationStatus.CONFIRMED.value, "payment_status": PaymentStatus.NOT_REQUIRED.value, "payment_method": pay_method, "category": selected_category, "reference": reg_ref}
    elif pay_method in ["CASH", "OFFLINE"]:
        doc["status"] = RegistrationStatus.CONFIRMED.value
        doc["payment_status"] = PaymentStatus.PENDING.value
        doc["payment_status_display"] = "PENDING"
        doc["payment_method"] = "CASH"
        doc["cash_payment"] = True
        doc["cash_status"] = "PENDING"
        await competition_registration_collection.insert_one(doc)
        try:
            await competition_collection.update_one(
                {"_id": c_id},
                {"$inc": {"registered_count": 1}}
            )
        except Exception:
            pass
        return {
            "message": f"Registration confirmed! Cash payment of ₹{fee:.2f} is pending at the venue.",
            "status": RegistrationStatus.CONFIRMED.value,
            "payment_status": PaymentStatus.PENDING.value,
            "payment_status_display": "PENDING",
            "payment_method": "CASH",
            "category": selected_category,
            "reference": reg_ref
        }
    else:
        doc["status"] = RegistrationStatus.PENDING.value
        doc["payment_status"] = PaymentStatus.PENDING.value
        doc["payment_status_display"] = "PENDING"
        doc["payment_method"] = "ONLINE"
        await competition_registration_collection.insert_one(doc)
        payment_info = await PaymentService.initiate_payment(
            booking_reference=reg_ref,
            amount=fee,
            currency="INR",
            notes={
                "type": "competition_registration",
                "competition_id": str(comp_id),
                "user_email": user_email,
                "category": selected_category,
                "registration_reference": reg_ref
            }
        )
        return {
            "message": "Registration pending payment.",
            "status": RegistrationStatus.PENDING.value,
            "payment_status": PaymentStatus.PENDING.value,
            "payment_status_display": "PENDING",
            "payment_method": "ONLINE",
            "category": selected_category,
            "payment_info": payment_info,
            "reference": reg_ref
        }

@router.post(
    "/v1/competitions/{comp_id}/submissions",
    summary="Submit Competition Entry Files / Links",
    response_description="Returns the processed submission document with verified file URLs and links",
)
async def submit_files(
    comp_id: str,
    files: list[UploadFile] = File(..., description="Multipart files or text files containing YouTube links"),
    field_names: str = Form(..., description="JSON-encoded array of strings or comma-separated list of field names matching dynamic fields"),
    user: dict = Depends(get_current_user),
):
    """
    Submit competition entry files and/or performance links.

    ### Submission Rules & Policies:
    - **Audio Fields (`Audio`, `Audio Only`):**
      - Strictly **MP3 format (`.mp3`)** only.
      - Binary header magic bytes are strictly verified (ID3v2 or MPEG Sync).
      - Rejects WAV, MP4/M4A, AAC, and all non-MP3 containers.
      - Uploaded to Cloudinary (`eventora/submissions`) and secure Cloudinary URL is stored.
    - **Video Fields (`Video`, `Performance Video`):**
      - Strictly **YouTube links** only (`https://youtube.com/watch?v=...`, `https://youtu.be/...`, or `https://youtube.com/shorts/...`).
      - Direct video file uploads (`.mp4`, `.mov`, `.avi`, `.mkv`) are strictly rejected with HTTP 400.
      - YouTube link is stored directly in database (no Cloudinary consumption).
    - **Dual Fields (`Video/Audio`, `Audio/Video`):**
      - Accepts either an MP3 audio file (uploaded to Cloudinary) or a YouTube video link (saved directly).
    - **Field Names Format:**
      - JSON string (e.g., `["Performance Entry (Video/Audio or Link)"]`) or comma-separated string.
      - Automatically normalizes legacy `Video, Audio` to `Video/Audio`.
    """
    c_id = safe_object_id(comp_id)
    if not c_id:
        raise HTTPException(status_code=400, detail="Invalid competition ID format.")

    comp = await competition_collection.find_one({"_id": c_id})
    if not comp:
        raise HTTPException(status_code=404, detail="Competition not found")

    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=401, detail="User email not found in session.")

    # Verify confirmed registration
    reg = await competition_registration_collection.find_one({
        "competition_id": str(comp_id), 
        "user_email": user_email,
        "status": RegistrationStatus.CONFIRMED.value
    })
    if not reg:
        legacy_reg = await registration_collection.find_one({
            "activity_id": str(comp_id),
            "email": user_email,
            "status": "CONFIRMED"
        })
        if not legacy_reg:
            raise HTTPException(status_code=403, detail="Confirmed registration required to submit.")
        reg = legacy_reg

    # Dynamic Fields and Competition Submission Settings Check
    comp = enrich_competition_submission_config(comp)
    sub_cfg = comp.get("submission_format_config") or {}
    allow_file = bool(comp.get("allow_file_upload", sub_cfg.get("allow_file_upload", True)))
    allow_video = bool(comp.get("allow_video_link", sub_cfg.get("allow_video_link", False)))
    allow_audio = bool(comp.get("allow_audio_upload", sub_cfg.get("allow_audio_upload", False)))
    require_all = bool(sub_cfg.get("require_all_fields", False))

    fields = comp.get("dynamic_submission_fields", [])
    valid_field_names = [f["name"] for f in fields if isinstance(f, dict) and f.get("name")]
    expected_fields = [f["name"] for f in fields if isinstance(f, dict) and f.get("required") and f.get("name")]

    # Parse field_names (support both JSON array and comma-separated string)
    raw_fn = field_names.strip()
    if raw_fn.startswith("[") and raw_fn.endswith("]"):
        try:
            provided_fields = [str(x).strip() for x in json.loads(raw_fn)]
        except Exception:
            provided_fields = [f.strip() for f in raw_fn.split(",") if f.strip()]
    else:
        provided_fields = [f.strip() for f in raw_fn.split(",") if f.strip()]

    # Normalize legacy field naming variations if needed
    normalized_expected = [
        ef.replace("Video, Audio", "Video/Audio").replace("video,audio", "video/audio")
        for ef in expected_fields
    ]
    normalized_provided = [
        pf.replace("Video, Audio", "Video/Audio").replace("video,audio", "video/audio")
        for pf in provided_fields
    ]
    norm_valid_fields = [
        vf.replace("Video, Audio", "Video/Audio").replace("video,audio", "video/audio")
        for vf in valid_field_names
    ]

    # Validate that every provided field matches an enabled competition field
    if norm_valid_fields:
        for pf, norm_pf in zip(provided_fields, normalized_provided):
            match_found = (
                pf in valid_field_names
                or norm_pf in norm_valid_fields
                or pf.lower() in [x.lower() for x in valid_field_names]
                or norm_pf.lower() in [x.lower() for x in norm_valid_fields]
            )
            if not match_found:
                raise HTTPException(
                    status_code=400,
                    detail=f"Field '{pf}' is not enabled for this competition. Allowed fields: {valid_field_names}"
                )

    # Required field verification
    if require_all:
        missing = set(normalized_expected) - set(normalized_provided)
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing required fields: {list(missing)}")
    else:
        # At least one of the enabled fields must be provided if expected fields exist
        if normalized_expected and not set(normalized_provided).intersection(set(normalized_expected)):
            raise HTTPException(status_code=400, detail=f"At least one valid submission field is required from: {expected_fields}")

    if len(files) != len(provided_fields):
        raise HTTPException(status_code=400, detail="Number of files does not match number of field names provided.")

    # Process files: enforce strict validation against enabled competition submission formats
    uploaded_urls = {}
    for i, file_obj in enumerate(files):
        field_name = provided_fields[i]
        norm_field_name = normalized_provided[i]

        field_cfg = next(
            (f for f in fields if isinstance(f, dict) and f.get("name") in (field_name, norm_field_name)),
            {}
        )
        field_type = (field_cfg.get("type") or "").upper()
        field_name_lower = norm_field_name.lower()

        is_video_field = field_type == "VIDEO" or "video" in field_name_lower
        is_audio_field = field_type == "AUDIO" or "audio" in field_name_lower
        is_dual_field = is_video_field and is_audio_field

        # 1. Check if submission is a web link (YouTube video link)
        link_url = await AudioValidator.validate_and_extract_link(file_obj, field_name)
        if link_url:
            if not allow_video:
                raise HTTPException(
                    status_code=400,
                    detail=f"Video link submissions are not allowed for this competition."
                )
            is_yt = AudioValidator.is_valid_youtube_url(link_url)
            if is_yt:
                # Valid YouTube link: stored directly in database!
                uploaded_urls[norm_field_name] = link_url
                continue

            # Link was provided, but is not a valid YouTube link
            if is_video_field or is_dual_field:
                raise HTTPException(
                    status_code=400,
                    detail=f"For video entries in '{field_name}', only valid YouTube links (e.g. https://www.youtube.com/watch?v=... or https://youtu.be/...) are accepted."
                )
            elif is_audio_field:
                raise HTTPException(
                    status_code=400,
                    detail=f"Audio field '{field_name}' requires an MP3 audio file (.mp3) upload, not a link."
                )
            else:
                uploaded_urls[norm_field_name] = link_url
                continue

        # 2. It is a binary file upload.
        # Check if user tried to upload a direct video file:
        filename_lower = (file_obj.filename or "").lower()
        file_ext = os.path.splitext(filename_lower)[1]
        ct = (file_obj.content_type or "").lower()

        if file_ext in VIDEO_EXTENSIONS or ct.startswith("video/"):
            raise HTTPException(
                status_code=400,
                detail=f"Direct video file uploads are not supported for '{field_name}'. Please provide a YouTube link instead."
            )

        # 3. Audio upload verification
        is_audio_candidate = file_ext == ".mp3" or ct.startswith("audio/") or is_audio_field
        if is_audio_field or (is_audio_candidate and not allow_file):
            if not allow_audio:
                raise HTTPException(
                    status_code=400,
                    detail=f"Audio uploads are not allowed for this competition."
                )
            await AudioValidator.validate_mp3_file(file_obj, field_name)
        else:
            # 4. General file upload verification
            if not allow_file:
                raise HTTPException(
                    status_code=400,
                    detail=f"File uploads are not allowed for this competition."
                )

        # 5. Stream to Cloudinary (for MP3 audio files or other allowed attachments like images/docs)
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

        uploaded_urls[norm_field_name] = cloud_url

    # Check existing submission to update or insert fresh
    existing_sub = await competition_submission_collection.find_one({
        "competition_id": str(comp_id),
        "user_email": user_email
    })

    sub_category = reg.get("category") or comp.get("category") or "General"
    sub_dob = reg.get("date_of_birth") or user.get("date_of_birth") or user.get("dob")
    sub_age = reg.get("age") or user.get("age")
    if existing_sub:
        await competition_submission_collection.update_one(
            {"_id": existing_sub["_id"]},
            {"$set": {
                "files": uploaded_urls,
                "category": sub_category,
                "date_of_birth": sub_dob,
                "age": sub_age,
                "status": SubmissionStatus.SUBMITTED.value,
                "updated_at": datetime.now(timezone.utc)
            }}
        )
        sub_id = str(existing_sub["_id"])
    else:
        doc = {
            "competition_id": str(comp_id),
            "user_email": user_email,
            "category": sub_category,
            "date_of_birth": sub_dob,
            "age": sub_age,
            "files": uploaded_urls,
            "status": SubmissionStatus.SUBMITTED.value,
            "submitted_at": datetime.now(timezone.utc)
        }
        res = await competition_submission_collection.insert_one(doc)
        sub_id = str(res.inserted_id)

    return {"message": "Submission successful", "submission_id": sub_id, "files": uploaded_urls, "category": sub_category}


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
    c_id = safe_object_id(comp_id)
    if not c_id:
        raise HTTPException(status_code=400, detail="Invalid competition ID format.")

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
        "registration_reference": req.registration_reference
    })
    if not reg or str(reg.get("competition_id")) != str(comp_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competition registration not found")

    user_email = user.get("email")
    if reg.get("user_email") != user_email and user.get("role") != "super_admin":
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
            {"_id": c_id},
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
