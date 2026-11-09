from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from typing import Optional, List, Dict, Any
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import booking_collection, ticket_collection, event_collection, issued_ticket_collection
from app.schemas.booking import BookingCreateReq, BookingResponse, BookingStatus, PaymentStatus
from app.services.payment import PaymentService
from app.services.ticket import TicketService
from datetime import datetime, timezone, timedelta
import re
import uuid
import logging

logger = logging.getLogger("eventora.bookings")

router = APIRouter()

async def generate_tickets(booking_id: str, booking_ref: str, event_id: str, ticket_type: str, user_email: str, quantity: int, names: list, payment_method: str = "ONLINE", payment_status: str = "PENDING", category: Optional[str] = None, seats: Optional[list] = None):
    """Background task to generate digital admission pass once confirmed with multi-person quantity and assigned seats."""
    b_doc = None
    if booking_ref:
        b_doc = await booking_collection.find_one({"booking_reference": booking_ref})
    if not b_doc and booking_id and ObjectId.is_valid(booking_id):
        b_doc = await booking_collection.find_one({"_id": ObjectId(booking_id)})

    if b_doc:
        if not seats:
            seats = b_doc.get("seats") or []
        if not names:
            names = b_doc.get("holder_names") or []
        if not category:
            category = b_doc.get("category") or b_doc.get("ticket_type")
        if not quantity or quantity <= 1:
            quantity = int(b_doc.get("quantity") or quantity or 1)
        if not user_email:
            user_email = b_doc.get("user_email")
        if payment_method == "ONLINE" and b_doc.get("payment_method"):
            payment_method = b_doc.get("payment_method")
        if payment_status == "PENDING" and b_doc.get("payment_status"):
            payment_status = b_doc.get("payment_status")

    total_amount = b_doc.get("total_amount") if b_doc else None
    unit_price = b_doc.get("unit_price") if b_doc else None
    if unit_price is None and total_amount is not None and quantity > 0:
        unit_price = round(total_amount / quantity, 2)

    ev_doc = None
    if event_id and ObjectId.is_valid(event_id):
        ev_doc = await event_collection.find_one({"_id": ObjectId(event_id)})

    cover_img = None
    ev_title = None
    ev_date = None
    ev_time = None
    ev_venue = None
    if ev_doc:
        ev_title = ev_doc.get("title")
        ev_date = ev_doc.get("date")
        ev_time = ev_doc.get("time")
        ev_venue = ev_doc.get("venue")
        cover_img = (
            ev_doc.get("card_image_url")
            or ev_doc.get("image_url")
            or ev_doc.get("banner_url")
            or ev_doc.get("banner_image_url")
            or ev_doc.get("cover_image")
        )

    docs = []
    ticket_ref = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    qr_token = TicketService.generate_qr_token(ticket_ref, booking_ref)
    holder_names_list = [n for n in (names or []) if n]
    primary_name = holder_names_list[0] if holder_names_list else "Attendee"
    holder_display = primary_name + (f" (+{quantity - 1} Guests)" if quantity > 1 else "")
    cat_val = category or ticket_type or "General"
    seats_list = [str(s).strip() for s in (seats or []) if s]
    primary_seat = seats_list[0] if seats_list else None
    
    docs.append({
        "ticket_ref": ticket_ref,
        "booking_id": booking_id,
        "booking_ref": booking_ref,
        "event_id": event_id,
        "event_title": ev_title,
        "event_date": ev_date,
        "event_time": ev_time,
        "event_venue": ev_venue,
        "event_cover": cover_img,
        "event_image": cover_img,
        "category": cat_val,
        "ticket_type": ticket_type,
        "holder_email": user_email,
        "holder_name": holder_display,
        "holder_names": holder_names_list if holder_names_list else [primary_name],
        "quantity": quantity,
        "seats": seats_list,
        "seat": primary_seat,
        "admitted_count": 0,
        "is_checked_in": False,
        "check_in_time": None,
        "status": "ACTIVE",
        "qr_token": qr_token,
        "unit_price": unit_price,
        "total_amount": total_amount,
        "total_booking_amount": total_amount,
        "total_price": total_amount,
        "price": total_amount if (total_amount is not None and quantity > 1) else (unit_price or total_amount),
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

        # Send Ultra-Premium Ticket Confirmation Email with Real Scannable QR & Event Details
        try:
            from app.services.email import EmailService
            ev_schedule = f"{ev_date} • {ev_time}" if (ev_date and ev_time) else (ev_date or ev_time or "")
            EmailService.send_ticket_confirmation(
                email=user_email,
                name=primary_name,
                event_title=ev_title or "Official Live Event",
                ticket_reference=ticket_ref,
                booking_reference=booking_ref,
                event_date=ev_schedule,
                venue=ev_venue,
                quantity=quantity,
                category=cat_val,
                seats=seats_list,
                holder_names=holder_names_list,
                total_amount=total_amount,
                unit_price=unit_price,
                payment_method=payment_method,
                payment_status=payment_status,
                event_cover=cover_img,
                qr_token=qr_token,
                event_id=str(event_id) if event_id else None
            )
        except Exception as e:
            logger.error(f"Failed to dispatch ticket confirmation email for {ticket_ref}: {e}", exc_info=True)

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
        e_id = ObjectId(req.event_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid event ID")

    event = await event_collection.find_one({"_id": e_id, "is_published": True})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found or not published")
    if not event.get("registration_enabled", True) or not event.get("ticket_enabled", True):
        raise HTTPException(status_code=400, detail="Ticketing is closed for this event")
    if event.get("event_status") in ["completed", "cancelled"]:
        raise HTTPException(status_code=400, detail=f"This event is {event.get('event_status')}. Ticket booking is closed.")

    check_event_deadline(event)

    raw_items = []
    if req.items:
        raw_items = [it.model_dump() for it in req.items if it.quantity > 0]
    elif req.categories and len(req.categories) > 1:
        raw_items = [{"category": c, "quantity": 1} for c in req.categories]
    else:
        qty = req.quantity if req.quantity is not None else 1
        raw_items = [{
            "ticket_id": req.ticket_id,
            "category": req.category,
            "quantity": qty,
            "holder_names": req.holder_names or []
        }]

    if not raw_items:
        raise HTTPException(status_code=400, detail="Please select at least one ticket category")

    booked_items = []
    total_amount = 0.0
    total_quantity = 0
    reserved_tickets = []

    try:
        for it in raw_items:
            it_qty = it.get("quantity", 1)
            it_ticket_id = it.get("ticket_id")
            it_category = (it.get("category") or "").strip()

            item_ticket = None
            item_tid = None

            if it_ticket_id and ObjectId.is_valid(it_ticket_id):
                item_tid = ObjectId(it_ticket_id)
                item_ticket = await ticket_collection.find_one({"_id": item_tid, "event_id": str(e_id)})

            if not item_ticket and it_category:
                cat_regex = {"$regex": f"^{re.escape(it_category)}$", "$options": "i"}
                item_ticket = await ticket_collection.find_one({
                    "event_id": str(e_id),
                    "$or": [{"category": cat_regex}, {"type": cat_regex}, {"categories": cat_regex}]
                })
                if item_ticket:
                    item_tid = item_ticket["_id"]

            if not item_ticket and it_category:
                match_cat = next((c for c in event.get("categories", []) if c.get("name", "").strip().lower() == it_category.lower()), None)
                if match_cat:
                    c_price = float(match_cat.get("price", 0.0))
                    c_cap = int(match_cat.get("capacity") or event.get("capacity") or 100)
                    new_tier = {
                        "event_id": str(e_id),
                        "type": match_cat["name"],
                        "category": match_cat["name"],
                        "categories": [match_cat["name"]],
                        "price": c_price,
                        "total_quantity": c_cap,
                        "available": c_cap,
                        "sale_start": None,
                        "sale_end": None,
                        "is_visible": True,
                        "display_order": 0,
                        "created_at": datetime.now(timezone.utc)
                    }
                    res_tier = await ticket_collection.insert_one(new_tier)
                    item_ticket = new_tier
                    item_tid = res_tier.inserted_id

            if not item_ticket:
                avail_tickets = await ticket_collection.find({"event_id": str(e_id)}).to_list(length=10)
                if len(avail_tickets) == 1:
                    item_ticket = avail_tickets[0]
                    item_tid = item_ticket["_id"]
                elif not it_ticket_id and not it_category:
                    raise HTTPException(status_code=400, detail="Please select a ticket tier or category")
                else:
                    raise HTTPException(status_code=404, detail=f"Ticket tier not found for category '{it_category}'")

            final_cat = it_category or item_ticket.get("category") or item_ticket.get("type") or "General"
            
            # Atomic inventory decrement
            up_res = await ticket_collection.update_one(
                {"_id": item_tid, "event_id": str(e_id), "available": {"$gte": it_qty}},
                {"$inc": {"available": -it_qty}}
            )
            if up_res.modified_count == 0:
                t_check = await ticket_collection.find_one({"_id": item_tid})
                if not t_check:
                    raise HTTPException(status_code=404, detail="Ticket not found")
                raise HTTPException(status_code=409, detail=f"Insufficient tickets available for category '{final_cat}'")
            
            reserved_tickets.append((item_tid, it_qty))
            unit_p = float(item_ticket.get("price", 0.0))
            subtotal = unit_p * it_qty
            total_amount += subtotal
            total_quantity += it_qty

            booked_items.append({
                "ticket_id": str(item_tid),
                "category": final_cat,
                "ticket_type": item_ticket.get("type") or final_cat,
                "quantity": it_qty,
                "unit_price": unit_p,
                "subtotal": subtotal,
                "holder_names": it.get("holder_names") or []
            })
    except Exception as e:
        for r_tid, r_qty in reserved_tickets:
            await ticket_collection.update_one({"_id": r_tid}, {"$inc": {"available": r_qty}})
        raise e

    distinct_categories = list(dict.fromkeys([b_it["category"] for b_it in booked_items]))
    primary_category = distinct_categories[0] if len(distinct_categories) == 1 else ", ".join(distinct_categories)
    first_tid = booked_items[0]["ticket_id"]
    unit_price = booked_items[0]["unit_price"] if len(booked_items) == 1 else (total_amount / max(1, total_quantity))

    booking_ref = f"BKG-{uuid.uuid4().hex[:10].upper()}"
    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(minutes=15)

    payment_method = (getattr(req, "payment_method", None) or "ONLINE").strip().upper()
    if payment_method not in ["ONLINE", "CASH", "OFFLINE"]:
        payment_method = "ONLINE"

    booking = {
        "booking_reference": booking_ref,
        "event_id": str(e_id),
        "ticket_id": first_tid,
        "category": primary_category,
        "categories": distinct_categories,
        "items": booked_items,
        "ticket_type": booked_items[0]["ticket_type"],
        "user_email": current_user["email"],
        "user_name": current_user.get("name") or current_user.get("full_name") or "Attendee",
        "user_phone": current_user.get("phone_number") or current_user.get("phone") or "",
        "holder_names": req.holder_names or [],
        "seats": getattr(req, "seats", None) or [],
        "quantity": total_quantity,
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
        "category": primary_category,
        "categories": distinct_categories,
        "items": booked_items,
        "unit_price": unit_price,
        "total_amount": total_amount,
        "payment_method": payment_method
    }

    if total_amount == 0.0:
        booking["booking_status"] = BookingStatus.CONFIRMED.value
        booking["payment_status"] = PaymentStatus.NOT_REQUIRED.value
        booking["payment_status_display"] = "SUCCESS"
        booking["confirmed_at"] = now_utc
        res = await booking_collection.insert_one(booking)
        for b_it in booked_items:
            it_names = b_it.get("holder_names") or req.holder_names or []
            bg_tasks.add_task(
                generate_tickets, str(res.inserted_id), booking_ref, str(e_id), b_it["ticket_type"], current_user["email"], b_it["quantity"], it_names, payment_method, "NOT_REQUIRED", category=b_it["category"]
            )
        await event_collection.update_one({"_id": e_id}, {"$inc": {"registered_count": total_quantity}})
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
        for b_it in booked_items:
            it_names = b_it.get("holder_names") or req.holder_names or []
            bg_tasks.add_task(
                generate_tickets, str(res.inserted_id), booking_ref, str(e_id), b_it["ticket_type"], current_user["email"], b_it["quantity"], it_names, "CASH", "PENDING", category=b_it["category"]
            )
        await event_collection.update_one({"_id": e_id}, {"$inc": {"registered_count": total_quantity}})
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

    # Fetch primary updated ticket metrics to return real-time remaining count
    updated_ticket = await ticket_collection.find_one({"_id": ObjectId(first_tid)})
    norm = TicketService.normalize_ticket_data(updated_ticket) if updated_ticket else {}
    response_data.update({
        "booking_id": str(res.inserted_id),
        "ticket_id": first_tid,
        "total_tickets": norm.get("total_quantity", 0),
        "remaining_tickets": norm.get("available", 0),
        "available": norm.get("available", 0),
        "sold_count": norm.get("sold_count", 0),
        "quantity_booked": total_quantity,
        "expires_at": expires_at.isoformat() if total_amount > 0.0 else None,
    })
    return response_data

@router.get("/my")
async def get_my_bookings(current_user: dict = Depends(get_current_user)):
    bookings = await booking_collection.find({"user_email": current_user["email"]}).sort("booked_at", -1).to_list(length=100)
    for b in bookings:
        b["_id"] = str(b["_id"])
        if b.get("event_id") and ObjectId.is_valid(b["event_id"]):
            try:
                ev = await event_collection.find_one({"_id": ObjectId(b["event_id"])})
                if ev:
                    b["event_title"] = ev.get("title")
                    b["event_date"] = ev.get("date")
                    b["event_time"] = ev.get("time")
                    b["event_venue"] = ev.get("venue")
                    b["event_cover"] = ev.get("cover_image")
                    b["event_categories"] = ev.get("categories", [])
                    if not b.get("category"):
                        b["category"] = ev.get("category") or b.get("ticket_type") or "General"
            except Exception:
                pass
        if not b.get("category"):
            b["category"] = b.get("ticket_type") or "General"
        try:
            tix = await issued_ticket_collection.find({
                "$or": [
                    {"booking_id": str(b["_id"])},
                    {"booking_ref": b.get("booking_reference")}
                ]
            }).to_list(length=20)
            b["tickets"] = [{
                "ticket_ref": t.get("ticket_ref"),
                "quantity": int(t.get("quantity", b.get("quantity", 1))),
                "admitted_count": int(t.get("admitted_count", b.get("quantity", 1) if t.get("is_checked_in") else 0)),
                "is_checked_in": bool(t.get("is_checked_in")),
                "status": t.get("status", "USED" if t.get("is_checked_in") else "ACTIVE"),
                "check_in_time": t.get("check_in_time")
            } for t in tix]
        except Exception:
            b["tickets"] = []
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
    tickets = await issued_ticket_collection.find({"holder_email": current_user["email"]}).sort("issued_at", -1).to_list(length=100)
    for t in tickets:
        t["_id"] = str(t["_id"])
        
        # Normalize quantity and admissions
        qty = int(t.get("quantity") or 1)
        admitted = int(t.get("admitted_count", 0))
        if t.get("is_checked_in") and admitted == 0:
            admitted = qty
        is_used = bool(t.get("is_checked_in")) or (admitted >= qty)
        
        t["quantity"] = qty
        t["admitted_count"] = admitted
        t["remaining_count"] = max(0, qty - admitted)
        t["is_checked_in"] = is_used
        t["is_fully_used"] = is_used
        t["status"] = "USED" if is_used else ("PARTIALLY_USED" if admitted > 0 else "ACTIVE")

        if t.get("event_id"):
            try:
                e = await event_collection.find_one({"_id": ObjectId(t["event_id"])})
                if e:
                    t["event_title"] = e.get("title") or t.get("event_title")
                    t["event_date"] = e.get("date") or t.get("event_date")
                    t["event_time"] = e.get("time") or t.get("event_time")
                    t["event_venue"] = e.get("venue") or t.get("event_venue")
                    cover_img = (
                        e.get("card_image_url")
                        or e.get("image_url")
                        or e.get("banner_url")
                        or e.get("banner_image_url")
                        or e.get("cover_image")
                        or t.get("event_cover")
                    )
                    t["event_cover"] = cover_img
                    t["event_image"] = cover_img
                    t["base_price"] = e.get("base_price")
                    t["categories"] = e.get("categories", [])
                    if not t.get("category"):
                        t["category"] = e.get("category") or t.get("ticket_type") or "General"
            except Exception:
                pass
        if not t.get("category"):
            t["category"] = t.get("ticket_type") or "General"
        if t.get("booking_id"):
            try:
                b = await booking_collection.find_one({"_id": ObjectId(t["booking_id"])})
                if b:
                    total_amt = b.get("total_amount")
                    unit_amt = b.get("unit_price")
                    if unit_amt is None and total_amt is not None and qty > 0:
                        unit_amt = round(total_amt / qty, 2)
                    t["unit_price"] = unit_amt
                    t["total_amount"] = total_amt
                    t["total_booking_amount"] = total_amt
                    t["total_price"] = total_amt
                    # When booking has multiple tickets (e.g. 5 tickets for ₹12,500), the pass price displays total amount ₹12,500
                    t["price"] = total_amt if (total_amt is not None and qty > 1) else (unit_amt or total_amt or t.get("price"))
                    t["booking_ref"] = b.get("booking_reference")
                    t["payment_method"] = b.get("payment_method", t.get("payment_method", "ONLINE"))
                    t["payment_status"] = b.get("payment_status", t.get("payment_status", "PENDING"))
                    if not t.get("seats") and b.get("seats"):
                        t["seats"] = b.get("seats")
                    if not t.get("seat") and t.get("seats"):
                        t["seat"] = ", ".join(t["seats"])
                    if (not t.get("holder_names") or t.get("holder_names") == ["Attendee"]) and b.get("holder_names"):
                        t["holder_names"] = b.get("holder_names")
                        first_holder = t["holder_names"][0]
                        t["holder_name"] = first_holder + (f" (+{qty - 1} Guests)" if qty > 1 else "")
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

@router.post("/tickets/{ticket_ref}/send-email")
async def send_ticket_email_manual(ticket_ref: str, current_user: dict = Depends(get_current_user)):
    """Allows attendee to receive or re-send their ultra-pro admission pass & QR code directly to their email inbox."""
    clean_ref = ticket_ref.strip()
    ticket = await issued_ticket_collection.find_one({
        "ticket_ref": clean_ref,
        "$or": [
            {"holder_email": current_user["email"]},
            {"holder_email": current_user["email"].lower()}
        ]
    })
    if not ticket:
        ticket = await issued_ticket_collection.find_one({"ticket_ref": clean_ref})
        if not ticket or (ticket.get("holder_email") != current_user["email"] and current_user.get("role") not in ["ADMIN", "SUPERADMIN"]):
            raise HTTPException(status_code=404, detail="Ticket not found")

    event = None
    if ticket.get("event_id") and ObjectId.is_valid(ticket["event_id"]):
        event = await event_collection.find_one({"_id": ObjectId(ticket["event_id"])})

    booking = None
    if ticket.get("booking_id") and ObjectId.is_valid(ticket["booking_id"]):
        booking = await booking_collection.find_one({"_id": ObjectId(ticket["booking_id"])})
    elif ticket.get("booking_ref"):
        booking = await booking_collection.find_one({"booking_reference": ticket["booking_ref"]})

    ev_title = (event.get("title") if event else None) or ticket.get("event_title") or "Official Live Event"
    ev_date = (event.get("date") if event else None) or ticket.get("event_date")
    ev_time = (event.get("time") if event else None) or ticket.get("event_time")
    ev_venue = (event.get("venue") if event else None) or ticket.get("event_venue") or "Official Event Arena"
    ev_cover = (
        (event.get("card_image_url") or event.get("image_url") or event.get("banner_url") or event.get("banner_image_url") or event.get("cover_image"))
        if event else ticket.get("event_cover")
    )
    ev_schedule = f"{ev_date} • {ev_time}" if (ev_date and ev_time) else (ev_date or ev_time or "")

    qty = int(ticket.get("quantity") or (booking.get("quantity") if booking else 1) or 1)
    seats = ticket.get("seats") or (booking.get("seats") if booking else [])
    holder_names = ticket.get("holder_names") or (booking.get("holder_names") if booking else [])
    primary_name = holder_names[0] if holder_names else (ticket.get("holder_name") or current_user.get("name") or "Attendee")
    category = ticket.get("category") or ticket.get("ticket_type") or (booking.get("category") if booking else "General Access")
    
    total_amount = (booking.get("total_amount") if booking else None) or ticket.get("total_amount") or ticket.get("total_booking_amount")
    unit_price = (booking.get("unit_price") if booking else None) or ticket.get("unit_price")
    if unit_price is None and total_amount is not None and qty > 0:
        unit_price = round(total_amount / qty, 2)
    payment_method = ticket.get("payment_method") or (booking.get("payment_method") if booking else "ONLINE")
    payment_status = ticket.get("payment_status") or (booking.get("payment_status") if booking else "PAID")

    from app.services.email import EmailService
    sent = EmailService.send_ticket_confirmation(
        email=current_user["email"],
        name=primary_name,
        event_title=ev_title,
        ticket_reference=ticket["ticket_ref"],
        booking_reference=ticket.get("booking_ref") or (booking.get("booking_reference") if booking else None),
        event_date=ev_schedule,
        venue=ev_venue,
        quantity=qty,
        category=category,
        seats=seats,
        holder_names=holder_names,
        total_amount=total_amount,
        unit_price=unit_price,
        payment_method=payment_method,
        payment_status=payment_status,
        event_cover=ev_cover,
        qr_token=ticket.get("qr_token"),
        event_id=str(event["_id"]) if event else ticket.get("event_id")
    )

    return {
        "success": True,
        "message": f"Admission pass and QR code have been dispatched to {current_user['email']}",
        "email": current_user["email"],
        "ticket_ref": ticket["ticket_ref"]
    }
