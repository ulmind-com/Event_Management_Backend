from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import booking_collection, ticket_collection, event_collection, issued_ticket_collection
from app.schemas.booking import BookingCreateReq, BookingResponse, BookingStatus, PaymentStatus
from app.services.payment import PaymentService
from app.services.ticket import TicketService
from datetime import datetime, timezone
import uuid

router = APIRouter()

async def generate_tickets(booking_id: str, booking_ref: str, event_id: str, ticket_type: str, user_email: str, quantity: int, names: list):
    """Background task to generate digital tickets once confirmed."""
    docs = []
    for i in range(quantity):
        ticket_ref = f"TKT-{uuid.uuid4().hex[:8].upper()}"
        qr_token = TicketService.generate_qr_token(ticket_ref, booking_ref)
        holder_name = names[i] if names and i < len(names) else "Guest"
        
        docs.append({
            "ticket_ref": ticket_ref,
            "booking_id": booking_id,
            "event_id": event_id,
            "ticket_type": ticket_type,
            "holder_email": user_email,
            "holder_name": holder_name,
            "is_checked_in": False,
            "check_in_time": None,
            "qr_token": qr_token,
            "issued_at": datetime.now(timezone.utc)
        })
    if docs:
        await issued_ticket_collection.insert_many(docs)

        # Authoritative trigger: TICKET_CONFIRMATION
        try:
            from app.services.notification_service import NotificationService
            from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
            from app.schemas.notification import NotificationCreate

            await NotificationService.create_notification(NotificationCreate(
                recipient_user_id=user_email,
                notification_type=NotificationType.TICKET_CONFIRMATION,
                title="Tickets Confirmed",
                message=f"Your {quantity} ticket(s) for booking {booking_ref} have been successfully issued.",
                resource_type="booking",
                resource_id=booking_id,
                action_url=f"/bookings/{booking_id}",
                priority=NotificationPriority.HIGH,
                sound_category=SoundCategory.TICKET,
                deduplication_key=f"ticket_conf_{booking_id}",
                source_module="bookings",
                source_event_id=booking_ref,
            ))
        except Exception as e:
            pass

        # Schedule Event Reminders
        try:
            from app.services.notification_scheduler import NotificationSchedulerService
            ev = await event_collection.find_one({"_id": ObjectId(event_id)}) if ObjectId.is_valid(event_id) else None
            if ev and ev.get("start_date"):
                st = ev.get("start_date")
                if isinstance(st, str):
                    st = datetime.fromisoformat(st)
                await NotificationSchedulerService.schedule_event_reminders(
                    event_id=str(event_id),
                    event_title=ev.get("title", "Event"),
                    start_time=st
                )
        except Exception as e:
            pass

