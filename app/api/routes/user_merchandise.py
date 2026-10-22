from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from typing import List, Optional, Dict, Any

from app.api.deps import get_current_user
from app.core.database import merchandise_order_collection
from app.schemas.merchandise import (
    OrderCreateReq,
    MerchandiseCategory
)
from app.services.merchandise_service import MerchandiseService, serialize_mongo
from app.services.order_pdf_service import OrderPDFService


router = APIRouter()

# ──────────────────────────────────────────────────────────
# Public Product Discovery
# ──────────────────────────────────────────────────────────

@router.get("/categories", response_model=List[str])
async def get_merchandise_categories():
    """Returns all available merchandise product categories."""
    return [c.value for c in MerchandiseCategory]

@router.get("/products", response_model=Dict[str, Any])
async def list_available_merchandise_products(
    category: Optional[str] = Query(None),
    event_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100)
):
    """
    Public product discovery.
    Only returns ACTIVE products with authoritative pricing and variant availability.
    """
    return await MerchandiseService.list_products(
        admin_view=False,
        category=category,
        event_id=event_id,
        search=search,
        skip=skip,
        limit=limit
    )

@router.get("/products/{product_id}", response_model=Dict[str, Any])
async def get_merchandise_product_detail(product_id: str):
    """
    Public product details including available sizes, colors, variants, and price.
    """
    return await MerchandiseService.get_product(product_id, admin_view=False)

# ──────────────────────────────────────────────────────────
# Authenticated User Orders & Checkout
# ──────────────────────────────────────────────────────────

@router.post("/orders", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_merchandise_order(
    req: OrderCreateReq,
    current_user: dict = Depends(get_current_user)
):
    """
    Authenticated user places a merchandise order.
    Calculates authoritative server-side price, allocates stock atomically, and creates payment order.
    Every merchandise purchase requires a positive payment amount (> 0). Free checkout is prohibited.
    """
    return await MerchandiseService.create_order(current_user, req)

@router.get("/orders", response_model=Dict[str, Any])
async def get_my_merchandise_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """Retrieves all merchandise orders placed by the authenticated user."""
    email = current_user.get("email")
    query = {"user_email": email}

    total = await merchandise_order_collection.count_documents(query)
    orders = await merchandise_order_collection.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)

    return serialize_mongo({
        "total": total,
        "skip": skip,
        "limit": limit,
        "orders": orders
    })

@router.get("/orders/{order_id}", response_model=Dict[str, Any])
async def get_my_merchandise_order_detail(
    order_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Retrieves detailed order information with strict ownership check."""
    email = current_user.get("email")
    o_oid = MerchandiseService._to_object_id(order_id)
    query = {"user_email": email, "$or": [{"_id": o_oid}, {"_id": order_id}, {"order_number": order_id}]} if o_oid else {"user_email": email, "$or": [{"_id": order_id}, {"order_number": order_id}]}

    order = await merchandise_order_collection.find_one(query)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found or unauthorized.")
    return serialize_mongo(order)

@router.get("/orders/{order_id}/tracking", response_model=Dict[str, Any])
async def get_my_merchandise_order_tracking(
    order_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Retrieves real-time fulfillment and tracking details for an order."""
    email = current_user.get("email")
    o_oid = MerchandiseService._to_object_id(order_id)
    query = {"user_email": email, "$or": [{"_id": o_oid}, {"_id": order_id}, {"order_number": order_id}]} if o_oid else {"user_email": email, "$or": [{"_id": order_id}, {"order_number": order_id}]}

    order = await merchandise_order_collection.find_one(query)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found or unauthorized.")

    return serialize_mongo({
        "order_number": order.get("order_number"),
        "status": order.get("status"),
        "courier_name": order.get("courier_name"),
        "tracking_number": order.get("tracking_number"),
        "timeline": order.get("status_history", [])
    })


@router.get("/orders/{order_id}/invoice")
@router.get("/orders/{order_id}/pdf")
@router.get("/orders/{order_id}/receipt")
@router.get("/orders/{order_id}/download")
async def download_my_merchandise_order_invoice(
    order_id: str,
    token: Optional[str] = Query(None, description="Optional JWT token for direct browser link downloads"),
    download: bool = Query(False, description="Whether to force attachment download"),
    current_user: dict = Depends(get_current_user)
):
    """
    Downloads or previews the authoritative Tax Invoice / Order Receipt PDF.
    Generates a high-quality PDF containing complete customer details, item breakdown,
    payment reference, fulfillment status, and live tracking QR code.
    """
    email = current_user.get("email")
    o_oid = MerchandiseService._to_object_id(order_id)
    if current_user.get("role") == "super_admin":
        query = {"$or": [{"_id": o_oid}, {"_id": order_id}, {"order_number": order_id}]} if o_oid else {"$or": [{"_id": order_id}, {"order_number": order_id}]}
    else:
        query = {"user_email": email, "$or": [{"_id": o_oid}, {"_id": order_id}, {"order_number": order_id}]} if o_oid else {"user_email": email, "$or": [{"_id": order_id}, {"order_number": order_id}]}

    order = await merchandise_order_collection.find_one(query)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found or unauthorized.")

    pdf_bytes = OrderPDFService.generate_order_invoice_pdf(order)
    order_number = order.get("order_number", "order")
    disposition = "attachment" if download else "inline"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="Order_Invoice_{order_number}.pdf"',
            "Content-Type": "application/pdf"
        }
    )



class MerchandisePaymentVerifyReq(BaseModel):
    order_number: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


@router.post("/orders/verify-payment", response_model=Dict[str, Any])
async def verify_merchandise_payment_client(
    req: MerchandisePaymentVerifyReq,
    current_user: dict = Depends(get_current_user)
):
    """
    Client-side verification for Razorpay Merchandise order checkout.
    Verifies cryptographic signature and confirms order completion.
    """
    from app.services.merchandise_payment_gateway import MerchandisePaymentGatewayAdapter
    is_valid = MerchandisePaymentGatewayAdapter.verify_payment_signature(
        razorpay_order_id=req.razorpay_order_id,
        razorpay_payment_id=req.razorpay_payment_id,
        razorpay_signature=req.razorpay_signature
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Razorpay payment signature."
        )

    order = await merchandise_order_collection.find_one({"order_number": req.order_number})
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchandise order not found.")

    if order.get("user_email") != current_user.get("email") and current_user.get("role") != "super_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unauthorized order access.")

    return await MerchandiseService.confirm_payment_and_complete_order(
        order_number=req.order_number,
        payment_txn_id=req.razorpay_payment_id,
        amount_paid=float(order.get("total_amount", 0.0)),
        currency=order.get("currency", "INR"),
        provider_order_id=req.razorpay_order_id
    )
