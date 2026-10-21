import json
import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Header, Request, status
from pydantic import BaseModel, Field
from bson import ObjectId

from app.api.deps import get_current_user
from app.core.database import booking_collection, event_collection, ticket_collection
from app.schemas.booking import PaymentVerifyReq, BookingStatus, PaymentStatus
from app.api.routes.bookings import generate_tickets
from app.services.payment import PaymentService

router = APIRouter()
logger = logging.getLogger("eventora.payments_route")


class RazorpayVerifyReq(BaseModel):
    booking_id: str = Field(..., description="Booking MongoDB ID or booking_reference")
    razorpay_order_id: str = Field(..., description="Razorpay Order ID")
    razorpay_payment_id: str = Field(..., description="Razorpay Payment ID")
    razorpay_signature: str = Field(..., description="Razorpay HMAC signature from checkout")


class RazorpayCreateOrderReq(BaseModel):
    booking_id: str = Field(..., description="Booking MongoDB ID or booking_reference")


@router.post("/verify-razorpay", status_code=status.HTTP_200_OK)
async def verify_razorpay_payment(
    req: RazorpayVerifyReq,
    bg_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Authoritative Razorpay payment signature verification endpoint.
    Called by client frontend after Razorpay modal completes successfully.
    Confirms booking, stores payment reference, and initiates ticket generation.
    """
    # 1. Verify Razorpay cryptographic signature
    is_valid = PaymentService.verify_payment_signature(
        razorpay_order_id=req.razorpay_order_id,
        razorpay_payment_id=req.razorpay_payment_id,
        razorpay_signature=req.razorpay_signature
    )
    if not is_valid:
        logger.warning(f"Signature verification failed for payment {req.razorpay_payment_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Razorpay payment signature. Payment verification failed."
        )

    # 2. Find booking
    booking = None
    try:
        booking = await booking_collection.find_one({"_id": ObjectId(req.booking_id)})
    except Exception:
        pass

    if not booking:
        booking = await booking_collection.find_one({
            "$or": [
                {"booking_reference": req.booking_id},
                {"_id": req.booking_id}
            ]
        })

    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    # 3. Ownership check
    user_email = current_user.get("email")
    if booking.get("user_email") != user_email and current_user.get("role") != "super_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized booking payment verification")

    # 4. Check if already confirmed (idempotency)
    if booking.get("payment_status") == PaymentStatus.PAID.value:
        return {
            "status": "ALREADY_CONFIRMED",
            "message": "Payment already confirmed for this booking.",
            "booking_reference": booking.get("booking_reference")
        }

    # 5. Confirm booking and record transaction
    b_id = booking["_id"]
    await booking_collection.update_one(
        {"_id": b_id},
        {"$set": {
            "payment_status": PaymentStatus.PAID.value,
            "booking_status": BookingStatus.CONFIRMED.value,
            "payment_reference": req.razorpay_payment_id,
            "provider_order_id": req.razorpay_order_id,
            "payment_provider": "RAZORPAY"
        }}
    )

    # Increment registered_count on event
    if ObjectId.is_valid(booking.get("event_id")):
        await event_collection.update_one(
            {"_id": ObjectId(booking["event_id"])},
            {"$inc": {"registered_count": booking.get("quantity", 1)}}
        )

    # 6. Trigger Ticket Generation background task
    bg_tasks.add_task(
        generate_tickets,
        str(b_id),
        booking["booking_reference"],
        booking["event_id"],
        "PaidTicket",
        booking["user_email"],
        booking["quantity"],
        []
    )

    # Authoritative trigger: PAYMENT_CONFIRMATION
    try:
        from app.services.notification_service import NotificationService
        from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
        from app.schemas.notification import NotificationCreate

        await NotificationService.create_notification(NotificationCreate(
            recipient_user_id=booking["user_email"],
            notification_type=NotificationType.PAYMENT_CONFIRMATION,
            title="Payment Received",
            message=f"Your payment of ₹{booking.get('total_amount', 0)} for booking {booking['booking_reference']} has been verified.",
            resource_type="booking",
            resource_id=str(b_id),
            action_url=f"/bookings/{b_id}",
            priority=NotificationPriority.HIGH,
            sound_category=SoundCategory.SUCCESS,
            deduplication_key=f"pay_conf_{req.razorpay_payment_id}",
            source_module="payments",
            source_event_id=booking["booking_reference"],
        ))
    except Exception as e:
        logger.warning(f"Failed to create payment confirmation notification: {e}")

    logger.info(f"Booking {booking['booking_reference']} confirmed via Razorpay payment {req.razorpay_payment_id}")


    return {
        "status": "SUCCESS",
        "message": "Razorpay payment verified and booking confirmed successfully.",
        "booking_reference": booking["booking_reference"],
        "payment_id": req.razorpay_payment_id
    }


@router.post("/create-order/{booking_id}", status_code=status.HTTP_200_OK)
async def create_razorpay_order_for_booking(
    booking_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Creates or re-initiates a Razorpay order for an existing pending booking.
    """
    booking = None
    try:
        booking = await booking_collection.find_one({"_id": ObjectId(booking_id)})
    except Exception:
        pass

    if not booking:
        booking = await booking_collection.find_one({
            "$or": [
                {"booking_reference": booking_id},
                {"_id": booking_id}
            ]
        })

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    user_email = current_user.get("email")
    if booking.get("user_email") != user_email and current_user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Unauthorized")

    if booking.get("payment_status") == PaymentStatus.PAID.value:
        raise HTTPException(status_code=400, detail="Booking is already paid.")

    payment_info = await PaymentService.initiate_payment(
        booking_reference=booking["booking_reference"],
        amount=booking.get("total_amount", 0.0),
        currency="INR",
        notes={"user_email": user_email, "booking_id": str(booking["_id"])}
    )

    return payment_info


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def handle_razorpay_booking_webhook(
    request: Request,
    bg_tasks: BackgroundTasks,
    x_razorpay_signature: Optional[str] = Header(None)
):
    """
    Authoritative server-to-server webhook endpoint for Razorpay payment notifications.
    """
    raw_body = await request.body()
    if not x_razorpay_signature:
        raise HTTPException(status_code=400, detail="Missing X-Razorpay-Signature header")

    is_valid = PaymentService.verify_webhook_signature(raw_body, x_razorpay_signature)
    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid Razorpay webhook signature")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = payload.get("event")
    logger.info(f"Received Razorpay webhook event: {event}")

    if event in ["payment.captured", "order.paid"]:
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        notes = payment_entity.get("notes", {})
        booking_ref = notes.get("booking_reference") or notes.get("order_reference") or payment_entity.get("description")
        payment_id = payment_entity.get("id")
        order_id = payment_entity.get("order_id")

        if booking_ref:
            # 1. Check Competition Registration
            if booking_ref.startswith("CRG-") or notes.get("type") == "competition_registration":
                from app.core.database import competition_registration_collection, competition_collection
                from app.schemas.competition import RegistrationStatus
                reg = await competition_registration_collection.find_one({"registration_reference": booking_ref})
                if reg and reg.get("payment_status") != PaymentStatus.PAID.value:
                    await competition_registration_collection.update_one(
                        {"_id": reg["_id"]},
                        {"$set": {
                            "status": RegistrationStatus.CONFIRMED.value,
                            "payment_status": PaymentStatus.PAID.value,
                            "payment_reference": payment_id,
                            "provider_order_id": order_id,
                            "payment_provider": "RAZORPAY"
                        }}
                    )
                    try:
                        await competition_collection.update_one(
                            {"_id": ObjectId(reg["competition_id"])},
                            {"$inc": {"registered_count": 1}}
                        )
                    except Exception:
                        pass
                    logger.info(f"Webhook confirmed paid competition registration {booking_ref}")

            # 2. Check General Activity / Course / Workshop Registration
            elif booking_ref.startswith("ORD_") or notes.get("type") == "activity_registration":
                from app.core.database import registration_collection
                from app.services.wallet_service import WalletService
                reg = await registration_collection.find_one({"order_reference": booking_ref})
                if reg and reg.get("status") != "CONFIRMED":
                    await registration_collection.update_one(
                        {"_id": reg["_id"]},
                        {"$set": {
                            "status": "CONFIRMED",
                            "payment_status": "PAID",
                            "payment_reference": payment_id,
                            "provider_order_id": order_id,
                            "payment_provider": "RAZORPAY"
                        }}
                    )
                    await WalletService.commit_reserved_coins(booking_ref)
                    logger.info(f"Webhook confirmed activity registration {booking_ref}")

            # 3. Check Event Ticket Bookings
            else:
                booking = await booking_collection.find_one({"booking_reference": booking_ref})
                if booking and booking.get("payment_status") != PaymentStatus.PAID.value:
                    b_id = booking["_id"]
                    await booking_collection.update_one(
                        {"_id": b_id},
                        {"$set": {
                            "payment_status": PaymentStatus.PAID.value,
                            "booking_status": BookingStatus.CONFIRMED.value,
                            "payment_reference": payment_id,
                            "provider_order_id": order_id,
                            "payment_provider": "RAZORPAY"
                        }}
                    )
                    if ObjectId.is_valid(booking.get("event_id")):
                        await event_collection.update_one(
                            {"_id": ObjectId(booking["event_id"])},
                            {"$inc": {"registered_count": booking.get("quantity", 1)}}
                        )
                    bg_tasks.add_task(
                        generate_tickets,
                        str(b_id),
                        booking["booking_reference"],
                        booking["event_id"],
                        "PaidTicket",
                        booking["user_email"],
                        booking["quantity"],
                        []
                    )
                    # Authoritative trigger: PAYMENT_CONFIRMATION via webhook
                    try:
                        from app.services.notification_service import NotificationService
                        from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
                        from app.schemas.notification import NotificationCreate

                        await NotificationService.create_notification(NotificationCreate(
                            recipient_user_id=booking["user_email"],
                            notification_type=NotificationType.PAYMENT_CONFIRMATION,
                            title="Payment Received",
                            message=f"Payment for booking {booking['booking_reference']} was captured successfully.",
                            resource_type="booking",
                            resource_id=str(b_id),
                            action_url=f"/bookings/{b_id}",
                            priority=NotificationPriority.HIGH,
                            sound_category=SoundCategory.SUCCESS,
                            deduplication_key=f"pay_conf_{payment_id}",
                            source_module="payments_webhook",
                            source_event_id=booking_ref,
                        ))
                    except Exception:
                        pass
                    logger.info(f"Webhook confirmed ticket booking {booking_ref}")

    return {"status": "PROCESSED"}


@router.post("/verify-mock/{booking_id}")
async def verify_payment_mock(
    booking_id: str,
    req: PaymentVerifyReq,
    bg_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Mock endpoint to simulate a payment gateway webhook or client-side verify.
    Maintained for test environment compatibility.
    """
    try:
        b_id = ObjectId(booking_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid booking ID")

    booking = await booking_collection.find_one({"_id": b_id})
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.get("payment_status") != PaymentStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Booking is not pending payment")

    if req.status == "success":
        await booking_collection.update_one(
            {"_id": b_id},
            {"$set": {
                "payment_status": PaymentStatus.PAID.value,
                "booking_status": BookingStatus.CONFIRMED.value,
                "payment_reference": req.transaction_id
            }}
        )
        if ObjectId.is_valid(booking.get("event_id")):
            await event_collection.update_one(
                {"_id": ObjectId(booking["event_id"])},
                {"$inc": {"registered_count": booking.get("quantity", 1)}}
            )
        bg_tasks.add_task(
            generate_tickets,
            str(b_id),
            booking["booking_reference"],
            booking["event_id"],
            "PaidTicket",
            booking["user_email"],
            booking["quantity"],
            []
        )
        return {"message": "Payment verified and booking confirmed"}
    else:
        await booking_collection.update_one(
            {"_id": b_id},
            {"$set": {
                "payment_status": PaymentStatus.FAILED.value,
                "booking_status": BookingStatus.CANCELLED.value
            }}
        )
        # Restore ticket inventory on failed payment
        if ObjectId.is_valid(booking.get("ticket_id")):
            await ticket_collection.update_one(
                {"_id": ObjectId(booking["ticket_id"])},
                {"$inc": {"available": booking.get("quantity", 1)}}
            )
        return {"message": "Payment failed, booking cancelled and inventory restored"}
