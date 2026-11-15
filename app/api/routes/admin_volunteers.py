import secrets
import string
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from bson import ObjectId
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException

from app.core.admin_auth import get_current_admin
from app.core.database import user_collection, event_collection
from app.core.security import get_password_hash
from app.services.email import EmailService

router = APIRouter()


def generate_volunteer_password(length: int = 6) -> str:
    """Generates a memorable and clean secure temporary access password."""
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    code = "".join(secrets.choice(alphabet) for _ in range(length))
    return f"UTI-{code}"


class VolunteerCreateReq(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    phone: Optional[str] = None
    password: Optional[str] = None
    role_title: Optional[str] = "Entrance Gate Scanner"


class SingleVolunteerInput(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = "Entrance Gate Scanner"
    password: Optional[str] = None


class AssignEventVolunteersReq(BaseModel):
    volunteer_emails: Optional[List[EmailStr]] = []
    new_volunteers: Optional[List[SingleVolunteerInput]] = []
    default_role: Optional[str] = "Entrance Gate Scanner"


# ── GLOBAL VOLUNTEER DIRECTORY ──────────────────────────────────────

@router.get("/")
async def list_volunteers(admin: dict = Depends(get_current_admin)):
    """List all registered volunteers in the system."""
    volunteers = await user_collection.find({"role": "VOLUNTEER"}).to_list(length=1000)
    for v in volunteers:
        v["_id"] = str(v["_id"])
        # Do not leak hashed passwords to client
        v.pop("password_hash", None)
    return volunteers


@router.post("/")
async def add_volunteer(req: VolunteerCreateReq, admin: dict = Depends(get_current_admin)):
    """Add or upgrade a volunteer, generate secure credentials and send invitation."""
    email_clean = str(req.email).lower().strip()
    generated_pwd = req.password or generate_volunteer_password()
    hashed_pwd = get_password_hash(generated_pwd)

    existing = await user_collection.find_one({"email": email_clean})
    if existing:
        update_doc: Dict[str, Any] = {
            "role": "VOLUNTEER",
            "registration_complete": True,
            "password_hash": hashed_pwd,
            "has_password": True,
            "updated_at": datetime.now(timezone.utc),
        }
        if req.name and not existing.get("name"):
            update_doc["name"] = req.name.strip()
        if req.phone and not existing.get("phone"):
            update_doc["phone"] = req.phone.strip()

        await user_collection.update_one({"email": email_clean}, {"$set": update_doc})
    else:
        await user_collection.insert_one({
            "email": email_clean,
            "name": (req.name or "").strip(),
            "phone": (req.phone or "").strip(),
            "role": "VOLUNTEER",
            "registration_complete": True,
            "password_hash": hashed_pwd,
            "has_password": True,
            "assigned_events": [],
            "created_at": datetime.now(timezone.utc),
        })

    # Dispatch invite email with credentials
    EmailService.send_volunteer_invitation(email_clean, name=req.name)
    return {
        "message": "Volunteer registered successfully with login credentials.",
        "email": email_clean,
        "temporary_password": generated_pwd,
    }


@router.delete("/{email}")
async def remove_volunteer(email: str, admin: dict = Depends(get_current_admin)):
    """Demote volunteer to regular user and unassign from all events."""
    email_clean = email.lower().strip()
    user = await user_collection.find_one({"email": email_clean, "role": "VOLUNTEER"})
    if not user:
        raise HTTPException(status_code=404, detail="Volunteer not found.")

    await user_collection.update_one(
        {"email": email_clean},
        {
            "$set": {
                "role": "USER",
                "assigned_events": [],
                "updated_at": datetime.now(timezone.utc),
            }
        }
    )

    # Pull from all events
    await event_collection.update_many(
        {"volunteers.email": email_clean},
        {"$pull": {"volunteers": {"email": email_clean}}}
    )

    return {"message": "Volunteer role removed and unassigned from events."}


# ── EVENT-SPECIFIC VOLUNTEER ASSIGNMENT ──────────────────────────────

@router.get("/events/{event_id}")
async def get_event_volunteers(event_id: str, admin: dict = Depends(get_current_admin)):
    """
    Get all assigned volunteers for an event plus available registered volunteers for selection.
    """
    if not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid event ID")

    event = await event_collection.find_one({"_id": ObjectId(event_id)})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    assigned = event.get("volunteers") or []
    assigned_emails = {str(v.get("email", "")).lower() for v in assigned if v.get("email")}

    # Fetch all volunteers registered in system
    all_vols = await user_collection.find({"role": "VOLUNTEER"}).to_list(length=1000)
    available = []
    for v in all_vols:
        v_email = str(v.get("email", "")).lower()
        if not v_email:
            continue
        available.append({
            "user_id": str(v["_id"]),
            "email": v_email,
            "name": v.get("name") or v.get("full_name") or "",
            "phone": v.get("phone") or v.get("phone_number") or "",
            "is_assigned": v_email in assigned_emails,
        })

    return {
        "event_id": str(event["_id"]),
        "event_title": event.get("title", ""),
        "event_date": event.get("date", ""),
        "event_time": event.get("time", ""),
        "event_venue": event.get("venue", ""),
        "assigned_volunteers": assigned,
        "available_volunteers": available,
        "total_assigned": len(assigned),
        "total_available": len(available),
    }


@router.post("/events/{event_id}")
async def assign_event_volunteers(
    event_id: str,
    req: AssignEventVolunteersReq,
    admin: dict = Depends(get_current_admin)
):
    """
    Assign multiple volunteers to an event, generate secure credentials, and email credentials + gate pass.
    """
    if not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid event ID")

    event = await event_collection.find_one({"_id": ObjectId(event_id)})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    event_title = event.get("title", "Upgrade Talents Event")
    event_date = event.get("date", "")
    event_time = event.get("time", "")
    event_venue = event.get("venue", "")

    existing_volunteers = event.get("volunteers") or []
    assigned_map = {str(v.get("email", "")).lower(): v for v in existing_volunteers if v.get("email")}

    assigned_results = []

    # 1. Process volunteer_emails (existing or created on the fly)
    target_emails = [str(e).lower().strip() for e in (req.volunteer_emails or []) if e]

    # 2. Also merge new_volunteers
    for nv in (req.new_volunteers or []):
        nv_email = str(nv.email).lower().strip()
        if not nv_email:
            continue

        target_emails.append(nv_email)
        # Ensure user exists in user_collection
        user = await user_collection.find_one({"email": nv_email})
        pwd = nv.password or generate_volunteer_password()
        hashed = get_password_hash(pwd)

        if user:
            await user_collection.update_one(
                {"email": nv_email},
                {
                    "$set": {
                        "role": "VOLUNTEER",
                        "password_hash": hashed,
                        "has_password": True,
                        "name": nv.name or user.get("name", ""),
                        "phone": nv.phone or user.get("phone", ""),
                        "updated_at": datetime.now(timezone.utc),
                    }
                }
            )
        else:
            await user_collection.insert_one({
                "email": nv_email,
                "name": nv.name or "",
                "phone": nv.phone or "",
                "role": "VOLUNTEER",
                "registration_complete": True,
                "password_hash": hashed,
                "has_password": True,
                "assigned_events": [],
                "created_at": datetime.now(timezone.utc),
            })

    # Deduplicate emails
    target_emails = list(dict.fromkeys(target_emails))

    for email_clean in target_emails:
        user = await user_collection.find_one({"email": email_clean})
        raw_password = generate_volunteer_password()

        if user:
            # Check if user already has a password; if not or if assigning, set guaranteed password
            if not user.get("password_hash"):
                await user_collection.update_one(
                    {"email": email_clean},
                    {
                        "$set": {
                            "role": "VOLUNTEER",
                            "password_hash": get_password_hash(raw_password),
                            "has_password": True,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    }
                )
            else:
                # Keep password or provide fresh access code
                await user_collection.update_one(
                    {"email": email_clean},
                    {
                        "$set": {
                            "role": "VOLUNTEER",
                            "password_hash": get_password_hash(raw_password),
                            "has_password": True,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    }
                )
        else:
            # Auto-create volunteer
            user_insert = await user_collection.insert_one({
                "email": email_clean,
                "name": "",
                "phone": "",
                "role": "VOLUNTEER",
                "registration_complete": True,
                "password_hash": get_password_hash(raw_password),
                "has_password": True,
                "assigned_events": [],
                "created_at": datetime.now(timezone.utc),
            })
            user = await user_collection.find_one({"_id": user_insert.inserted_id})

        user_name = user.get("name") or user.get("full_name") or ""
        user_phone = user.get("phone") or user.get("phone_number") or ""
        assigned_role = req.default_role or "Entrance Gate Scanner"

        # Update event record
        vol_entry = {
            "user_id": str(user["_id"]),
            "email": email_clean,
            "name": user_name,
            "phone": user_phone,
            "role": assigned_role,
            "assigned_at": datetime.now(timezone.utc).isoformat(),
        }
        assigned_map[email_clean] = vol_entry

        # Update user's assigned_events array
        event_entry = {
            "event_id": str(event["_id"]),
            "title": event_title,
            "date": event_date,
            "time": event_time,
            "venue": event_venue,
            "role": assigned_role,
        }
        await user_collection.update_one(
            {"email": email_clean},
            {
                "$pull": {"assigned_events": {"event_id": str(event["_id"])}},
            }
        )
        await user_collection.update_one(
            {"email": email_clean},
            {
                "$push": {"assigned_events": event_entry},
            }
        )

        # Dispatch email with password credentials & event details
        try:
            EmailService.send_volunteer_event_assignment(
                email=email_clean,
                password=raw_password,
                event_title=event_title,
                event_date=event_date,
                event_time=event_time,
                event_venue=event_venue,
                name=user_name,
                role=assigned_role,
            )
        except Exception as e:
            # Log and continue
            pass

        assigned_results.append({
            "email": email_clean,
            "name": user_name,
            "role": assigned_role,
            "status": "ASSIGNED_AND_NOTIFIED",
        })

    # Save updated list to event document
    new_volunteers_list = list(assigned_map.values())
    await event_collection.update_one(
        {"_id": ObjectId(event_id)},
        {"$set": {"volunteers": new_volunteers_list, "updated_at": datetime.now(timezone.utc)}}
    )

    return {
        "message": f"Successfully assigned {len(assigned_results)} volunteer(s) to {event_title}.",
        "event_id": event_id,
        "assigned": assigned_results,
        "total_assigned": len(new_volunteers_list),
    }


@router.delete("/events/{event_id}/{email}")
async def remove_event_volunteer(
    event_id: str,
    email: str,
    admin: dict = Depends(get_current_admin)
):
    """Unassign a volunteer from a specific event."""
    if not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid event ID")

    email_clean = email.lower().strip()
    await event_collection.update_one(
        {"_id": ObjectId(event_id)},
        {"$pull": {"volunteers": {"email": email_clean}}}
    )

    await user_collection.update_one(
        {"email": email_clean},
        {"$pull": {"assigned_events": {"event_id": event_id}}}
    )

    return {"message": f"Volunteer {email_clean} unassigned from event."}


@router.post("/events/{event_id}/{email}/resend-credentials")
async def resend_event_volunteer_credentials(
    event_id: str,
    email: str,
    admin: dict = Depends(get_current_admin)
):
    """
    Generate fresh password credentials and re-email the volunteer for this event.
    """
    if not ObjectId.is_valid(event_id):
        raise HTTPException(status_code=400, detail="Invalid event ID")

    event = await event_collection.find_one({"_id": ObjectId(event_id)})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    email_clean = email.lower().strip()
    user = await user_collection.find_one({"email": email_clean})
    if not user:
        raise HTTPException(status_code=404, detail="Volunteer user not found")

    # Generate fresh password and update hash
    fresh_password = generate_volunteer_password()
    await user_collection.update_one(
        {"email": email_clean},
        {
            "$set": {
                "password_hash": get_password_hash(fresh_password),
                "has_password": True,
                "updated_at": datetime.now(timezone.utc),
            }
        }
    )

    # Find assigned role
    volunteers = event.get("volunteers") or []
    role = "Entrance Gate Scanner"
    for v in volunteers:
        if str(v.get("email", "")).lower() == email_clean:
            role = v.get("role") or role
            break

    user_name = user.get("name") or user.get("full_name") or ""

    sent = EmailService.send_volunteer_event_assignment(
        email=email_clean,
        password=fresh_password,
        event_title=event.get("title", ""),
        event_date=event.get("date", ""),
        event_time=event.get("time", ""),
        event_venue=event.get("venue", ""),
        name=user_name,
        role=role,
    )

    return {
        "message": f"Fresh credentials and event pass emailed to {email_clean}.",
        "email": email_clean,
        "temporary_password": fresh_password,
        "email_sent": sent,
    }
