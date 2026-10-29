"""
Profile Lifecycle & Onboarding Enforcement Module
=================================================
Centralizes profile completeness verification, required fields validation,
profile input form schemas, and onboarding lifecycle for all user logins (Email & Google).
"""

from datetime import datetime, date, timezone
from typing import Tuple, List, Optional, Dict, Any
import re


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
    Checks if all required profile fields (name, phone_number, date_of_birth, address) are filled.
    Returns (is_completed, missing_fields_list).
    """
    if not user:
        return False, ["name", "phone_number", "date_of_birth", "address"]

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
                "placeholder": "Required for participants under 18",
                "required": False,
                "value": parent_phone_val,
                "is_missing": False,
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
    user["redirect_url"] = "/dashboard?tab=profile&setup=true" if not is_completed else "/dashboard"
    user["next_step"] = "COMPLETE_PROFILE" if not is_completed else "DASHBOARD"
    user["profile_form"] = form_schema
    return user
