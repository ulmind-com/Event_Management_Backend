from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional, Dict, Any
from bson import ObjectId

from app.api.deps import get_current_user
from app.core.database import (
    vip_configuration_collection,
    vip_booking_collection,
    vip_pass_collection,
)
from app.schemas.vip import (
    VIPBookingCreateReq,
    VIPBookingResponse,
    VIPPassResponse,
    VIPCapacityStats,
)
from app.services.vip_service import VIPService, serialize_mongo

router = APIRouter()

# ──────────────────────────────────────────────────────────
# Available VIP Pass Discovery
# ──────────────────────────────────────────────────────────

@router.get("/events/{event_id}/vip-passes", response_model=List[Dict[str, Any]])
async def get_available_event_vip_passes(event_id: str):
    """
    Public discovery of enabled VIP pass configurations for an event.
    Returns live capacity metrics: total_capacity, booked_count, confirmed_count,
    pending_payment_count, available_count, sold_out, booking_open, and price.
    """
    return [serialize_mongo(x) for x in await VIPService.list_event_configurations(event_id, admin_view=False)]

@router.get("/events/{event_id}/vip-passes/{configuration_id}", response_model=Dict[str, Any])
async def get_event_vip_pass_configuration(event_id: str, configuration_id: str):
    """
    Public detail of a specific VIP pass configuration with live capacity stats.
    """
    config = await VIPService.get_configuration(configuration_id)
    if str(config.get("event_id")) != str(event_id):
        raise HTTPException(status_code=400, detail="Configuration does not match event.")
    if not config.get("is_enabled"):
        raise HTTPException(status_code=404, detail="VIP Pass configuration is not currently available.")
    return serialize_mongo(config)

# ──────────────────────────────────────────────────────────
# User VIP Pass Booking (Paid-Only & Atomic)
# ──────────────────────────────────────────────────────────

