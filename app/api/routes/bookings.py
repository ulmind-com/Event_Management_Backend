from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import booking_collection, ticket_collection
from app.schemas.action import BookingReq
from datetime import datetime, timezone

router = APIRouter()

@router.post("/")
async def book_ticket(req: BookingReq, current_user: dict = Depends(get_current_user)):
    try:
        obj_id = ObjectId(req.ticket_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid ticket ID")

    ticket = await ticket_collection.find_one({"_id": obj_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if ticket.get("available", 0) < req.quantity:
        raise HTTPException(status_code=400, detail="Not enough tickets available")

    # Payment check simulation could be here
    # For now, we assume successful or free
    total_price = ticket.get("price", 0) * req.quantity

    booking = {
        "email": current_user["email"],
        "ticket_id": req.ticket_id,
        "event_id": ticket.get("event_id"),
        "quantity": req.quantity,
        "total_price": total_price,
        "booked_at": datetime.now(timezone.utc),
        "status": "confirmed"
    }

    await booking_collection.insert_one(booking)
    booking['_id'] = str(booking['_id'])
    await ticket_collection.update_one({"_id": obj_id}, {"$inc": {"available": -req.quantity}})

    return {"message": "Ticket booked successfully", "booking": booking}

@router.get("/")
async def get_my_bookings(current_user: dict = Depends(get_current_user)):
    bookings = await booking_collection.find({"email": current_user["email"]}).to_list(length=100)
    for b in bookings:
        b["_id"] = str(b["_id"])
    return bookings