@router.post("/", response_model=dict)
async def create_booking(req: BookingCreateReq, bg_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    try:
        t_id = ObjectId(req.ticket_id)
        e_id = ObjectId(req.event_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid IDs")

    event = await event_collection.find_one({"_id": e_id, "is_published": True})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found or not published")
    if not event.get("registration_enabled", True) or not event.get("ticket_enabled", True):
        raise HTTPException(status_code=400, detail="Ticketing is closed for this event")

    update_result = await ticket_collection.update_one(
        {"_id": t_id, "event_id": str(e_id), "available": {"$gte": req.quantity}},
        {"$inc": {"available": -req.quantity}}
    )
    
    if update_result.modified_count == 0:
        t_check = await ticket_collection.find_one({"_id": t_id})
        if not t_check:
            raise HTTPException(status_code=404, detail="Ticket not found")
        raise HTTPException(status_code=409, detail="Insufficient tickets available")
    
    ticket = await ticket_collection.find_one({"_id": t_id})
    unit_price = float(ticket.get("price", 0.0))
    total_amount = unit_price * req.quantity
    booking_ref = f"BKG-{uuid.uuid4().hex[:10].upper()}"

    booking = {
        "booking_reference": booking_ref,
        "event_id": str(e_id),
        "ticket_id": str(t_id),
        "user_email": current_user["email"],
        "quantity": req.quantity,
        "unit_price": unit_price,
        "total_amount": total_amount,
        "booked_at": datetime.now(timezone.utc),
        "payment_reference": None
    }

    response_data = {"message": "", "booking_reference": booking_ref, "total_amount": total_amount}

    if total_amount == 0.0:
        booking["booking_status"] = BookingStatus.CONFIRMED.value
        booking["payment_status"] = PaymentStatus.NOT_REQUIRED.value
        res = await booking_collection.insert_one(booking)
        bg_tasks.add_task(
            generate_tickets, str(res.inserted_id), booking_ref, str(e_id), ticket.get("type"), current_user["email"], req.quantity, req.holder_names
        )
        response_data["message"] = "Free ticket booked and confirmed successfully."
        response_data["booking_status"] = BookingStatus.CONFIRMED.value
    else:
        booking["booking_status"] = BookingStatus.PENDING.value
        booking["payment_status"] = PaymentStatus.PENDING.value
        res = await booking_collection.insert_one(booking)
        
        payment_info = await PaymentService.initiate_payment(booking_ref, total_amount)
        response_data["message"] = "Booking reserved. Proceed to payment."
        response_data["booking_status"] = BookingStatus.PENDING.value
        response_data["payment_info"] = payment_info

    response_data["booking_id"] = str(res.inserted_id)
    return response_data

@router.get("/my")
async def get_my_bookings(current_user: dict = Depends(get_current_user)):
    bookings = await booking_collection.find({"user_email": current_user["email"]}).sort("booked_at", -1).to_list(length=100)
    for b in bookings:
        b["_id"] = str(b["_id"])
    return bookings

@router.post("/{booking_id}/cancel")
async def cancel_my_booking(booking_id: str, current_user: dict = Depends(get_current_user)):
    try:
        b_id = ObjectId(booking_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid booking ID")
        
    booking = await booking_collection.find_one({"_id": b_id, "user_email": current_user["email"]})
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
        
    if booking.get("booking_status") in [BookingStatus.CANCELLED.value, BookingStatus.EXPIRED.value]:
        raise HTTPException(status_code=400, detail="Booking already cancelled or expired")
        
    await booking_collection.update_one({"_id": b_id}, {"$set": {"booking_status": BookingStatus.CANCELLED.value}})
    
    await ticket_collection.update_one(
        {"_id": ObjectId(booking["ticket_id"])},
        {"$inc": {"available": booking["quantity"]}}
    )
    
    if booking.get("payment_status") == PaymentStatus.PAID.value:
        await PaymentService.process_refund(booking["booking_reference"], booking["total_amount"])
        await booking_collection.update_one({"_id": b_id}, {"$set": {"payment_status": PaymentStatus.REFUNDED.value}})
        
    await issued_ticket_collection.update_many({"booking_id": booking_id}, {"$set": {"is_valid": False}})
    
    return {"message": "Booking cancelled and inventory restored"}

@router.get("/my-tickets")
async def get_my_tickets(current_user: dict = Depends(get_current_user)):
    tickets = await issued_ticket_collection.find({"holder_email": current_user["email"]}).to_list(length=100)
    for t in tickets:
        t["_id"] = str(t["_id"])
        e = await event_collection.find_one({"_id": ObjectId(t["event_id"])})
        if e:
            t["event_title"] = e.get("title")
    return tickets

@router.get("/tickets/{ticket_ref}/qr")
async def get_ticket_qr(ticket_ref: str, current_user: dict = Depends(get_current_user)):
    ticket = await issued_ticket_collection.find_one({"ticket_ref": ticket_ref, "holder_email": current_user["email"]})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    b64_qr = TicketService.generate_qr_image_base64(ticket["qr_token"])
    return {"qr_image": b64_qr}
