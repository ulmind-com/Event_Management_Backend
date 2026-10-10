from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from bson import ObjectId
from app.core.admin_auth import get_current_admin
from app.core.security import decode_token
from app.core.database import issued_ticket_collection, booking_collection, admin_collection, user_collection
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

@router.post("/verify")
async def verify_ticket(req: TicketScanReq, scanner: dict = Depends(get_scanner_user)):
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
async def check_in_ticket(req: TicketScanReq, scanner: dict = Depends(get_scanner_user)):
    """Atomically marks a ticket as checked in."""
    result = TicketService.verify_qr_token(req.qr_token)
    if not result["valid"]:
        raise HTTPException(status_code=400, detail=result["error"])
        
    # Check if already checked in
    ticket = await issued_ticket_collection.find_one({"ticket_ref": result["ticket_ref"]})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
        
    if ticket.get("is_checked_in"):
        raise HTTPException(status_code=400, detail="Ticket has already been checked in!")
        
    update_result = await issued_ticket_collection.update_one(
        {"ticket_ref": result["ticket_ref"], "is_checked_in": False},
        {"$set": {
            "is_checked_in": True,
            "check_in_time": datetime.now(timezone.utc),
            "scanned_by": scanner.get("email")
        }}
    )
    
    if update_result.modified_count == 0:
        raise HTTPException(status_code=400, detail="Failed to check in. Ticket might be already checked in or invalid.")
        
    return {"message": "Successfully checked in", "ticket_ref": result["ticket_ref"]}
