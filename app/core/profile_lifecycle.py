"""
Profile Lifecycle & Onboarding Enforcement Module
=================================================
Centralizes profile completeness verification, required fields validation,
profile input form schemas, and onboarding lifecycle for all user logins (Email & Google).
"""

from datetime import datetime, date, timezone
from typing import Tuple, List, Optional, Dict, Any
from fastapi import HTTPException, status
import re


def is_user_verified(user: Optional[Dict[str, Any]]) -> bool:
    """Returns True if the user account has been verified by an admin."""
    if not user or not isinstance(user, dict):
        return False
    return bool(
        user.get("is_verified")
        or user.get("is_id_verified")
        or user.get("is_govt_id_verified")
        or user.get("verified_badge")
        or user.get("verified_tick")
        or str(user.get("kyc_status") or "").upper() == "VERIFIED"
        or str(user.get("verification_status") or "").upper() == "VERIFIED"
    )


def normalize_dob(dob_val: Any) -> Optional[str]:
    """Normalizes any date of birth string/date/datetime into YYYY-MM-DD string."""
    if not dob_val:
        return None
    if isinstance(dob_val, datetime):
        return dob_val.strftime("%Y-%m-%d")
    if isinstance(dob_val, date):
        return dob_val.strftime("%Y-%m-%d")
    s = str(dob_val).strip()
    if not s:
        return None
    # ISO / YYYY-MM-DD
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', s)
    if m:
        try:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        except Exception:
            pass
    # DD-MM-YYYY or DD/MM/YYYY
    m = re.search(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', s)
    if m:
        try:
            return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        except Exception:
            pass
    return s[:10]


def assert_dob_modification_allowed(current_user: Optional[Dict[str, Any]], new_dob_val: Any) -> None:
    """
    Enforces that an admin-verified user cannot alter their date of birth.
    Raises HTTPException(400) if an admin-verified user attempts to change their DOB.
    """
    if not current_user or not is_user_verified(current_user):
        return

    existing_dob_norm = normalize_dob(current_user.get("date_of_birth") or current_user.get("dob"))
    if not existing_dob_norm:
        return  # No prior DOB was set

    if new_dob_val is None:
        return

    new_dob_norm = normalize_dob(new_dob_val)
    if not new_dob_norm:
        return

    if new_dob_norm != existing_dob_norm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your profile has been verified by admin. You cannot modify your date of birth once verified."
        )