@router.post("/events/{event_id}/vip-passes/{configuration_id}/book", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def book_event_vip_pass(
    event_id: str,
    configuration_id: str,
    req: VIPBookingCreateReq,
    current_user: dict = Depends(get_current_user)
):
    """
    Authenticated User books an eligible VIP pass.
    Allocates capacity atomically, creates a pending paid booking, and initiates payment gateway order.
    Every VIP pass is PAID; free booking is strictly prohibited.
    """
    return serialize_mongo(await VIPService.book_vip_pass(event_id, configuration_id, current_user, req))

# ──────────────────────────────────────────────────────────
# User VIP Pass & Booking Library
# ──────────────────────────────────────────────────────────

@router.get("/users/me/vip-bookings", response_model=Dict[str, Any])
async def get_my_vip_bookings(
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """Retrieves all VIP pass bookings owned by the authenticated user."""
    email = current_user.get("email")
    query = {"user_email": email}

    total = await vip_booking_collection.count_documents(query)
    bookings = await vip_booking_collection.find(query).sort("booked_at", -1).skip(skip).limit(limit).to_list(length=limit)
    for b in bookings:
        b["booking_id"] = str(b["_id"])
        b["_id"] = str(b["_id"])

    return serialize_mongo({"total": total, "skip": skip, "limit": limit, "bookings": bookings})

@router.get("/users/me/vip-bookings/{booking_id}", response_model=Dict[str, Any])
async def get_my_vip_booking_detail(
    booking_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Retrieves a specific VIP booking with strict ownership validation."""
    email = current_user.get("email")
    b_oid = VIPService._to_object_id(booking_id)
    query = {"user_email": email, "$or": [{"_id": b_oid}, {"_id": booking_id}]} if b_oid else {"user_email": email, "_id": booking_id}
    
    booking = await vip_booking_collection.find_one(query)
    if not booking:
        raise HTTPException(status_code=404, detail="VIP Booking not found or unauthorized.")

    booking["booking_id"] = str(booking["_id"])
    booking["_id"] = str(booking["_id"])
    return serialize_mongo(booking)

@router.get("/users/me/vip-passes", response_model=Dict[str, Any])
async def get_my_vip_passes(
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """Retrieves all issued VIP passes owned by the authenticated user."""
    email = current_user.get("email")
    query = {"user_email": email}

    total = await vip_pass_collection.count_documents(query)
    passes = await vip_pass_collection.find(query).sort("issued_at", -1).skip(skip).limit(limit).to_list(length=limit)
    for p in passes:
        p["pass_id"] = str(p["_id"])
        p["_id"] = str(p["_id"])

    return serialize_mongo({"total": total, "skip": skip, "limit": limit, "passes": passes})

@router.get("/users/me/vip-passes/{pass_id}", response_model=Dict[str, Any])
async def get_my_vip_pass_detail(
    pass_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Retrieves a specific VIP pass owned by the authenticated user."""
    email = current_user.get("email")
    p_oid = VIPService._to_object_id(pass_id)
    query = {"user_email": email, "$or": [{"_id": p_oid}, {"_id": pass_id}]} if p_oid else {"user_email": email, "_id": pass_id}

    pass_doc = await vip_pass_collection.find_one(query)
    if not pass_doc:
        raise HTTPException(status_code=404, detail="VIP Pass not found or unauthorized.")

    pass_doc["pass_id"] = str(pass_doc["_id"])
    pass_doc["_id"] = str(pass_doc["_id"])
    return serialize_mongo(pass_doc)

@router.get("/users/me/vip-passes/{pass_id}/qr", response_model=Dict[str, Any])
async def get_my_vip_pass_qr(
    pass_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Retrieves the secure entry QR code image and token for the authenticated owner to present at event gate.
    """
    email = current_user.get("email")
    p_oid = VIPService._to_object_id(pass_id)
    query = {"user_email": email, "$or": [{"_id": p_oid}, {"_id": pass_id}]} if p_oid else {"user_email": email, "_id": pass_id}

    pass_doc = await vip_pass_collection.find_one(query)
    if not pass_doc:
        raise HTTPException(status_code=404, detail="VIP Pass not found or unauthorized.")

    return {
        "pass_id": str(pass_doc["_id"]),
        "pass_reference": pass_doc.get("pass_reference"),
        "holder_name": pass_doc.get("holder_name"),
        "event_title": pass_doc.get("event_title"),
        "pass_name": pass_doc.get("pass_name"),
        "status": pass_doc.get("status"),
        "qr_token": pass_doc.get("qr_token"),
        "qr_image_base64": pass_doc.get("qr_image_base64"),
        "access_areas": pass_doc.get("access_areas"),
        "validity_start": pass_doc.get("validity_start"),
        "validity_end": pass_doc.get("validity_end")
    }


class VIPPaymentVerifyReq(BaseModel):
    booking_reference: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/vip/verify-payment", response_model=Dict[str, Any])
async def verify_vip_payment(
    req: VIPPaymentVerifyReq,
    current_user: dict = Depends(get_current_user)
):
    """
    Client-side verification for Razorpay VIP pass checkout.
    Verifies cryptographic signature and confirms pass issuance.
    """
    from app.services.vip_payment_gateway import VIPPaymentGatewayAdapter
    is_valid = VIPPaymentGatewayAdapter.verify_payment_signature(
        razorpay_order_id=req.razorpay_order_id,
        razorpay_payment_id=req.razorpay_payment_id,
        razorpay_signature=req.razorpay_signature
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Razorpay payment signature."
        )

    booking = await vip_booking_collection.find_one({"booking_reference": req.booking_reference})
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VIP Booking reference not found.")

    if booking.get("user_email") != current_user.get("email") and current_user.get("role") != "super_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized VIP booking access.")

    return await VIPService.confirm_payment_and_issue_pass(
        booking_reference=req.booking_reference,
        payment_txn_id=req.razorpay_payment_id,
        amount_paid=float(booking.get("total_amount", 0.0)),
        currency=booking.get("currency", "INR"),
        provider_order_id=req.razorpay_order_id
    )
