from pydantic import BaseModel
from app.services.wallet_service import WalletService
from fastapi import APIRouter, Depends, HTTPException
from typing import List
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import registration_collection, event_collection, competition_collection, course_collection
from app.schemas.action import RegistrationReq
from datetime import datetime, timezone

router = APIRouter()

@router.post("/")
async def register_activity(req: RegistrationReq, current_user: dict = Depends(get_current_user)):
    """Register for an event, competition, or course"""
    email = current_user["email"]
    
    # Check if already registered or pending payment
    existing = await registration_collection.find_one({
        "email": email, 
        "activity_id": req.activity_id,
        "activity_type": req.activity_type
    })
    
    if existing:
        if existing.get("status") == "CONFIRMED":
            raise HTTPException(status_code=400, detail="Already registered for this activity")
        elif existing.get("status") == "PENDING_PAYMENT":
            req_method = (getattr(req, "payment_method", None) or "ONLINE").strip().upper()
            if req_method in ["CASH", "OFFLINE"]:
                await registration_collection.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {
                        "status": "CONFIRMED",
                        "payment_status": "PENDING",
                        "payment_status_display": "PENDING",
                        "payment_method": "CASH",
                        "cash_payment": True,
                        "cash_status": "PENDING"
                    }}
                )
                try:
                    obj_id = ObjectId(req.activity_id)
                    if req.activity_type == "event":
                        await event_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})
                    elif req.activity_type == "competition":
                        await competition_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})
                    elif req.activity_type in ("course", "workshop"):
                        await course_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})
                except Exception:
                    pass
                return {
                    "message": "Registration updated to Cash on Venue. Cash payment pending.",
                    "registration_id": str(existing["_id"]),
                    "status": "CONFIRMED",
                    "payment_status": "PENDING",
                    "payment_status_display": "PENDING",
                    "payment_method": "CASH",
                    "payable_amount": float(existing.get("payable_amount", 0.0)),
                    "order_reference": existing.get("order_reference")
                }

            order_ref = existing.get("order_reference")
            payable_amount = float(existing.get("payable_amount", 0.0))
            if payable_amount == 0.0:
                await registration_collection.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"status": "CONFIRMED", "payment_status": "NOT_REQUIRED"}}
                )
                try:
                    obj_id = ObjectId(req.activity_id)
                    if req.activity_type == "event":
                        await event_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})
                    elif req.activity_type == "competition":
                        await competition_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})
                    elif req.activity_type in ("course", "workshop"):
                        await course_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})
                except Exception:
                    pass
                return {
                    "message": "Registration confirmed.",
                    "registration_id": str(existing["_id"]),
                    "status": "CONFIRMED",
                    "payable_amount": 0.0,
                    "order_reference": order_ref
                }

            # Re-initiate payment gateway order for pending registration
            from app.services.payment import PaymentService
            payment_info = await PaymentService.initiate_payment(
                booking_reference=order_ref,
                amount=payable_amount,
                currency="INR",
                notes={
                    "type": f"{req.activity_type}_registration",
                    "activity_id": req.activity_id,
                    "user_email": email,
                    "order_reference": order_ref
                }
            )
            return {
                "message": "Registration pending payment. Please proceed to payment.",
                "registration_id": str(existing["_id"]),
                "status": "PENDING_PAYMENT",
                "payable_amount": payable_amount,
                "order_reference": order_ref,
                "payment_info": payment_info
            }

    # Validate activity exists and eligibility
    try:
        obj_id = ObjectId(req.activity_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid activity ID")

    if req.activity_type == "event":
        activity = await event_collection.find_one({"_id": obj_id})
    elif req.activity_type == "competition":
        activity = await competition_collection.find_one({"_id": obj_id})
    elif req.activity_type in ["course", "workshop"]:
        activity = await course_collection.find_one({"_id": obj_id, "course_type": req.activity_type})
    else:
        raise HTTPException(status_code=400, detail="Invalid activity type")

    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
        
    now = datetime.now(timezone.utc)
        
    # STRICT REGISTRATION DEADLINE ENFORCEMENT FOR COURSES AND WORKSHOPS
    if req.activity_type in ["course", "workshop"]:
        if not activity.get("is_published"):
            raise HTTPException(status_code=400, detail="Activity is not published or archived")
            
        start_at = activity.get("registration_start_at")
        end_at = activity.get("registration_end_at")

        # Normalise to timezone-aware datetimes before comparing. The windows are
        # optional — a course with no start/end simply has no such gate. (The old
        # one-liner crashed with AttributeError when start_at/end_at were None,
        # because the `… if start_at.tzinfo is None …` ternary dereferenced None.)
        def _aware(dt):
            if not dt:
                return None
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

        start_aware = _aware(start_at)
        end_aware = _aware(end_at)

        if start_aware and now < start_aware:
            raise HTTPException(status_code=400, detail="Registration has not opened yet (UPCOMING)")

        if end_aware and now >= end_aware:
            raise HTTPException(status_code=400, detail="Registration closed")
            
        capacity = activity.get("available_seats", 0)
        registered_count = await registration_collection.count_documents({
            "activity_id": req.activity_id,
            "activity_type": req.activity_type,
            "status": "CONFIRMED"
        })
        if registered_count >= capacity:
            raise HTTPException(status_code=400, detail="Activity is full")


    # Check capacity/deadlines (simplified logic for now)
    if "deadline" in activity:
        deadline = datetime.fromisoformat(activity["deadline"])
        if datetime.now(timezone.utc) > deadline:
            raise HTTPException(status_code=400, detail="Registration deadline has passed")
            
    if "capacity" in activity and "registered_count" in activity:
        if activity["registered_count"] >= activity["capacity"]:
            raise HTTPException(status_code=400, detail="Activity is full")

    # Determine Payment Status & Apply Course/Workshop Coin Discounts
    price = float(activity.get("price", 0.0))
    discount_amount = 0.0
    coins_applied = 0
    order_ref = req.order_reference or f"ORD_{req.activity_type}_{int(now.timestamp())}_{ObjectId()}"

    if req.activity_type in ["course", "workshop"] and req.coins_to_use and req.coins_to_use > 0:
        quote = await WalletService.calculate_discount_quote(
            user_email=email,
            item_id=req.activity_id,
            item_type=req.activity_type,
            coins_to_use=req.coins_to_use
        )
        if not quote.get("is_eligible"):
            raise HTTPException(status_code=400, detail=quote.get("ineligible_reason", "Coin discount not eligible"))
        
        coins_applied = quote.get("coins_applied", 0)
        discount_amount = quote.get("discount_amount", 0.0)
        
        if coins_applied > 0:
            res_result = await WalletService.reserve_coins_for_order(
                user_email=email,
                item_id=req.activity_id,
                item_type=req.activity_type,
                coins_to_reserve=coins_applied,
                order_reference=order_ref
            )
            if not res_result.get("reserved"):
                raise HTTPException(status_code=400, detail=res_result.get("reason", "Failed to reserve coins"))

    payable_amount = max(0.0, price - discount_amount)
    payment_method = (getattr(req, "payment_method", None) or "ONLINE").strip().upper()
    if payment_method not in ["ONLINE", "CASH", "OFFLINE"]:
        payment_method = "ONLINE"
    
    if payable_amount == 0.0:
        # Fully covered by coins or free: confirm and commit reserved coins
        final_status = "CONFIRMED"
        payment_status = "NOT_REQUIRED"
        payment_status_display = "SUCCESS"
        if coins_applied > 0:
            await WalletService.commit_reserved_coins(order_ref)
    elif payment_method in ["CASH", "OFFLINE"]:
        # Cash on Venue / Spot participation: Registration completed, cash payment pending at event
        final_status = "CONFIRMED"
        payment_status = "PENDING"
        payment_status_display = "PENDING"
        payment_method = "CASH"
        if coins_applied > 0:
            await WalletService.commit_reserved_coins(order_ref)
    else:
        final_status = "PENDING_PAYMENT"
        payment_status = "PENDING"
        payment_status_display = "PENDING"
        payment_method = "ONLINE"

    # Register
    new_reg = {
        "email": email,
        "participant_name": current_user.get("full_name") or current_user.get("name") or email.split('@')[0],
        "participant_email": email,
        "activity_id": req.activity_id,
        "activity_type": req.activity_type,
        "price_snapshot": price,
        "original_price": price,
        "coins_applied": coins_applied,
        "discount_amount": discount_amount,
        "payable_amount": payable_amount,
        "order_reference": order_ref,
        "created_at": now,
        "status": final_status,
        "payment_status": payment_status,
        "payment_status_display": payment_status_display,
        "payment_method": payment_method,
        "cash_payment": (payment_method == "CASH"),
        "cash_status": "PENDING" if payment_method == "CASH" else None
    }
    
    await registration_collection.insert_one(new_reg)
    
    # Authoritative trigger: REGISTRATION_CONFIRMATION (if free, coin-covered, or cash-registered)
    if final_status == "CONFIRMED":
        try:
            from app.services.notification_service import NotificationService
            from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
            from app.schemas.notification import NotificationCreate

            act_title = activity.get("title") or activity.get("name") or req.activity_type.capitalize()
            await NotificationService.create_notification(NotificationCreate(
                recipient_user_id=email,
                notification_type=NotificationType.REGISTRATION_CONFIRMATION,
                title=f"Registration Confirmed: {act_title}",
                message=f"Your registration for {req.activity_type} '{act_title}' has been successfully confirmed." + (" Cash payment is pending at venue." if payment_method == "CASH" else ""),
                resource_type=req.activity_type,
                resource_id=req.activity_id,
                action_url=f"/{req.activity_type}s/{req.activity_id}",
                priority=NotificationPriority.HIGH,
                sound_category=SoundCategory.SUCCESS,
                deduplication_key=f"reg_conf_{new_reg['_id']}",
                source_module="registrations",
                source_event_id=order_ref,
            ))
        except Exception:
            pass

    # Update count if free, coin-covered, or confirmed via cash registration
    if final_status == "CONFIRMED":
        if req.activity_type == "event":
            await event_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})
        elif req.activity_type == "competition":
            await competition_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})
        elif req.activity_type == "course":
            await course_collection.update_one({"_id": obj_id}, {"$inc": {"registered_count": 1}})

    payment_info = None
    if payable_amount > 0.0 and payment_method == "ONLINE":
        from app.services.payment import PaymentService
        payment_info = await PaymentService.initiate_payment(
            booking_reference=order_ref,
            amount=payable_amount,
            currency="INR",
            notes={
                "type": "activity_registration",
                "activity_type": req.activity_type,
                "activity_id": req.activity_id,
                "order_reference": order_ref,
                "user_email": email
            }
        )

    if payment_method == "CASH":
        msg = f"Registration successful! Cash payment of ₹{payable_amount:.2f} is pending at the event venue."
    elif final_status == "CONFIRMED":
        msg = "Registration successful"
    else:
        msg = "Registration pending payment. Please proceed to payment."

    response_data = {
        "message": msg,
        "registration_id": str(new_reg["_id"]),
        "order_reference": order_ref,
        "status": final_status,
        "payment_status": payment_status,
        "payment_status_display": payment_status_display,
        "payment_method": payment_method,
        "payable_amount": payable_amount
    }
    if payment_info:
        response_data["payment_info"] = payment_info
    return response_data