def calculate_age(dob: date) -> int:
    """Calculates current age from date of birth."""
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def parse_date(date_str: str) -> Optional[date]:
    """Parses date string supporting ISO and standard Indian formats."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            pass
    return None


def check_profile_completion(user: Optional[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """
    Checks if all required profile fields (name, phone_number, date_of_birth, address, parent_phone_number) are filled.
    Returns (is_completed, missing_fields_list).
    Staff roles (JUDGE, ADMIN, SUPER_ADMIN, VOLUNTEER) do not require competitor onboarding.
    """
    if not user:
        return False, ["name", "phone_number", "date_of_birth", "address", "parent_phone_number"]

    # Exempt staff / judge / admin / volunteer roles from competitor profile checks
    role = str(user.get("role") or "").upper().strip()
    if role in ["JUDGE", "ADMIN", "SUPER_ADMIN", "VOLUNTEER"]:
        return True, []

    missing: List[str] = []

    # 1. Full Name
    name = user.get("name")
    if not name or not str(name).strip():
        missing.append("name")

    # 2. Phone Number
    phone = user.get("phone_number")
    if phone is None or not str(phone).strip():
        missing.append("phone_number")
    else:
        cleaned_phone = re.sub(r"\D", "", str(phone).strip())
        if len(cleaned_phone) < 10:
            missing.append("phone_number")

    # 3. Date of Birth
    dob = user.get("date_of_birth")
    if not dob or not str(dob).strip():
        missing.append("date_of_birth")

    # 4. Address
    address = user.get("address")
    if not address or not str(address).strip():
        missing.append("address")

    # 5. Parent / Guardian Phone Number (Only required for standard USER role, not JUDGE/ADMIN/VOLUNTEER)
    if user.get("role") in [None, "USER"]:
        parent_phone = user.get("parent_phone_number")
        if parent_phone is None or not str(parent_phone).strip():
            missing.append("parent_phone_number")
        else:
            cleaned_parent_phone = re.sub(r"\D", "", str(parent_phone).strip())
            if len(cleaned_parent_phone) < 10:
                missing.append("parent_phone_number")


    return len(missing) == 0, missing


def get_profile_form_schema(user: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Returns the structured schema for the profile details input form.
    Frontend and mobile clients use this schema to render the required input form immediately after login.
    """
    user_data = user or {}
    is_completed, missing = check_profile_completion(user_data)

    current_phone = user_data.get("phone_number")
    phone_val = str(current_phone) if current_phone is not None else ""

    current_parent_phone = user_data.get("parent_phone_number")
    parent_phone_val = str(current_parent_phone) if current_parent_phone is not None else ""

    return {
        "title": "Complete Your Profile Details",
        "description": "Please provide your personal information to complete registration and access all event features.",
        "is_completed": is_completed,
        "requires_completion": not is_completed,
        "missing_fields": missing,
        "submit_url": "/api/auth/complete-profile",
        "submit_json_url": "/api/auth/complete-profile-json",
        "submit_method": "POST",
        "enctype": "multipart/form-data",
        "fields": [
            {
                "name": "name",
                "label": "Full Name",
                "type": "text",
                "placeholder": "Enter your full name",
                "required": True,
                "value": user_data.get("name") or "",
                "is_missing": "name" in missing,
            },
            {
                "name": "phone_number",
                "label": "Phone Number",
                "type": "tel",
                "placeholder": "10-digit mobile number",
                "required": True,
                "value": phone_val,
                "is_missing": "phone_number" in missing,
            },
            {
                "name": "date_of_birth",
                "label": "Date of Birth",
                "type": "date",
                "placeholder": "YYYY-MM-DD",
                "required": True,
                "value": user_data.get("date_of_birth") or "",
                "is_missing": "date_of_birth" in missing,
                "is_locked": bool(is_user_verified(user_data) and (user_data.get("date_of_birth") or user_data.get("dob"))),
                "read_only": bool(is_user_verified(user_data) and (user_data.get("date_of_birth") or user_data.get("dob"))),
            },
            {
                "name": "address",
                "label": "Residential Address",
                "type": "text",
                "placeholder": "Enter your full address",
                "required": True,
                "value": user_data.get("address") or "",
                "is_missing": "address" in missing,
            },
            {
                "name": "parent_phone_number",
                "label": "Parent / Guardian Contact",
                "type": "tel",
                "placeholder": "10-digit guardian mobile number",
                "required": True,
                "value": parent_phone_val,
                "is_missing": "parent_phone_number" in missing,
            },
            {
                "name": "profile_photo",
                "label": "Profile Photo",
                "type": "file",
                "accept": "image/*",
                "required": False,
                "value": user_data.get("profile_photo") or None,
                "is_missing": False,
            },
        ],
    }


def enrich_user_with_profile_status(user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enriches a user dictionary with profile completion flags and structured form schema.
    """
    is_completed, missing = check_profile_completion(user)
    form_schema = get_profile_form_schema(user)

    user["registration_complete"] = is_completed
    user["is_profile_completed"] = is_completed
    user["profile_completed"] = is_completed
    user["requires_profile_completion"] = not is_completed
    user["prompt_profile_form"] = not is_completed
    user["first_time_login"] = not is_completed or user.get("first_time_login", False)
    user["missing_fields"] = missing
    user["redirect_url"] = "/complete-profile" if not is_completed else "/dashboard"
    user["next_step"] = "COMPLETE_PROFILE" if not is_completed else "DASHBOARD"
    user["profile_form"] = form_schema

    # ── Government ID & Profile Verification Badges ──
    is_verified = is_user_verified(user)
    user["is_verified"] = is_verified
    user["is_id_verified"] = is_verified
    user["is_govt_id_verified"] = is_verified
    user["verified_badge"] = is_verified
    user["verified_tick"] = is_verified
    user["verification_status"] = "VERIFIED" if is_verified else user.get("verification_status", "NOT_VERIFIED")
    if is_verified:
        user["badge_label"] = "Verified Profile"

    # Flag indicating whether DOB is locked due to admin verification
    has_dob = bool(user.get("date_of_birth") or user.get("dob"))
    user["is_dob_locked"] = bool(is_verified and has_dob)

    return user

