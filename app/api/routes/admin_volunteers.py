import re
import secrets
import string
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from bson import ObjectId
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException

from app.core.admin_auth import get_current_admin
from app.core.database import user_collection, event_collection, issued_ticket_collection, booking_collection, admin_collection
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
    """List all registered volunteers in the system with check-in and cash collection metrics."""
    volunteers = await user_collection.find({"role": "VOLUNTEER"}).to_list(length=1000)
    for v in volunteers:
        v["_id"] = str(v["_id"])
        # Do not leak hashed passwords to client
        v.pop("password_hash", None)
        email = v.get("email")
        if email:
            checkins = await issued_ticket_collection.find({
                "$or": [
                    {"checked_in_by": email},
                    {"scanned_by": email}
                ]
            }).to_list(length=2000)
            
            total_checkins = len(checkins)
            cash_collected_tickets = [
                t for t in checkins 
                if t.get("cash_status") == "COLLECTED" and (t.get("cash_collected_by") == email or t.get("checked_in_by") == email)
            ]
            total_cash_amount = sum(float(t.get("cash_amount_collected") or t.get("cash_amount") or t.get("price") or t.get("total_amount") or 0.0) for t in cash_collected_tickets)
            
            v["total_checkins"] = total_checkins
            v["cash_collected_count"] = len(cash_collected_tickets)
            v["online_checkins_count"] = max(0, total_checkins - len(cash_collected_tickets))
            v["total_cash_collected"] = round(total_cash_amount, 2)
            v["last_active"] = checkins[-1].get("checked_in_at") if checkins else None
        else:
            v["total_checkins"] = 0
            v["cash_collected_count"] = 0
            v["online_checkins_count"] = 0
            v["total_cash_collected"] = 0.0
    return volunteers


@router.get("/{email}/checkins")
async def get_volunteer_checkins(email: str, admin: dict = Depends(get_current_admin)):
    """Return all attendees and tickets scanned by this specific volunteer with cash details."""
    clean_email = email.strip().lower()
    
    vol_user = await user_collection.find_one({"email": clean_email})
    if not vol_user:
        vol_user = await user_collection.find_one({"email": {"$regex": f"^{re.escape(clean_email)}$", "$options": "i"}})
    
    checkins = await issued_ticket_collection.find({
        "$or": [
            {"checked_in_by": clean_email},
            {"scanned_by": clean_email},
            {"cash_collected_by": clean_email}
        ]
    }).sort("check_in_time", -1).to_list(length=1000)
    
    results = []
    total_cash = 0.0
    cash_count = 0
    online_count = 0
    
    for t in checkins:
        is_cash = str(t.get("payment_method", "")).upper() in ["CASH", "OFFLINE"] or bool(t.get("cash_payment"))
        is_cash_done = bool(
            t.get("cash_collected")
            or t.get("cash_collect_done")
            or t.get("cash_status") in ("COLLECTED", "APPROVED")
            or t.get("cash_approved")
        )
        amt = float(t.get("cash_amount_collected") or t.get("cash_amount") or t.get("price") or t.get("total_amount") or 0.0)
        
        if is_cash_done:
            total_cash += amt
            cash_count += 1
        else:
            online_count += 1
            
        seats = t.get("seats") or ([t.get("seat")] if t.get("seat") else [])
        holder_names = t.get("holder_names") or ([t.get("holder_name")] if t.get("holder_name") else ["Guest"])
        
        results.append({
            "ticket_ref": t.get("ticket_ref"),
            "booking_reference": t.get("booking_ref"),
            "holder_name": t.get("holder_name") or (holder_names[0] if holder_names else "Guest"),
            "holder_names": holder_names,
            "holder_email": t.get("holder_email"),
            "event_id": t.get("event_id"),
            "event_title": t.get("event_title") or "Event",
            "category": t.get("category") or "General",
            "quantity": t.get("quantity", 1),
            "seats": seats,
            "payment_method": "CASH" if is_cash else t.get("payment_method", "ONLINE"),
            "payment_status": "PAID" if is_cash_done else t.get("payment_status", "PAID"),
            "cash_status": "COLLECTED" if is_cash_done else ("PENDING" if is_cash else "NOT_APPLICABLE"),
            "cash_collect_done": is_cash_done,
            "cash_collected_display": "Cash Collect Done" if is_cash_done else ("Cash Pending" if is_cash else "Online Paid"),
            "cash_amount": amt if is_cash_done else 0.0,
            "check_in_time": t.get("check_in_time") or t.get("checked_in_at"),
            "checked_in_at": t.get("checked_in_at") or t.get("check_in_time"),
            "checked_in_by": t.get("checked_in_by") or clean_email,
            "checked_in_by_name": t.get("checked_in_by_name") or ((vol_user.get("full_name") or vol_user.get("name")) if vol_user else None),
        })
        
    return {
        "volunteer_email": clean_email,
        "volunteer_name": ((vol_user.get("full_name") or vol_user.get("name")) if vol_user else clean_email),
        "total_checkins": len(results),
        "cash_collected_count": cash_count,
        "online_checkins_count": online_count,
        "total_cash_collected": round(total_cash, 2),
        "checkins": results
    }


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

    enriched_assigned = []
    for v in assigned:
        v_copy = dict(v)
        vemail = str(v.get("email", "")).strip().lower()
        if vemail:
            v_checkins = await issued_ticket_collection.find({
                "event_id": str(event["_id"]),
                "$or": [
                    {"checked_in_by": vemail},
                    {"scanned_by": vemail}
                ]
            }).to_list(length=1000)
            v_cash_tix = [
                t for t in v_checkins
                if t.get("cash_status") == "COLLECTED" and (t.get("cash_collected_by") == vemail or t.get("checked_in_by") == vemail)
            ]
            v_copy["total_checkins"] = len(v_checkins)
            v_copy["cash_collected_count"] = len(v_cash_tix)
            v_copy["online_checkins_count"] = max(0, len(v_checkins) - len(v_cash_tix))
            v_copy["total_cash_collected"] = round(sum(float(t.get("cash_amount_collected") or t.get("cash_amount") or t.get("price") or t.get("total_amount") or 0.0) for t in v_cash_tix), 2)
        else:
            v_copy["total_checkins"] = 0
            v_copy["cash_collected_count"] = 0
            v_copy["online_checkins_count"] = 0
            v_copy["total_cash_collected"] = 0.0
        enriched_assigned.append(v_copy)

    return {
        "event_id": str(event["_id"]),
        "event_title": event.get("title", ""),
        "event_date": event.get("date", ""),
        "event_time": event.get("time", ""),
        "event_venue": event.get("venue", ""),
        "assigned_volunteers": enriched_assigned,
        "available_volunteers": available,
        "total_assigned": len(enriched_assigned),
        "total_available": len(available),
    }


