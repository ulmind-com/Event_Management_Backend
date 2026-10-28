"""
app/services/competition_lifecycle.py
======================================
Authoritative production-grade service for Competition Registration Lifecycle
and deadline enforcement.

Ensures that whenever a competition registration is closed:
1. By explicit administrator action (registration_status == 'CLOSED' or is_registration_open == False)
2. By automated deadline expiration (current time > registration_deadline)
3. By scheduled opening window (current time < registration_start_date)
4. By maximum participant capacity exhaustion (registered_count >= max_participants)

Any attempt to register is strictly prevented across ALL registration endpoints,
and real-time status is queryable via public & admin APIs.
"""

from datetime import datetime, timezone, date
from typing import Optional, Dict, Any, Tuple
from bson import ObjectId
from fastapi import HTTPException
import logging

logger = logging.getLogger(__name__)


def safe_object_id(id_str: Any) -> Optional[ObjectId]:
    """Safely converts a string or ObjectId to BSON ObjectId without raising exceptions."""
    if not id_str:
        return None
    if isinstance(id_str, ObjectId):
        return id_str
    try:
        return ObjectId(str(id_str).strip())
    except Exception:
        return None


def parse_to_utc(val: Any) -> Optional[datetime]:
    """
    Safely converts strings (ISO, with or without Z/offset, common date formats),
    datetime objects, and date objects into a timezone-aware UTC datetime.
    Returns None if empty or unparseable.
    """
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day, 0, 0, 0, tzinfo=timezone.utc)
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            return None
        # Try fromisoformat (handles ISO 8601 with +00:00 or Z)
        try:
            dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass

        # Fallback date formats
        formats = (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d-%m-%Y",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y",
            "%Y/%m/%d",
        )
        for fmt in formats:
            try:
                dt = datetime.strptime(cleaned, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

    return None


def evaluate_competition_registration_status(
    comp: Optional[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Evaluates the real-time registration status of a competition.

    Returns a comprehensive status dictionary:
    {
        "can_register": bool,
        "is_registration_open": bool,
        "registration_status": "OPEN" | "CLOSED" | "UPCOMING" | "FULL",
        "reason": str,
        "registration_start_date": Optional[str],
        "registration_deadline": Optional[str],
        "registered_count": int,
        "max_participants": Optional[int],
        "is_published": bool,
    }
    """
    if not comp:
        return {
            "can_register": False,
            "is_registration_open": False,
            "registration_status": "CLOSED",
            "reason": "Competition does not exist.",
            "registration_start_date": None,
            "registration_deadline": None,
            "registered_count": 0,
            "max_participants": None,
            "is_published": False,
        }

    now_utc = now if (now and now.tzinfo) else datetime.now(timezone.utc)

    # 1. Published verification
    is_published = bool(
        comp.get("is_published") is True
        or str(comp.get("status", "")).strip().upper() == "PUBLISHED"
    )

    # 2. Extract and parse dates
    reg_start = parse_to_utc(
        comp.get("registration_start_date")
        or comp.get("reg_start_date")
        or comp.get("registration_start_at")
    )
    reg_deadline = parse_to_utc(
        comp.get("registration_deadline")
        or comp.get("registration_end_date")
        or comp.get("reg_end_date")
        or comp.get("registration_end_at")
        or comp.get("deadline")
    )
    end_date = parse_to_utc(comp.get("end_date"))

    # 3. Extract capacity
    max_participants = comp.get("max_participants") or comp.get("capacity")
    try:
        max_participants = int(max_participants) if max_participants is not None else None
    except (ValueError, TypeError):
        max_participants = None

    registered_count = comp.get("registered_count", 0)
    try:
        registered_count = int(registered_count or 0)
    except (ValueError, TypeError):
        registered_count = 0

    base_result = {
        "registration_start_date": reg_start.isoformat() if reg_start else None,
        "registration_deadline": reg_deadline.isoformat() if reg_deadline else None,
        "registered_count": registered_count,
        "max_participants": max_participants,
        "is_published": is_published,
    }

    # Rule A: Unpublished competition
    if not is_published:
        return {
            **base_result,
            "can_register": False,
            "is_registration_open": False,
            "registration_status": "CLOSED",
            "reason": "This competition is not published.",
        }

    # Rule B: Overall competition terminal status
    general_status = str(comp.get("status") or "").strip().upper()
    if general_status in ("CLOSED", "ENDED", "COMPLETED", "ARCHIVED", "CANCELLED"):
        return {
            **base_result,
            "can_register": False,
            "is_registration_open": False,
            "registration_status": "CLOSED",
            "reason": f"Registration is closed because the competition is {general_status.lower()}.",
        }

    # Rule C: Explicit administrative toggle (is_registration_open: False)
    if comp.get("is_registration_open") is False:
        return {
            **base_result,
            "can_register": False,
            "is_registration_open": False,
            "registration_status": "CLOSED",
            "reason": "Registration for this competition has been manually closed by administration.",
        }

    # Rule D: Explicit registration_status attribute ('CLOSED' or 'UPCOMING')
    explicit_reg_status = str(comp.get("registration_status") or "").strip().upper()
    if explicit_reg_status == "CLOSED":
        return {
            **base_result,
            "can_register": False,
            "is_registration_open": False,
            "registration_status": "CLOSED",
            "reason": "Registration for this competition is closed.",
        }

    # Rule E: Registration start date in the future (UPCOMING)
    if reg_start and now_utc < reg_start:
        formatted_start = reg_start.strftime("%d %b %Y, %H:%M UTC")
        return {
            **base_result,
            "can_register": False,
            "is_registration_open": False,
            "registration_status": "UPCOMING",
            "reason": f"Registration has not opened yet. Registration opens on {formatted_start}.",
        }

    if explicit_reg_status == "UPCOMING" and (not reg_start or now_utc < reg_start):
        return {
            **base_result,
            "can_register": False,
            "is_registration_open": False,
            "registration_status": "UPCOMING",
            "reason": "Registration for this competition has not opened yet.",
        }

    # Rule F: Registration deadline has passed
    if reg_deadline and now_utc >= reg_deadline:
        formatted_deadline = reg_deadline.strftime("%d %b %Y, %H:%M UTC")
        return {
            **base_result,
            "can_register": False,
            "is_registration_open": False,
            "registration_status": "CLOSED",
            "reason": f"Registration closed on {formatted_deadline}.",
        }

    # Rule G: Fallback to competition end date if no registration deadline was specified
    if not reg_deadline and end_date and now_utc >= end_date:
        formatted_end = end_date.strftime("%d %b %Y, %H:%M UTC")
        return {
            **base_result,
            "can_register": False,
            "is_registration_open": False,
            "registration_status": "CLOSED",
            "reason": f"Competition ended on {formatted_end}. Registration is closed.",
        }

    # Rule H: Participant capacity limit reached
    if max_participants is not None and max_participants > 0:
        if registered_count >= max_participants:
            return {
                **base_result,
                "can_register": False,
                "is_registration_open": False,
                "registration_status": "FULL",
                "reason": f"Registration closed: participant capacity limit ({max_participants}) has been reached.",
            }

    # Rule I: All checks passed -> Registration is OPEN
    return {
        **base_result,
        "can_register": True,
        "is_registration_open": True,
        "registration_status": "OPEN",
        "reason": "Registration is open.",
    }


def assert_registration_allowed(
    comp: Optional[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Enforces that registration is currently permitted for this competition.
    Raises HTTPException(status_code=400) if closed, full, or upcoming.
    Returns the evaluated status dict on success.
    """
    status_info = evaluate_competition_registration_status(comp, now=now)
    if not status_info["can_register"]:
        reason = status_info.get("reason") or "Registration for this competition is closed."
        logger.warning(
            f"Blocked registration attempt for competition '{comp.get('_id') if comp else None}': {reason}"
        )
        raise HTTPException(
            status_code=400,
            detail=reason,
        )
    return status_info
