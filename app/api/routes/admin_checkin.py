from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from bson import ObjectId
from app.core.admin_auth import get_current_admin
from app.core.security import decode_token
from app.core.database import issued_ticket_collection, booking_collection, admin_collection, user_collection, event_collection
from app.schemas.booking import TicketScanReq
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
    
    if token_type == "admin":
        admin = await admin_collection.find_one({"email": email})
        if admin: return admin
        
    if token_type == "user":
        user = await user_collection.find_one({"email": email, "role": "VOLUNTEER"})
        if user: return user
        
    raise HTTPException(status_code=403, detail="Not authorized to scan tickets. Must be Admin or Volunteer.")

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
    """Scans the QR token and returns comprehensive ticket status and multi-person capacity."""
    ticket = await _resolve_ticket(req.qr_token)
    
    booking = None
    if ticket.get("booking_id") and ObjectId.is_valid(ticket["booking_id"]):
        booking = await booking_collection.find_one({"_id": ObjectId(ticket["booking_id"])})
    elif ticket.get("booking_ref"):
        booking = await booking_collection.find_one({"booking_reference": ticket["booking_ref"]})

    event = None
    if ticket.get("event_id") and ObjectId.is_valid(ticket["event_id"]):
        event = await event_collection.find_one({"_id": ObjectId(ticket["event_id"])})

    # Multi-person calculation
    quantity = int(ticket.get("quantity") or (booking.get("quantity") if booking else 1) or 1)
    admitted_count = int(ticket.get("admitted_count", 0))
    if ticket.get("is_checked_in") and admitted_count == 0:
        admitted_count = quantity

    remaining_count = max(0, quantity - admitted_count)
    is_fully_used = bool(ticket.get("is_checked_in")) or (admitted_count >= quantity)
    
    return {
        "is_valid": True,
        "ticket": {
            "ticket_ref": ticket["ticket_ref"],
            "holder_name": ticket.get("holder_name") or (booking.get("user_name") if booking else "Guest"),
            "holder_names": ticket.get("holder_names") or (booking.get("holder_names") if booking else []),
            "holder_email": ticket.get("holder_email") or (booking.get("user_email") if booking else ""),
            "ticket_type": ticket.get("ticket_type", "General Access"),
            "quantity": quantity,
            "admitted_count": admitted_count,
            "remaining_count": remaining_count,
            "is_checked_in": is_fully_used,
            "is_fully_used": is_fully_used,
            "status": "USED" if is_fully_used else ("PARTIALLY_USED" if admitted_count > 0 else "ACTIVE"),
            "check_in_time": ticket.get("check_in_time"),
            "payment_method": ticket.get("payment_method") or (booking.get("payment_method") if booking else "ONLINE"),
            "payment_status": ticket.get("payment_status") or (booking.get("payment_status") if booking else "PENDING"),
        },
        "event_title": event.get("title") if event else ticket.get("event_title"),
        "booking_reference": booking.get("booking_reference") if booking else ticket.get("booking_ref"),
        "booking_status": booking.get("booking_status") if booking else "CONFIRMED"
    }

@router.post("/check-in")
async def check_in_ticket(req: TicketScanReq, scanner: dict = Depends(get_scanner_user)):
    """
    Atomically checks in attendees on this ticket.
    Supports multi-person admissions (e.g. 3 people). Once all admitted, ticket is fully USED and invalid for reuse.
    """
    ticket = await _resolve_ticket(req.qr_token)

    booking = None
    if ticket.get("booking_id") and ObjectId.is_valid(ticket["booking_id"]):
        booking = await booking_collection.find_one({"_id": ObjectId(ticket["booking_id"])})
    elif ticket.get("booking_ref"):
        booking = await booking_collection.find_one({"booking_reference": ticket["booking_ref"]})

    quantity = int(ticket.get("quantity") or (booking.get("quantity") if booking else 1) or 1)
    current_admitted = int(ticket.get("admitted_count", 0))
    if ticket.get("is_checked_in") and current_admitted == 0:
        current_admitted = quantity

    # If already fully used, reject immediately
    if ticket.get("is_checked_in") or current_admitted >= quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Ticket has already been fully used! All {quantity} person(s) have already entered and validity has expired."
        )

    # Determine how many persons to admit this scan
    remaining = quantity - current_admitted
    to_admit = int(req.admit_count) if (req.admit_count and req.admit_count > 0) else remaining
    to_admit = min(to_admit, remaining)
    
    new_admitted = current_admitted + to_admit
    is_now_fully_used = (new_admitted >= quantity)
    now_utc = datetime.now(timezone.utc)
    scanner_email = scanner.get("email", "volunteer")

    # Atomic update on ticket
    update_res = await issued_ticket_collection.update_one(
        {"ticket_ref": ticket["ticket_ref"]},
        {
            "$set": {
                "quantity": quantity,
                "admitted_count": new_admitted,
                "is_checked_in": is_now_fully_used,
                "status": "USED" if is_now_fully_used else "PARTIALLY_USED",
                "check_in_time": now_utc,
                "scanned_by": scanner_email,
            },
            "$push": {
                "admissions_history": {
                    "admitted_count": to_admit,
                    "total_admitted": new_admitted,
                    "timestamp": now_utc,
                    "scanned_by": scanner_email,
                }
            }
        }
    )

    if update_res.modified_count == 0:
        raise HTTPException(status_code=400, detail="Failed to record check-in. Please try again.")

    # Update booking record if present
    if booking:
        await booking_collection.update_one(
            {"_id": booking["_id"]},
            {
                "$set": {
                    "checked_in_count": new_admitted,
                    "is_checked_in": is_now_fully_used,
                    "last_check_in_time": now_utc
                }
            }
        )

    return {
        "message": (
            f"ACCESS GRANTED — Admitted {to_admit} person(s). All {quantity} admissions completed; ticket is now fully used."
            if is_now_fully_used
            else f"ACCESS GRANTED — Admitted {to_admit} person(s). {quantity - new_admitted} admission(s) remaining on this ticket."
        ),
        "ticket_ref": ticket["ticket_ref"],
        "admitted_now": to_admit,
        "total_admitted": new_admitted,
        "total_quantity": quantity,
        "remaining": max(0, quantity - new_admitted),
        "is_fully_used": is_now_fully_used,
    }
