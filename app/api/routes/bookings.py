from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import booking_collection, ticket_collection, event_collection, issued_ticket_collection
from app.schemas.booking import BookingCreateReq, BookingResponse, BookingStatus, PaymentStatus
from app.services.payment import PaymentService
from app.services.ticket import TicketService
from datetime import datetime, timezone, timedelta
import re
import uuid

router = APIRouter()

async def generate_tickets(booking_id: str, booking_ref: str, event_id: str, ticket_type: str, user_email: str, quantity: int, names: list, payment_method: str = "ONLINE", payment_status: str = "PENDING"):
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
            "payment_method": payment_method,
            "payment_status": payment_status,
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

def check_event_deadline(event: dict) -> None:
    """Enforces registration deadline and event conclusion for bookings."""
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)

    # 1. Check explicit registration deadline
    deadline = event.get("registration_deadline")
    if deadline:
        try:
            if isinstance(deadline, str):
                dl_str = deadline.strip()
                if len(dl_str) == 10 and dl_str.count("-") == 2:
                    y, m, d = [int(x) for x in dl_str.split("-")]
                    dl_dt = datetime(y, m, d, 23, 59, 59, 999999, tzinfo=ist)
                else:
                    dl_dt = datetime.fromisoformat(dl_str)
                    if dl_dt.tzinfo is None:
                        dl_dt = dl_dt.replace(tzinfo=ist)
            elif isinstance(deadline, datetime):
                dl_dt = deadline if deadline.tzinfo else deadline.replace(tzinfo=ist)
            else:
                dl_dt = None

            if dl_dt and now_ist > dl_dt:
                raise HTTPException(
                    status_code=400,
                    detail=f"Registration deadline ({deadline}) has passed for this event. Ticket booking is closed."
                )
        except HTTPException:
            raise
        except Exception:
            pass

    # 2. Check event date & time
    ev_date = event.get("date")
    if ev_date:
        try:
            if isinstance(ev_date, str):
                date_str = ev_date.strip()
                if len(date_str) == 10 and date_str.count("-") == 2:
                    y, m, d = [int(x) for x in date_str.split("-")]
                    ev_time = event.get("time")
                    h, minute = 23, 59
                    if ev_time and isinstance(ev_time, str):
                        m_time = re.search(r'(\d{1,2}):(\d{2})(?:\s*([aApP][mM]))?', ev_time)
                        if m_time:
                            th = int(m_time.group(1))
                            tm = int(m_time.group(2))
                            mer = m_time.group(3)
                            if mer:
                                if mer.upper() == "PM" and th < 12:
                                    th += 12
                                elif mer.upper() == "AM" and th == 12:
                                    th = 0
                            h, minute = th, tm
                    ev_dt = datetime(y, m, d, h, minute, 0, tzinfo=ist)
                else:
                    ev_dt = datetime.fromisoformat(date_str)
                    if ev_dt.tzinfo is None:
                        ev_dt = ev_dt.replace(tzinfo=ist)
            elif isinstance(ev_date, datetime):
                ev_dt = ev_date if ev_date.tzinfo else ev_date.replace(tzinfo=ist)
            else:
                ev_dt = None

            if ev_dt and now_ist > ev_dt:
                raise HTTPException(
                    status_code=400,
                    detail=f"This event concluded on {ev_date}. Ticket booking is closed."
                )
        except HTTPException:
            raise
        except Exception:
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
    if event.get("event_status") in ["completed", "cancelled"]:
        raise HTTPException(status_code=400, detail=f"This event is {event.get('event_status')}. Ticket booking is closed.")

    check_event_deadline(event)

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
    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(minutes=15)

    payment_method = (getattr(req, "payment_method", None) or "ONLINE").strip().upper()
    if payment_method not in ["ONLINE", "CASH", "OFFLINE"]:
        payment_method = "ONLINE"

    booking = {
        "booking_reference": booking_ref,
        "event_id": str(e_id),
        "ticket_id": str(t_id),
        "user_email": current_user["email"],
        "user_name": current_user.get("name") or current_user.get("full_name") or "Attendee",
        "user_phone": current_user.get("phone_number") or current_user.get("phone") or "",
        "holder_names": req.holder_names or [],
        "quantity": req.quantity,
        "unit_price": unit_price,
        "total_amount": total_amount,
        "booked_at": now_utc,
        "expires_at": expires_at,
        "payment_method": payment_method,
        "payment_reference": None
    }

    response_data = {
        "message": "",
        "booking_reference": booking_ref,
        "total_amount": total_amount,
        "payment_method": payment_method
    }

    if total_amount == 0.0:
        booking["booking_status"] = BookingStatus.CONFIRMED.value
        booking["payment_status"] = PaymentStatus.NOT_REQUIRED.value
        booking["payment_status_display"] = "SUCCESS"
        booking["confirmed_at"] = now_utc
        res = await booking_collection.insert_one(booking)
        bg_tasks.add_task(
            generate_tickets, str(res.inserted_id), booking_ref, str(e_id), ticket.get("type"), current_user["email"], req.quantity, req.holder_names, payment_method, "NOT_REQUIRED"
        )
        await event_collection.update_one({"_id": e_id}, {"$inc": {"registered_count": req.quantity}})
        response_data["message"] = "Free ticket booked and confirmed successfully."
        response_data["booking_status"] = BookingStatus.CONFIRMED.value
        response_data["payment_status"] = PaymentStatus.NOT_REQUIRED.value
        response_data["payment_status_display"] = "SUCCESS"
    elif payment_method in ["CASH", "OFFLINE"]:
        # Cash on Venue / Spot registration: registration is active & confirmed, payment is PENDING cash collection
        booking["booking_status"] = BookingStatus.CONFIRMED.value
        booking["payment_status"] = PaymentStatus.PENDING.value
        booking["payment_status_display"] = "PENDING"
        booking["payment_method"] = "CASH"
        booking["cash_payment"] = True
        booking["cash_status"] = "PENDING"
        booking["confirmed_at"] = now_utc
        res = await booking_collection.insert_one(booking)
        bg_tasks.add_task(
            generate_tickets, str(res.inserted_id), booking_ref, str(e_id), ticket.get("type"), current_user["email"], req.quantity, req.holder_names, "CASH", "PENDING"
        )
        await event_collection.update_one({"_id": e_id}, {"$inc": {"registered_count": req.quantity}})
        response_data["message"] = f"Registration successful! Cash payment of ₹{total_amount:.2f} is pending at the event venue."
        response_data["booking_status"] = BookingStatus.CONFIRMED.value
        response_data["payment_status"] = PaymentStatus.PENDING.value
        response_data["payment_status_display"] = "PENDING"
        response_data["payment_method"] = "CASH"
    else:
        # Online payment via Razorpay
        booking["booking_status"] = BookingStatus.PENDING.value
        booking["payment_status"] = PaymentStatus.PENDING.value
        booking["payment_status_display"] = "PENDING"
        booking["payment_method"] = "ONLINE"
        res = await booking_collection.insert_one(booking)
        
        payment_info = await PaymentService.initiate_payment(booking_ref, total_amount)
        response_data["message"] = "Booking reserved. Proceed to payment."
        response_data["booking_status"] = BookingStatus.PENDING.value
        response_data["payment_status"] = PaymentStatus.PENDING.value
        response_data["payment_status_display"] = "PENDING"
        response_data["payment_method"] = "ONLINE"
        response_data["payment_info"] = payment_info

    # Fetch updated ticket metrics to return real-time remaining count
    updated_ticket = await ticket_collection.find_one({"_id": t_id})
    norm = TicketService.normalize_ticket_data(updated_ticket) if updated_ticket else {}
    response_data.update({
        "booking_id": str(res.inserted_id),
        "ticket_id": str(t_id),
        "total_tickets": norm.get("total_quantity", 0),
        "remaining_tickets": norm.get("available", 0),
        "available": norm.get("available", 0),
        "sold_count": norm.get("sold_count", 0),
        "quantity_booked": req.quantity,
        "expires_at": expires_at.isoformat() if total_amount > 0.0 else None,
    })
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
        
    was_confirmed = (booking.get("booking_status") == BookingStatus.CONFIRMED.value)
    await booking_collection.update_one({"_id": b_id}, {"$set": {"booking_status": BookingStatus.CANCELLED.value}})
    
    # Restore available ticket inventory
    if ObjectId.is_valid(booking.get("ticket_id")):
        await ticket_collection.update_one(
            {"_id": ObjectId(booking["ticket_id"])},
            {"$inc": {"available": booking["quantity"]}}
        )

    # If booking was confirmed, decrement registered_count on event
    if was_confirmed and ObjectId.is_valid(booking.get("event_id")):
        await event_collection.update_one(
            {"_id": ObjectId(booking["event_id"])},
            {"$inc": {"registered_count": -booking["quantity"]}}
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
        if t.get("event_id"):
            try:
                e = await event_collection.find_one({"_id": ObjectId(t["event_id"])})
                if e:
                    t["event_title"] = e.get("title")
                    t["event_date"] = e.get("date")
                    t["event_time"] = e.get("time")
                    t["event_venue"] = e.get("venue")
                    t["event_cover"] = e.get("cover_image")
                    t["base_price"] = e.get("base_price")
            except Exception:
                pass
        if t.get("booking_id"):
            try:
                b = await booking_collection.find_one({"_id": ObjectId(t["booking_id"])})
                if b:
                    t["price"] = b.get("unit_price") or b.get("total_amount")
            except Exception:
                pass
    return tickets

@router.get("/tickets/{ticket_ref}/qr")
async def get_ticket_qr(ticket_ref: str, current_user: dict = Depends(get_current_user)):
    ticket = await issued_ticket_collection.find_one({"ticket_ref": ticket_ref, "holder_email": current_user["email"]})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    b64_qr = TicketService.generate_qr_image_base64(ticket["qr_token"])
    return {"qr_image": b64_qr}