@router.delete("/{registration_id}")
async def cancel_registration(registration_id: str, current_user: dict = Depends(get_current_user)):
    """Cancel a registration"""
    try:
        obj_id = ObjectId(registration_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid registration ID")

    reg = await registration_collection.find_one({"_id": obj_id, "email": current_user["email"]})
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")
        
    # Delete
    if reg.get("status") == "PENDING_PAYMENT" and reg.get("order_reference"):
        try:
            await WalletService.release_reserved_coins(reg["order_reference"])
        except Exception:
            pass

    await registration_collection.delete_one({"_id": obj_id})
    
    # Decrement count
    act_obj_id = ObjectId(reg["activity_id"])
    if reg["activity_type"] == "event":
        await event_collection.update_one({"_id": act_obj_id}, {"$inc": {"registered_count": -1}})
    elif reg["activity_type"] == "competition":
        await competition_collection.update_one({"_id": act_obj_id}, {"$inc": {"registered_count": -1}})
    elif reg["activity_type"] == "course":
        await course_collection.update_one({"_id": act_obj_id}, {"$inc": {"registered_count": -1}})

    return {"message": "Registration cancelled successfully"}


class ActivityPaymentVerifyReq(BaseModel):
    order_reference: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/verify-payment")
async def verify_activity_registration_payment(
    req: ActivityPaymentVerifyReq,
    current_user: dict = Depends(get_current_user)
):
    """
    Client-side verification for paid activity/competition/course registration.
    Verifies cryptographic HMAC signature and marks registration as CONFIRMED.
    """
    from app.services.payment import PaymentService
    is_valid = PaymentService.verify_payment_signature(
        razorpay_order_id=req.razorpay_order_id,
        razorpay_payment_id=req.razorpay_payment_id,
        razorpay_signature=req.razorpay_signature
    )
    if not is_valid:
        raise HTTPException(
            status_code=400,
            detail="Invalid Razorpay payment signature. Verification failed."
        )

    reg = await registration_collection.find_one({"order_reference": req.order_reference})
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")

    if reg.get("email") != current_user.get("email") and current_user.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Unauthorized")

    if reg.get("status") == "CONFIRMED":
        return {
            "status": "CONFIRMED",
            "message": "Registration already confirmed.",
            "order_reference": req.order_reference
        }

    # Commit any reserved coins
    try:
        await WalletService.commit_reserved_coins(req.order_reference)
    except Exception:
        pass

    # Update registration to CONFIRMED
    await registration_collection.update_one(
        {"_id": reg["_id"]},
        {"$set": {
            "status": "CONFIRMED",
            "payment_status": "PAID",
            "payment_reference": req.razorpay_payment_id,
            "provider_order_id": req.razorpay_order_id,
            "payment_provider": "RAZORPAY",
            "paid_at": datetime.now(timezone.utc)
        }}
    )

    # Increment registered_count on activity upon successful payment verification
    try:
        act_id = reg.get("activity_id")
        act_type = reg.get("activity_type")
        if act_id and ObjectId.is_valid(str(act_id)):
            act_obj_id = ObjectId(str(act_id))
            if act_type == "event":
                await event_collection.update_one({"_id": act_obj_id}, {"$inc": {"registered_count": 1}})
            elif act_type == "competition":
                await competition_collection.update_one({"_id": act_obj_id}, {"$inc": {"registered_count": 1}})
            elif act_type in ("course", "workshop"):
                await course_collection.update_one({"_id": act_obj_id}, {"$inc": {"registered_count": 1}})
    except Exception:
        pass

    # Authoritative triggers: PAYMENT_CONFIRMATION and REGISTRATION_CONFIRMATION
    try:
        from app.services.notification_service import NotificationService
        from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
        from app.schemas.notification import NotificationCreate

        user_email = reg.get("email")
        act_type = reg.get("activity_type", "activity")
        act_id = reg.get("activity_id", "")

        # 1. Payment confirmation
        await NotificationService.create_notification(NotificationCreate(
            recipient_user_id=user_email,
            notification_type=NotificationType.PAYMENT_CONFIRMATION,
            title="Payment Successful",
            message=f"Your payment of ₹{reg.get('payable_amount', 0)} for order {req.order_reference} was successful.",
            resource_type="payment",
            resource_id=req.razorpay_payment_id,
            action_url=f"/orders/{req.order_reference}",
            priority=NotificationPriority.HIGH,
            sound_category=SoundCategory.SUCCESS,
            deduplication_key=f"pay_conf_{req.razorpay_payment_id}",
            source_module="registrations",
            source_event_id=req.order_reference,
        ))

        # 2. Registration confirmation
        await NotificationService.create_notification(NotificationCreate(
            recipient_user_id=user_email,
            notification_type=NotificationType.REGISTRATION_CONFIRMATION,
            title="Registration Confirmed",
            message=f"Your registration for {act_type} has been officially confirmed.",
            resource_type=act_type,
            resource_id=act_id,
            action_url=f"/{act_type}s/{act_id}",
            priority=NotificationPriority.HIGH,
            sound_category=SoundCategory.SUCCESS,
            deduplication_key=f"reg_conf_{reg['_id']}",
            source_module="registrations",
            source_event_id=req.order_reference,
        ))
    except Exception:
        pass

    return {
        "status": "CONFIRMED",
        "message": "Payment verified and registration confirmed successfully.",
        "order_reference": req.order_reference,
        "payment_id": req.razorpay_payment_id
    }