@router.get("/events/{event_id}/report")
@router.get("/events/{event_id}/volunteer-report")
async def get_event_volunteer_report(event_id: str, admin: dict = Depends(get_current_admin)):
    """
    Returns the comprehensive volunteer-wise entry and cash collection audit report for an event.
    For each volunteer assigned or who scanned tickets for this event:
    - total entries / admitted attendees
    - online payments count
    - cash payments count
    - total physical cash collected in INR
    - individual attendee tickets with seat numbers, payment status, cash amount, and timestamps.
    """
    clean_eid = str(event_id).strip()
    ev = None
    if ObjectId.is_valid(clean_eid):
        ev = await event_collection.find_one({"_id": ObjectId(clean_eid), "is_deleted": {"$ne": True}})
    if not ev:
        ev = await event_collection.find_one({"$or": [{"_id": clean_eid}, {"id": clean_eid}], "is_deleted": {"$ne": True}})
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")

    ev_match = [clean_eid]
    if ObjectId.is_valid(clean_eid):
        ev_match.append(ObjectId(clean_eid))

    # Assigned volunteers from event doc
    assigned = ev.get("volunteers") or []
    volunteer_groups: Dict[str, Dict[str, Any]] = {}

    for v in assigned:
        vemail = str(v.get("email", "")).strip().lower()
        if vemail:
            vname = v.get("name") or v.get("full_name") or vemail.split("@")[0].title()
            volunteer_groups[vemail] = {
                "volunteer_name": vname,
                "volunteer_email": vemail,
                "role": v.get("role") or "Entrance Gate Scanner",
                "phone": v.get("phone") or v.get("phone_number") or "",
                "is_assigned": True,
                "total_entries": 0,
                "online_payments": 0,
                "cash_payments": 0,
                "cash_collected": 0.0,
                "attendees": []
            }

    # Fetch all issued tickets for this event that have been scanned or checked in
    tickets = await issued_ticket_collection.find({
        "event_id": {"$in": ev_match},
        "$or": [
            {"is_checked_in": True},
            {"admitted_count": {"$gt": 0}},
            {"status": {"$in": ["USED", "PARTIALLY_USED"]}}
        ]
    }).sort("check_in_time", -1).to_list(length=5000)

    # Process each scanned ticket
    for t in tickets:
        scanner_email = str(t.get("checked_in_by") or t.get("scanned_by") or "").strip().lower()
        if not scanner_email:
            scanner_email = "unassigned_gate"

        if scanner_email not in volunteer_groups:
            u = await user_collection.find_one({"email": scanner_email})
            if not u:
                u = await admin_collection.find_one({"email": scanner_email})
            vname = t.get("checked_in_by_name") or (u.get("full_name") or u.get("name") if u else scanner_email.split("@")[0].title())
            volunteer_groups[scanner_email] = {
                "volunteer_name": vname,
                "volunteer_email": scanner_email,
                "role": t.get("checked_in_by_role") or "Gate Scanner",
                "phone": u.get("phone") or u.get("phone_number") or "" if u else "",
                "is_assigned": False,
                "total_entries": 0,
                "online_payments": 0,
                "cash_payments": 0,
                "cash_collected": 0.0,
                "attendees": []
            }

        vg = volunteer_groups[scanner_email]
        admitted_qty = int(t.get("admitted_count") or (t.get("quantity") if t.get("is_checked_in") else 1) or 1)

        is_cash = str(t.get("payment_method", "")).upper() in ["CASH", "OFFLINE"] or bool(t.get("cash_payment"))
        is_cash_done = bool(
            t.get("cash_collected")
            or t.get("cash_collect_done")
            or t.get("cash_status") in ("COLLECTED", "APPROVED")
            or t.get("cash_approved")
        )
        amt = float(t.get("cash_amount_collected") or t.get("cash_amount") or t.get("price") or t.get("total_amount") or 0.0)

        vg["total_entries"] += admitted_qty
        if is_cash:
            vg["cash_payments"] += admitted_qty
            if is_cash_done:
                vg["cash_collected"] += amt
        else:
            vg["online_payments"] += admitted_qty

        seats = t.get("seats") or ([t.get("seat")] if t.get("seat") else [])
        holder_names = t.get("holder_names") or ([t.get("holder_name")] if t.get("holder_name") else ["Guest"])

        vg["attendees"].append({
            "ticket_ref": t.get("ticket_ref"),
            "booking_reference": t.get("booking_ref"),
            "attendee_name": t.get("holder_name") or (holder_names[0] if holder_names else "Guest"),
            "attendee_names": holder_names,
            "attendee_email": t.get("holder_email"),
            "attendee_phone": t.get("user_phone") or t.get("holder_phone"),
            "category": t.get("category") or t.get("ticket_type") or "General",
            "quantity": admitted_qty,
            "seat": ", ".join(seats) if seats else "—",
            "seats": seats,
            "payment_method": "CASH" if is_cash else t.get("payment_method", "ONLINE"),
            "cash_status": "COLLECTED" if is_cash_done else ("PENDING" if is_cash else "NOT_APPLICABLE"),
            "cash_collect_done": is_cash_done,
            "cash_collected_display": "Cash Collect Done" if is_cash_done else ("Cash Pending" if is_cash else "Online Paid"),
            "amount": amt if is_cash else float(t.get("price") or t.get("total_amount") or 0.0),
            "checked_in_at": t.get("checked_in_at") or t.get("check_in_time"),
        })

    # Sort volunteers by total entries and cash collected
    vol_list = list(volunteer_groups.values())
    for v in vol_list:
        v["cash_collected"] = round(v["cash_collected"], 2)
    vol_list.sort(key=lambda x: (x["total_entries"], x["cash_collected"]), reverse=True)

    total_entries = sum(v["total_entries"] for v in vol_list)
    total_online = sum(v["online_payments"] for v in vol_list)
    total_cash_count = sum(v["cash_payments"] for v in vol_list)
    total_cash_amount = sum(v["cash_collected"] for v in vol_list)

    reconciliation = [
        {
            "volunteer_name": v["volunteer_name"],
            "volunteer_email": v["volunteer_email"],
            "total_entries": v["total_entries"],
            "online_payments": v["online_payments"],
            "cash_passes": v["cash_payments"],
            "cash_amount_due": v["cash_collected"]
        }
        for v in vol_list
        if v["cash_collected"] > 0 or v["cash_payments"] > 0 or v["total_entries"] > 0
    ]

    return {
        "event_id": clean_eid,
        "event_title": ev.get("title", ""),
        "event_date": ev.get("date", ""),
        "event_time": ev.get("time", ""),
        "event_venue": ev.get("venue", ""),
        "total_admitted": total_entries,
        "total_online_admitted": total_online,
        "total_cash_admitted": total_cash_count,
        "total_cash_collected": round(total_cash_amount, 2),
        "volunteers": vol_list,
        "reconciliation": reconciliation
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
