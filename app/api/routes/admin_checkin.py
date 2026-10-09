from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from app.core.admin_auth import get_current_admin
from app.core.database import issued_ticket_collection, booking_collection
from app.schemas.booking import TicketScanReq
from app.services.ticket import TicketService
from datetime import datetime, timezone

router = APIRouter()

@router.post("/verify")
async def verify_ticket(req: TicketScanReq, admin: dict = Depends(get_current_admin)):
    """Scans the QR token and returns ticket status."""
    result = TicketService.verify_qr_token(req.qr_token)
    if not result["valid"]:
        raise HTTPException(status_code=400, detail=result["error"])
        
    ticket = await issued_ticket_collection.find_one({"ticket_ref": result["ticket_ref"]})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found in database")
        
    booking = await booking_collection.find_one({"_id": ObjectId(ticket["booking_id"])})
    
    return {
        "is_valid": True,
        "ticket": {
            "ticket_ref": ticket["ticket_ref"],
            "holder_name": ticket["holder_name"],
            "holder_email": ticket["holder_email"],
            "is_checked_in": ticket["is_checked_in"],
            "check_in_time": ticket["check_in_time"]
        },
        "booking_status": booking.get("booking_status") if booking else "UNKNOWN"
    }

@router.post("/check-in")
async def check_in_ticket(req: TicketScanReq, admin: dict = Depends(get_current_admin)):
    """Atomically marks a ticket as checked in."""
    result = TicketService.verify_qr_token(req.qr_token)
    if not result["valid"]:
        raise HTTPException(status_code=400, detail=result["error"])
        
    ticket = await issued_ticket_collection.find_one({"ticket_ref": result["ticket_ref"]})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
        
    if ticket.get("is_checked_in"):
        raise HTTPException(status_code=400, detail="Ticket has already been used!")
        
    booking = await booking_collection.find_one({"_id": ObjectId(ticket["booking_id"])})
    if not booking or booking.get("booking_status") != "CONFIRMED":
        raise HTTPException(status_code=400, detail="Booking is not confirmed or was cancelled")
        
    # Atomic update
    update_result = await issued_ticket_collection.update_one(
        {"ticket_ref": result["ticket_ref"], "is_checked_in": False},
        {"$set": {"is_checked_in": True, "check_in_time": datetime.now(timezone.utc)}}
    )
    
    if update_result.modified_count == 0:
        raise HTTPException(status_code=400, detail="Check-in failed. Ticket may have been used concurrently.")
        
    return {"message": "Check-in successful", "ticket_ref": result["ticket_ref"]}
