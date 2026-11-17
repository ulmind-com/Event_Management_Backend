from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from bson import ObjectId
from typing import Optional, List, Dict, Any
from app.core.admin_auth import get_current_admin
from app.core.security import decode_token
from app.core.database import (
    issued_ticket_collection, booking_collection, admin_collection,
    user_collection, event_collection, audit_log_collection, registration_collection
)
from app.schemas.booking import TicketScanReq, CashCollectReq
from app.services.ticket import TicketService
from datetime import datetime, timezone

router = APIRouter()
security = HTTPBearer()

async def get_scanner_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Allows Super Admin OR Volunteer to access scanning endpoints."""
    token = credentials.credentials
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
        
    email = payload.get("sub")
    token_type = payload.get("type")
    role = str(payload.get("role", "")).upper()
    
    if token_type == "admin" or role in ["ADMIN", "SUPER_ADMIN"]:
        admin = await admin_collection.find_one({"email": email})
        if admin:
            admin["role"] = admin.get("role") or "SUPER_ADMIN"
            return admin
        
    if token_type in ["user", "volunteer"] or role == "VOLUNTEER":
        user = await user_collection.find_one({"email": email, "role": "VOLUNTEER"})
        if user:
            user["role"] = "VOLUNTEER"
            return user
        
    raise HTTPException(status_code=403, detail="Not authorized to scan tickets. Must be Admin or Volunteer.")


async def get_volunteer_authorized_event_ids(scanner: dict):
    """
    Returns set of authorized event IDs (as strings) for the scanner.
    If the scanner is Super Admin or Admin, returns None (meaning unrestricted global access).
    If the scanner is a Volunteer, returns the set of event IDs assigned to them.
    """
    is_admin = bool(
        scanner.get("role") in ["SUPER_ADMIN", "ADMIN"]
        or scanner.get("is_super_admin")
    )
    if is_admin:
        return None  # Unrestricted

    scanner_email = (scanner.get("email") or "").lower().strip()
    authorized_eids = set()

    # 1. From user document's assigned_events
    for ev in scanner.get("assigned_events") or []:
        eid = str(ev.get("event_id", "")).strip()
        if eid:
            authorized_eids.add(eid)

    # 2. From event_collection volunteers array
    if scanner_email:
        cursor = event_collection.find(
            {
                "$or": [
                    {"volunteers.email": scanner_email},
                    {"volunteers.email": {"$regex": f"^{scanner_email}$", "$options": "i"}},
                ]
            },
            {"_id": 1}
        )
        async for doc in cursor:
            authorized_eids.add(str(doc["_id"]))

    return authorized_eids


async def _resolve_ticket(raw_token: str):
    """
    Resolves a ticket from cryptographic QR token, raw ticket reference, or booking reference.
    """
    clean_token = (raw_token or "").strip()
    if not clean_token:
        raise HTTPException(status_code=400, detail="Missing ticket code or QR token")

    # 1. Try cryptographic QR token first
    qr_res = TicketService.verify_qr_token(clean_token)
    if qr_res.get("valid") and qr_res.get("ticket_ref"):
        ticket = await issued_ticket_collection.find_one({"ticket_ref": qr_res["ticket_ref"]})
        if ticket:
            return ticket

    # 2. Try direct ticket_ref or qr_token match in issued tickets
    ticket = await issued_ticket_collection.find_one({
        "$or": [
            {"ticket_ref": clean_token},
            {"ticket_ref": clean_token.upper()},
            {"qr_token": clean_token},
            {"booking_ref": clean_token},
            {"booking_ref": clean_token.upper()},
        ]
    })
    if ticket:
        return ticket

    # 3. Try booking reference lookup
    booking = await booking_collection.find_one({
        "$or": [
            {"booking_reference": clean_token},
            {"booking_reference": clean_token.upper()},
        ]
    })
    if booking:
        ticket = await issued_ticket_collection.find_one({"booking_id": str(booking["_id"])})
        if ticket:
            return ticket

    raise HTTPException(status_code=404, detail=f"Ticket not found for code: {clean_token}")

@router.post("/verify")
async def verify_ticket(req: TicketScanReq, scanner: dict = Depends(get_scanner_user)):
    """Scans the QR token and returns comprehensive ticket status, multi-person capacity, and cash payment state."""
    ticket = await _resolve_ticket(req.qr_token)
    
    booking = None
    if ticket.get("booking_id") and ObjectId.is_valid(ticket["booking_id"]):
        booking = await booking_collection.find_one({"_id": ObjectId(ticket["booking_id"])})
    elif ticket.get("booking_ref"):
        booking = await booking_collection.find_one({"booking_reference": ticket["booking_ref"]})

    raw_event_id = str(
        ticket.get("event_id")
        or (booking.get("event_id") if booking else "")
        or ticket.get("activity_id")
        or ""
    ).strip()

    event = None
    if raw_event_id and ObjectId.is_valid(raw_event_id):
        event = await event_collection.find_one({"_id": ObjectId(raw_event_id)})
    elif ticket.get("event_id") and ObjectId.is_valid(ticket["event_id"]):
        event = await event_collection.find_one({"_id": ObjectId(ticket["event_id"])})

    ticket_event_title = (
        (event.get("title") if event else None)
        or ticket.get("event_title")
        or (booking.get("event_title") if booking else None)
        or "Unknown Event"
    )

    # ── Strict Volunteer Event-Level Access Control ──
    authorized_eids = await get_volunteer_authorized_event_ids(scanner)
    if authorized_eids is not None:
        if not raw_event_id or raw_event_id not in authorized_eids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access Denied: This ticket is for '{ticket_event_title}'. You are only authorized to scan tickets for your assigned event(s)."
            )

    # Gate verification if gate/event_id was explicitly passed
    if req.event_id and str(req.event_id).strip():
        req_eid = str(req.event_id).strip()
        if raw_event_id and raw_event_id != req_eid:
            gate_event = await event_collection.find_one({"_id": ObjectId(req_eid)}) if ObjectId.is_valid(req_eid) else None
            gate_title = gate_event.get("title") if gate_event else "Assigned Gate Event"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Event Mismatch: This ticket is for '{ticket_event_title}', but your scanner is set to '{gate_title}'."
            )

    # Multi-person calculation
    quantity = int(ticket.get("quantity") or (booking.get("quantity") if booking else 1) or 1)
    admitted_count = int(ticket.get("admitted_count", 0))
    if ticket.get("is_checked_in") and admitted_count == 0:
        admitted_count = quantity

    remaining_count = max(0, quantity - admitted_count)
    is_fully_used = bool(ticket.get("is_checked_in")) or (admitted_count >= quantity)
    
    event_cover = (
        event.get("card_image_url")
        or event.get("image_url")
        or event.get("banner_url")
        or event.get("banner_image_url")
        or event.get("cover_image")
    ) if event else ticket.get("event_cover")

    seats = ticket.get("seats") or (booking.get("seats") if booking else [])

    # Robust participant/attendee list extraction
    extracted_names = []
    if ticket.get("holder_names") and isinstance(ticket["holder_names"], list):
        extracted_names.extend([str(n).strip() for n in ticket["holder_names"] if n and str(n).strip()])

    if not extracted_names and booking:
        if booking.get("holder_names") and isinstance(booking["holder_names"], list):
            extracted_names.extend([str(n).strip() for n in booking["holder_names"] if n and str(n).strip()])
        if not extracted_names and booking.get("items") and isinstance(booking["items"], list):
            for it in booking["items"]:
                if isinstance(it, dict):
                    if it.get("holder_names") and isinstance(it["holder_names"], list):
                        extracted_names.extend([str(n).strip() for n in it["holder_names"] if n and str(n).strip()])
                    elif it.get("holder_name"):
                        extracted_names.append(str(it["holder_name"]).strip())

    if not extracted_names:
        primary = ticket.get("holder_name") or (booking.get("user_name") if booking else None)
        if primary:
            extracted_names = [str(primary).strip()]

    holder_names = extracted_names
    category = ticket.get("category") or (booking.get("category") if booking else "General")
    total_amount = (booking.get("total_amount") if booking else None) or ticket.get("total_amount") or ticket.get("total_booking_amount") or 0.0
    unit_price = (booking.get("unit_price") if booking else None) or ticket.get("unit_price")
    if unit_price is None and total_amount is not None and quantity > 0:
        unit_price = round(total_amount / quantity, 2)

    # Cash payment & check-in metadata
    is_cash_payment = (
        str(ticket.get("payment_method", "")).upper() in ["CASH", "OFFLINE"]
        or (booking and str(booking.get("payment_method", "")).upper() in ["CASH", "OFFLINE"])
        or bool(ticket.get("cash_payment"))
        or (booking and bool(booking.get("cash_payment")))
    )
    is_cash_done = bool(
        ticket.get("cash_collected")
        or ticket.get("cash_collect_done")
        or ticket.get("cash_status") in ("COLLECTED", "APPROVED")
        or ticket.get("cash_approved")
        or (booking and (booking.get("cash_collected") or booking.get("cash_collect_done") or booking.get("cash_status") in ("COLLECTED", "APPROVED") or booking.get("cash_approved")))
    )
    is_cash_pending = is_cash_payment and not is_cash_done
    cash_status = "COLLECTED" if is_cash_done else ("PENDING" if is_cash_payment else None)
    cash_display = "Cash Collect Done" if is_cash_done else ("Cash Pending" if is_cash_payment else None)

    checked_in_by = ticket.get("checked_in_by") or ticket.get("scanned_by") or (booking.get("checked_in_by") if booking else None)
    checked_in_by_name = ticket.get("checked_in_by_name") or (booking.get("checked_in_by_name") if booking else None)
    checked_in_by_role = ticket.get("checked_in_by_role") or (booking.get("checked_in_by_role") if booking else None)

    cash_coll_by = ticket.get("cash_collected_by") or (booking.get("cash_collected_by") if booking else None)
    cash_coll_by_name = ticket.get("cash_collected_by_name") or (booking.get("cash_collected_by_name") if booking else None)
    cash_coll_at = ticket.get("cash_collected_at") or (booking.get("cash_collected_at") if booking else None)

    scan_msg = "Ticket is valid for admission."
    if is_fully_used:
        scanner_disp = checked_in_by_name or checked_in_by or "Gate Scanner"
        time_val = ticket.get("check_in_time") or ticket.get("checked_in_at")
        time_str = f" at {time_val}" if time_val else ""
        scan_msg = f"Ticket Already Scanned / Already Checked In! All {quantity} person(s) were admitted by {scanner_disp}{time_str}. Duplicate entry rejected."

    return {
        "is_valid": not is_fully_used,
        "valid": not is_fully_used,
        "allowed": not is_fully_used,
        "already_checked_in": is_fully_used,
        "duplicate_scan": is_fully_used,
        "message": scan_msg,
        "event_id": raw_event_id,
        "ticket": {
            "ticket_ref": ticket["ticket_ref"],
            "holder_name": ticket.get("holder_name") or (booking.get("user_name") if booking else "Guest"),
            "holder_names": holder_names,
            "holder_email": ticket.get("holder_email") or (booking.get("user_email") if booking else ""),
            "user_phone": ticket.get("holder_phone") or (booking.get("user_phone") if booking else ""),
            "ticket_type": ticket.get("ticket_type", "General Access"),
            "category": category,
            "quantity": quantity,
            "seats": seats,
            "seat": ticket.get("seat") or (", ".join(seats) if seats else None),
            "admitted_count": admitted_count,
            "remaining_count": remaining_count,
            "is_checked_in": is_fully_used,
            "is_fully_used": is_fully_used,
            "status": "USED" if is_fully_used else ("PARTIALLY_USED" if admitted_count > 0 else "ACTIVE"),
            "check_in_time": ticket.get("check_in_time") or ticket.get("checked_in_at"),
            "checked_in_by": checked_in_by,
            "checked_in_by_name": checked_in_by_name,
            "checked_in_by_role": checked_in_by_role,
            "scanned_by": checked_in_by,
            "payment_method": "CASH" if is_cash_payment else (ticket.get("payment_method") or (booking.get("payment_method") if booking else "ONLINE")),
            "payment_status": "PAID" if is_cash_done else (ticket.get("payment_status") or (booking.get("payment_status") if booking else "PENDING")),
            "is_cash": is_cash_payment,
            "is_cash_pending": is_cash_pending,
            "cash_status": cash_status,
            "cash_collected": is_cash_done,
            "cash_collect_done": is_cash_done,
            "cash_collected_display": cash_display,
            "cash_collected_by": cash_coll_by,
            "cash_collected_by_name": cash_coll_by_name,
            "cash_collected_at": cash_coll_at,
            "total_amount": total_amount,
            "unit_price": unit_price,
        },
        "event_title": ticket_event_title,
        "event_date": event.get("date") if event else ticket.get("event_date"),
        "event_time": event.get("time") if event else ticket.get("event_time"),
        "event_venue": event.get("venue") if event else ticket.get("event_venue"),
        "event_cover": event_cover,
        "booking_reference": booking.get("booking_reference") if booking else ticket.get("booking_ref"),
        "booking_status": booking.get("booking_status") if booking else "CONFIRMED",
        "is_checked_in": is_fully_used,
        "is_fully_used": is_fully_used,
        "admitted_count": admitted_count,
        "remaining_count": remaining_count,
        "ticket_ref": ticket.get("ticket_ref"),
        "is_cash": is_cash_payment,
        "is_cash_pending": is_cash_pending,
        "cash_status": cash_status,
        "cash_collect_done": is_cash_done,
        "cash_collected_display": cash_display,
        "checked_in_by": checked_in_by,
        "checked_in_by_name": checked_in_by_name,
    }


@router.post("/check-in")
async def check_in_ticket(req: TicketScanReq, scanner: dict = Depends(get_scanner_user)):
    """
    Atomically checks in attendees on this ticket.
    Supports multi-person admissions (e.g. 3 people). Once all admitted, ticket is fully USED and invalid for reuse.
    Strictly verifies volunteer assignment against ticket event before admission.
    Records volunteer/admin identity and auto-collects cash if ticket is cash payment (or explicitly requested).
    """
    ticket = await _resolve_ticket(req.qr_token)

    booking = None
    if ticket.get("booking_id") and ObjectId.is_valid(ticket["booking_id"]):
        booking = await booking_collection.find_one({"_id": ObjectId(ticket["booking_id"])})
    elif ticket.get("booking_ref"):
        booking = await booking_collection.find_one({"booking_reference": ticket["booking_ref"]})

    raw_event_id = str(
        ticket.get("event_id")
        or (booking.get("event_id") if booking else "")
        or ticket.get("activity_id")
        or ""
    ).strip()

    event = None
    if raw_event_id and ObjectId.is_valid(raw_event_id):
        event = await event_collection.find_one({"_id": ObjectId(raw_event_id)})
    elif ticket.get("event_id") and ObjectId.is_valid(ticket["event_id"]):
        event = await event_collection.find_one({"_id": ObjectId(ticket["event_id"])})

    ticket_event_title = (
        (event.get("title") if event else None)
        or ticket.get("event_title")
        or (booking.get("event_title") if booking else None)
        or "Unknown Event"
    )

    # ── Strict Volunteer Event-Level Access Control ──
    authorized_eids = await get_volunteer_authorized_event_ids(scanner)
    if authorized_eids is not None:
        if not raw_event_id or raw_event_id not in authorized_eids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access Denied: This ticket is for '{ticket_event_title}'. You are only authorized to admit attendees for your assigned event(s)."
            )

    # Gate verification if gate/event_id was explicitly passed
    if req.event_id and str(req.event_id).strip():
        req_eid = str(req.event_id).strip()
        if raw_event_id and raw_event_id != req_eid:
            gate_event = await event_collection.find_one({"_id": ObjectId(req_eid)}) if ObjectId.is_valid(req_eid) else None
            gate_title = gate_event.get("title") if gate_event else "Assigned Gate Event"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Event Mismatch: This ticket is for '{ticket_event_title}', but your scanner is set to '{gate_title}'."
            )

    quantity = int(ticket.get("quantity") or (booking.get("quantity") if booking else 1) or 1)
    current_admitted = int(ticket.get("admitted_count", 0))
    if ticket.get("is_checked_in") and current_admitted == 0:
        current_admitted = quantity

    # If already fully used, reject immediately
    if ticket.get("is_checked_in") or current_admitted >= quantity:
        scanner_disp = (
            ticket.get("checked_in_by_name")
            or ticket.get("checked_in_by")
            or (booking.get("checked_in_by_name") if booking else None)
            or (booking.get("checked_in_by") if booking else "Gate Scanner")
        )
        time_val = ticket.get("check_in_time") or ticket.get("checked_in_at")
        time_str = f" at {time_val}" if time_val else ""
        raise HTTPException(
            status_code=400,
            detail=f"Ticket Already Scanned / Already Checked In! All {quantity} person(s) have already entered (admitted by {scanner_disp}{time_str}). Duplicate entry rejected."
        )

    # Determine how many persons to admit this scan
    remaining = quantity - current_admitted
    to_admit = int(req.admit_count) if (req.admit_count and req.admit_count > 0) else remaining
    to_admit = min(to_admit, remaining)
    
    new_admitted = current_admitted + to_admit
    is_now_fully_used = (new_admitted >= quantity)
    now_utc = datetime.now(timezone.utc)
    
    scanner_email = (scanner.get("email") or "volunteer").strip().lower()
    scanner_name = scanner.get("full_name") or scanner.get("name") or (scanner_email.split("@")[0].title() if scanner_email else "Volunteer")
    scanner_role = str(scanner.get("role", "VOLUNTEER")).upper()

    # Cash payment evaluation
    is_cash_payment = (
        str(ticket.get("payment_method", "")).upper() in ["CASH", "OFFLINE"]
        or (booking and str(booking.get("payment_method", "")).upper() in ["CASH", "OFFLINE"])
        or bool(ticket.get("cash_payment"))
        or (booking and bool(booking.get("cash_payment")))
    )
    already_cash_done = bool(
        ticket.get("cash_collected")
        or ticket.get("cash_collect_done")
        or ticket.get("cash_status") in ("COLLECTED", "APPROVED")
        or ticket.get("cash_approved")
        or (booking and (booking.get("cash_collected") or booking.get("cash_collect_done") or booking.get("cash_status") in ("COLLECTED", "APPROVED") or booking.get("cash_approved")))
    )
    is_cash_pending = is_cash_payment and not already_cash_done

    should_collect_cash = bool(
        req.collect_cash is True
        or req.cash_collected is True
        or (is_cash_pending and req.collect_cash is not False)
    )

    cash_amt = float(
        req.cash_amount
        or (booking.get("total_amount") if booking else None)
        or ticket.get("total_amount")
        or ticket.get("total_booking_amount")
        or 0.0
    )

    # Atomic update on ticket
    ticket_update_fields: Dict[str, Any] = {
        "quantity": quantity,
        "admitted_count": new_admitted,
        "is_checked_in": is_now_fully_used,
        "status": "USED" if is_now_fully_used else "PARTIALLY_USED",
        "check_in_time": now_utc,
        "checked_in_at": now_utc,
        "scanned_by": scanner_email,
        "checked_in_by": scanner_email,
        "checked_in_by_name": scanner_name,
        "checked_in_by_role": scanner_role,
    }
    if should_collect_cash:
        ticket_update_fields.update({
            "payment_method": "CASH",
            "payment_status": "PAID",
            "payment_status_display": "SUCCESS",
            "is_paid": True,
            "cash_status": "COLLECTED",
            "cash_collected": True,
            "cash_collect_done": True,
            "cash_approved": True,
            "cash_collected_by": scanner_email,
            "cash_collected_by_name": scanner_name,
            "cash_collected_at": now_utc,
            "cash_amount_collected": cash_amt,
            "approved_by": scanner_email,
            "approved_at": now_utc,
            "paid_at": now_utc,
        })
    elif already_cash_done:
        ticket_update_fields.update({
            "cash_status": "COLLECTED",
            "cash_collected": True,
            "cash_collect_done": True,
            "payment_status": "PAID",
            "payment_status_display": "SUCCESS",
        })

    admit_history_entry = {
        "admitted_count": to_admit,
        "total_admitted": new_admitted,
        "timestamp": now_utc,
        "scanned_by": scanner_email,
        "scanned_by_name": scanner_name,
        "scanned_by_role": scanner_role,
        "cash_collected": should_collect_cash,
        "cash_amount": cash_amt if should_collect_cash else 0.0,
        "notes": req.notes,
    }

    update_res = await issued_ticket_collection.update_one(
        {"ticket_ref": ticket["ticket_ref"]},
        {
            "$set": ticket_update_fields,
            "$push": {"admissions_history": admit_history_entry}
        }
    )

    if update_res.modified_count == 0:
        raise HTTPException(status_code=400, detail="Failed to record check-in. Please try again.")

    # Update booking record if present
    if booking:
        booking_update_fields: Dict[str, Any] = {
            "checked_in_count": new_admitted,
            "is_checked_in": is_now_fully_used,
            "last_check_in_time": now_utc,
            "checked_in_at": now_utc,
            "checked_in_by": scanner_email,
            "checked_in_by_name": scanner_name,
            "checked_in_by_role": scanner_role,
            "scanned_by": scanner_email,
            "is_present": True,
            "attendance_status": "PRESENT",
            "attended_at": now_utc,
            "attended_by": scanner_email,
        }
        if should_collect_cash:
            payment_ref = f"CASH-{scanner_email.split('@')[0]}-{int(now_utc.timestamp())}"
            booking_update_fields.update({
                "payment_status": "PAID",
                "payment_status_display": "SUCCESS",
                "payment_method": "CASH",
                "cash_status": "COLLECTED",
                "cash_collected": True,
                "cash_collect_done": True,
                "cash_received": True,
                "cash_approved": True,
                "cash_collected_by": scanner_email,
                "cash_collected_by_name": scanner_name,
                "cash_collected_at": now_utc,
                "cash_amount_collected": cash_amt,
                "approved_by": scanner_email,
                "approved_at": now_utc,
                "paid_at": now_utc,
                "payment_reference": booking.get("payment_reference") or payment_ref,
            })
            # Also sync all sibling issued tickets under this booking
            await issued_ticket_collection.update_many(
                {"booking_id": str(booking["_id"])},
                {"$set": {
                    "payment_status": "PAID",
                    "payment_method": "CASH",
                    "is_paid": True,
                    "cash_status": "COLLECTED",
                    "cash_collected": True,
                    "cash_collect_done": True,
                    "cash_approved": True,
                    "cash_collected_by": scanner_email,
                    "cash_collected_by_name": scanner_name,
                    "cash_collected_at": now_utc,
                }}
            )

        await booking_collection.update_one(
            {"_id": booking["_id"]},
            {"$set": booking_update_fields}
        )

    # Sync direct registration if exists
    reg_match = []
    if ticket.get("booking_ref"):
        reg_match.append({"order_reference": ticket["booking_ref"]})
    if booking and booking.get("booking_reference"):
        reg_match.append({"order_reference": booking["booking_reference"]})
    if ticket.get("booking_id"):
        reg_match.append({"_id": str(ticket["booking_id"])})
        if ObjectId.is_valid(ticket["booking_id"]):
            reg_match.append({"_id": ObjectId(ticket["booking_id"])})
    if reg_match:
        reg_up: Dict[str, Any] = {
            "is_present": True,
            "attendance_status": "PRESENT",
            "attended_at": now_utc,
            "attended_by": scanner_email,
            "checked_in_by": scanner_email,
            "checked_in_by_name": scanner_name,
            "checked_in_by_role": scanner_role,
            "checked_in_at": now_utc,
            "last_check_in_time": now_utc,
        }
        if should_collect_cash:
            reg_up.update({
                "payment_status": "PAID",
                "payment_status_display": "SUCCESS",
                "cash_status": "COLLECTED",
                "cash_collected": True,
                "cash_collect_done": True,
                "cash_collected_by": scanner_email,
                "cash_collected_by_name": scanner_name,
                "cash_collected_at": now_utc,
                "cash_approved": True,
            })
        await registration_collection.update_many({"$or": reg_match}, {"$set": reg_up})

    holder_name = ticket.get("holder_name") or (booking.get("user_name") if booking else "Guest")
    holder_email = ticket.get("holder_email") or (booking.get("user_email") if booking else "")
    b_ref = booking.get("booking_reference") if booking else ticket.get("booking_ref")

    # Audit log
    try:
        await audit_log_collection.insert_one({
            "user_email": scanner_email,
            "role": scanner_role,
            "action": "volunteer_ticket_checkin",
            "target_type": "ticket",
            "target_id": ticket["ticket_ref"],
            "metadata": {
                "booking_reference": b_ref,
                "holder_name": holder_name,
                "holder_email": holder_email,
                "admitted_count": to_admit,
                "is_fully_used": is_now_fully_used,
                "cash_collected": should_collect_cash,
                "cash_amount": cash_amt if should_collect_cash else 0.0,
                "scanner_name": scanner_name,
                "scanner_role": scanner_role,
            },
            "timestamp": now_utc
        })
    except Exception:
        pass

    final_cash_done = bool(should_collect_cash or already_cash_done)
    final_cash_status = "COLLECTED" if final_cash_done else (ticket.get("cash_status") or (booking.get("cash_status") if booking else ("PENDING" if is_cash_payment else None)))
    final_cash_display = "Cash Collect Done" if final_cash_done else ("Cash Pending" if is_cash_payment else None)

    # Real-time WebSocket broadcast
    ev_id = str(ticket.get("event_id") or (booking.get("event_id") if booking else ""))
    if ev_id:
        try:
            from app.api.routes.ws_live import ws_manager
            await ws_manager.broadcast_to_event(ev_id, {
                "event": "ticket_checked_in",
                "ticket_ref": ticket["ticket_ref"],
                "holder_name": holder_name,
                "holder_email": holder_email,
                "checked_in_by": scanner_email,
                "checked_in_by_name": scanner_name,
                "checked_in_by_role": scanner_role,
                "checked_in_at": now_utc.isoformat(),
                "cash_status": final_cash_status,
                "cash_collect_done": final_cash_done,
                "cash_collected_display": final_cash_display,
                "admitted_count": to_admit,
                "is_fully_used": is_now_fully_used,
            })
        except Exception:
            pass

    return {
        "success": True,
        "message": (
            f"ACCESS GRANTED — Admitted {to_admit} person(s). All {quantity} admissions completed; ticket is now fully used."
            if is_now_fully_used
            else f"ACCESS GRANTED — Admitted {to_admit} person(s). {quantity - new_admitted} admission(s) remaining on this ticket."
        ),
        "ticket_ref": ticket["ticket_ref"],
        "booking_reference": b_ref,
        "holder_name": holder_name,
        "holder_email": holder_email,
        "admitted_now": to_admit,
        "total_admitted": new_admitted,
        "total_quantity": quantity,
        "remaining": max(0, quantity - new_admitted),
        "is_fully_used": is_now_fully_used,
        "checked_in_by": scanner_email,
        "checked_in_by_name": scanner_name,
        "checked_in_by_role": scanner_role,
        "check_in_time": now_utc.isoformat(),
        "checked_in_at": now_utc.isoformat(),
        "payment_method": "CASH" if is_cash_payment else ticket.get("payment_method", "ONLINE"),
        "payment_status": "PAID" if final_cash_done else (ticket.get("payment_status") or (booking.get("payment_status") if booking else "PENDING")),
        "cash_status": final_cash_status,
        "cash_collected": final_cash_done,
        "cash_collect_done": final_cash_done,
        "cash_collected_display": final_cash_display,
        "cash_collected_by": scanner_email if should_collect_cash else (ticket.get("cash_collected_by") or (booking.get("cash_collected_by") if booking else None)),
        "cash_collected_by_name": scanner_name if should_collect_cash else (ticket.get("cash_collected_by_name") or (booking.get("cash_collected_by_name") if booking else None)),
        "cash_collected_at": now_utc.isoformat() if should_collect_cash else (ticket.get("cash_collected_at") or (booking.get("cash_collected_at") if booking else None)),
    }


@router.get("/stats")
async def get_volunteer_shift_stats(
    event_id: Optional[str] = Query(None),
    scanner: dict = Depends(get_scanner_user)
):
    """
    Returns live shift statistics for the logged in volunteer or admin:
    - total_checkins (admitted attendees)
    - online_checkins_count
    - cash_collected_count
    - total_cash_collected (₹ amount)
    """
    scanner_email = (scanner.get("email") or "").strip().lower()
    query: Dict[str, Any] = {
        "$or": [
            {"checked_in_by": scanner_email},
            {"scanned_by": scanner_email},
            {"cash_collected_by": scanner_email}
        ]
    }
    if event_id:
        query["event_id"] = str(event_id)

    tickets = await issued_ticket_collection.find(query).to_list(length=3000)
    total_checkins = sum(int(t.get("admitted_count") or (t.get("quantity") if t.get("is_checked_in") else 1) or 1) for t in tickets) if tickets else 0
    
    cash_tickets = [
        t for t in tickets
        if bool(
            t.get("cash_collected")
            or t.get("cash_collect_done")
            or t.get("cash_status") in ("COLLECTED", "APPROVED")
            or t.get("cash_approved")
        )
        and (t.get("cash_collected_by") == scanner_email or t.get("checked_in_by") == scanner_email or t.get("scanned_by") == scanner_email)
    ]
    cash_collected_count = sum(int(t.get("admitted_count") or (t.get("quantity") if t.get("is_checked_in") else 1) or 1) for t in cash_tickets) if cash_tickets else 0
    online_checkins_count = max(0, total_checkins - cash_collected_count)
    total_cash_collected = sum(
        float(t.get("cash_amount_collected") or t.get("cash_amount") or t.get("price") or t.get("total_amount") or 0.0)
        for t in cash_tickets
    )

    return {
        "volunteer_email": scanner_email,
        "event_id": event_id,
        "total_tickets_scanned": len(tickets),
        "total_checkins": total_checkins,
        "online_checkins_count": online_checkins_count,
        "cash_collected_count": cash_collected_count,
        "total_cash_collected": round(total_cash_collected, 2),
    }


@router.get("/checkin-history")
async def get_checkin_history(
    limit: int = Query(50, ge=1, le=500),
    event_id: Optional[str] = Query(None),
    scanner: dict = Depends(get_scanner_user)
):
    """
    Returns recent ticket check-ins showing which volunteer scanned which ticket,
    attendee holder info, timestamps, and whether cash collection was completed.
    """
    query: Dict[str, Any] = {"is_checked_in": True}
    if event_id:
        query["event_id"] = str(event_id)
    
    tix = await issued_ticket_collection.find(query).sort("check_in_time", -1).to_list(length=limit)
    res = []
    for t in tix:
        c_done = bool(t.get("cash_collected") or t.get("cash_collect_done") or t.get("cash_status") in ("COLLECTED", "APPROVED") or t.get("cash_approved"))
        is_cash = str(t.get("payment_method", "")).upper() == "CASH" or bool(t.get("cash_payment"))
        res.append({
            "ticket_ref": t.get("ticket_ref"),
            "booking_reference": t.get("booking_ref"),
            "holder_name": t.get("holder_name"),
            "holder_email": t.get("holder_email"),
            "ticket_type": t.get("ticket_type"),
            "event_title": t.get("event_title"),
            "event_id": t.get("event_id"),
            "quantity": t.get("quantity", 1),
            "admitted_count": t.get("admitted_count", 1),
            "is_fully_used": bool(t.get("is_checked_in")),
            "checked_in_by": t.get("checked_in_by") or t.get("scanned_by"),
            "checked_in_by_name": t.get("checked_in_by_name"),
            "checked_in_by_role": t.get("checked_in_by_role"),
            "check_in_time": t.get("check_in_time") or t.get("checked_in_at"),
            "payment_method": t.get("payment_method", "ONLINE"),
            "payment_status": "PAID" if c_done else t.get("payment_status", "PAID"),
            "cash_status": "COLLECTED" if c_done else (t.get("cash_status") or ("PENDING" if is_cash else None)),
            "cash_collected": c_done,
            "cash_collect_done": c_done,
            "cash_collected_display": "Cash Collect Done" if c_done else ("Cash Pending" if is_cash else None),
            "cash_collected_by": t.get("cash_collected_by"),
            "cash_collected_by_name": t.get("cash_collected_by_name"),
            "cash_collected_at": t.get("cash_collected_at"),
        })
    return res


@router.post("/collect-cash")
@router.post("/{ticket_ref}/collect-cash")
async def collect_ticket_cash(
    ticket_ref: Optional[str] = None,
    req: Optional[CashCollectReq] = None,
    scanner: dict = Depends(get_scanner_user)
):
    """
    Volunteer or Admin marks cash payment as collected at the venue for a cash ticket or booking.
    Updates cash_status to 'COLLECTED', payment_status to 'PAID', and sets 'Cash Collect Done' across records.
    """
    token_or_ref = (req.qr_token if req and req.qr_token else None) or (req.ticket_ref if req and req.ticket_ref else None) or ticket_ref
    if not token_or_ref:
        raise HTTPException(status_code=400, detail="Missing ticket_ref or qr_token")
    
    ticket = await _resolve_ticket(token_or_ref)
    booking = None
    if ticket.get("booking_id") and ObjectId.is_valid(ticket["booking_id"]):
        booking = await booking_collection.find_one({"_id": ObjectId(ticket["booking_id"])})
    elif ticket.get("booking_ref"):
        booking = await booking_collection.find_one({"booking_reference": ticket["booking_ref"]})
        
    scanner_email = (scanner.get("email") or "volunteer").strip().lower()
    scanner_name = scanner.get("full_name") or scanner.get("name") or (scanner_email.split("@")[0].title() if scanner_email else "Volunteer")
    scanner_role = str(scanner.get("role", "VOLUNTEER")).upper()
    now_utc = datetime.now(timezone.utc)
    
    cash_amt = float(
        (req.amount if req and req.amount is not None else None)
        or (booking.get("total_amount") if booking else None)
        or ticket.get("total_amount")
        or ticket.get("total_booking_amount")
        or 0.0
    )
    
    # 1. Update ticket
    await issued_ticket_collection.update_many(
        {"$or": [{"ticket_ref": ticket["ticket_ref"]}, {"booking_id": str(booking["_id"]) if booking else ""}]},
        {"$set": {
            "payment_method": "CASH",
            "payment_status": "PAID",
            "payment_status_display": "SUCCESS",
            "is_paid": True,
            "cash_status": "COLLECTED",
            "cash_collected": True,
            "cash_collect_done": True,
            "cash_approved": True,
            "cash_collected_by": scanner_email,
            "cash_collected_by_name": scanner_name,
            "cash_collected_at": now_utc,
            "cash_amount_collected": cash_amt,
            "approved_by": scanner_email,
            "approved_at": now_utc,
            "paid_at": now_utc,
        }}
    )
    
    # 2. Update booking
    if booking:
        payment_ref = f"CASH-{scanner_email.split('@')[0]}-{int(now_utc.timestamp())}"
        await booking_collection.update_one(
            {"_id": booking["_id"]},
            {"$set": {
                "payment_status": "PAID",
                "payment_status_display": "SUCCESS",
                "payment_method": "CASH",
                "cash_status": "COLLECTED",
                "cash_collected": True,
                "cash_collect_done": True,
                "cash_received": True,
                "cash_approved": True,
                "cash_collected_by": scanner_email,
                "cash_collected_by_name": scanner_name,
                "cash_collected_at": now_utc,
                "cash_amount_collected": cash_amt,
                "approved_by": scanner_email,
                "approved_at": now_utc,
                "paid_at": now_utc,
                "payment_reference": booking.get("payment_reference") or payment_ref,
            }}
        )

    # 3. Update direct registration if exists
    reg_match = []
    if ticket.get("booking_ref"):
        reg_match.append({"order_reference": ticket["booking_ref"]})
    if booking and booking.get("booking_reference"):
        reg_match.append({"order_reference": booking["booking_reference"]})
    if ticket.get("booking_id"):
        reg_match.append({"_id": str(ticket["booking_id"])})
        if ObjectId.is_valid(ticket["booking_id"]):
            reg_match.append({"_id": ObjectId(ticket["booking_id"])})
    if reg_match:
        await registration_collection.update_many(
            {"$or": reg_match},
            {"$set": {
                "payment_status": "PAID",
                "payment_status_display": "SUCCESS",
                "cash_status": "COLLECTED",
                "cash_collected": True,
                "cash_collect_done": True,
                "cash_collected_by": scanner_email,
                "cash_collected_by_name": scanner_name,
                "cash_collected_at": now_utc,
                "cash_approved": True,
            }}
        )
        
    return {
        "success": True,
        "message": f"Cash of ₹{cash_amt:.2f} collected successfully by {scanner_name}.",
        "ticket_ref": ticket["ticket_ref"],
        "booking_reference": booking.get("booking_reference") if booking else ticket.get("booking_ref"),
        "cash_status": "COLLECTED",
        "cash_collected": True,
        "cash_collect_done": True,
        "cash_collected_display": "Cash Collect Done",
        "cash_collected_by": scanner_email,
        "cash_collected_by_name": scanner_name,
        "cash_collected_at": now_utc.isoformat(),
        "cash_amount": cash_amt
    }


@router.get("/assigned-events")
async def get_volunteer_assigned_events(scanner: dict = Depends(get_scanner_user)):
    """
    Returns events assigned to the logged-in Volunteer (or all active events if Admin).
    """
    scanner_email = (scanner.get("email") or "").lower().strip()
    is_admin = bool(scanner.get("role") in ["SUPER_ADMIN", "ADMIN"] or scanner.get("is_super_admin"))

    if is_admin:
        events = await event_collection.find({}).sort("date", -1).to_list(length=100)
    else:
        events = await event_collection.find({
            "$or": [
                {"volunteers.email": scanner_email},
                {"volunteers.email": {"$regex": f"^{scanner_email}$", "$options": "i"}},
            ]
        }).sort("date", -1).to_list(length=100)

        # Fallback to user document's assigned_events
        if not events:
            assigned_ids = []
            for ev in scanner.get("assigned_events", []):
                eid = ev.get("event_id")
                if eid and ObjectId.is_valid(eid):
                    assigned_ids.append(ObjectId(eid))
            if assigned_ids:
                events = await event_collection.find({"_id": {"$in": assigned_ids}}).to_list(length=100)

    results = []
    for ev in events:
        eid = str(ev["_id"])
        volunteers = ev.get("volunteers") or []
        my_role = "Entrance Gate Scanner"
        for v in volunteers:
            if str(v.get("email", "")).lower() == scanner_email:
                my_role = v.get("role") or my_role
                break

        results.append({
            "event_id": eid,
            "id": eid,
            "title": ev.get("title", "Event"),
            "date": ev.get("date", ""),
            "time": ev.get("time", ""),
            "venue": ev.get("venue", ""),
            "status": ev.get("event_status") or "UPCOMING",
            "is_published": ev.get("is_published", False),
            "cover_image": (
                ev.get("card_image_url")
                or ev.get("image_url")
                or ev.get("banner_url")
                or ev.get("banner_image_url")
            ),
            "my_role": my_role,
            "total_volunteers": len(volunteers),
        })

    return results
