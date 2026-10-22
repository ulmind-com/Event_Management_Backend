from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File
from fastapi.responses import Response
from typing import List, Optional, Dict, Any

from app.core.admin_auth import get_current_admin
from app.core.database import merchandise_order_collection
from app.schemas.merchandise import (
    ProductCreateReq,
    ProductUpdateReq,
    ProductStatus,
    OrderFulfillmentUpdateReq,
    StockAdjustmentReq
)
from app.services.merchandise_service import MerchandiseService, serialize_mongo
from app.services.cloudinary_srv import upload_image
from app.services.order_pdf_service import OrderPDFService

router = APIRouter()


# ──────────────────────────────────────────────────────────
# Super Admin Product Management
# ──────────────────────────────────────────────────────────

@router.post("/products", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_merchandise_product(
    req: ProductCreateReq,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin creates a new merchandise product. Free products are prohibited."""
    return await MerchandiseService.create_product(req, admin.get("email", "admin"))

@router.get("/products", response_model=Dict[str, Any])
async def list_admin_merchandise_products(
    category: Optional[str] = Query(None),
    event_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    admin: dict = Depends(get_current_admin)
):
    """Super Admin lists all merchandise products (including drafts and archived)."""
    return await MerchandiseService.list_products(
        admin_view=True,
        category=category,
        event_id=event_id,
        status_filter=status_filter,
        search=search,
        skip=skip,
        limit=limit
    )

@router.get("/products/{product_id}", response_model=Dict[str, Any])
async def get_admin_product_detail(
    product_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin retrieves detailed information for an individual product."""
    return await MerchandiseService.get_product(product_id, admin_view=True)

@router.put("/products/{product_id}", response_model=Dict[str, Any])
async def update_merchandise_product(
    product_id: str,
    req: ProductUpdateReq,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin updates an existing product."""
    return await MerchandiseService.update_product(product_id, req, admin.get("email", "admin"))

@router.post("/products/{product_id}/publish", response_model=Dict[str, Any])
async def publish_merchandise_product(
    product_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin sets product status to ACTIVE."""
    return await MerchandiseService.set_product_status(product_id, ProductStatus.ACTIVE, admin.get("email", "admin"))

@router.post("/products/{product_id}/archive", response_model=Dict[str, Any])
async def archive_merchandise_product(
    product_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin archives a product (disables purchase)."""
    return await MerchandiseService.set_product_status(product_id, ProductStatus.ARCHIVED, admin.get("email", "admin"))

@router.post("/upload-image", response_model=Dict[str, Any])
async def upload_merchandise_image(
    file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin)
):
    """Super Admin uploads a merchandise product image to Cloudinary."""
    contents = await file.read()
    if not contents or len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    url = upload_image(contents, folder="eventora/merchandise", filename=file.filename)
    if not url:
        raise HTTPException(status_code=500, detail="Failed to upload image to cloud storage.")
    return {"url": url}

# ──────────────────────────────────────────────────────────
# Super Admin Order & Fulfillment Management
# ──────────────────────────────────────────────────────────

@router.get("/orders", response_model=Dict[str, Any])
async def list_admin_orders(
    status_filter: Optional[str] = Query(None, alias="status"),
    payment_status: Optional[str] = Query(None),
    order_number: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    admin: dict = Depends(get_current_admin)
):
    """Super Admin inspects all merchandise orders with filters."""
    query: Dict[str, Any] = {}
    if status_filter:
        query["status"] = status_filter
    if payment_status:
        query["payment_status"] = payment_status
    if order_number:
        query["order_number"] = order_number

    total = await merchandise_order_collection.count_documents(query)
    orders = await merchandise_order_collection.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)

    return serialize_mongo({
        "total": total,
        "skip": skip,
        "limit": limit,
        "orders": orders
    })

@router.get("/orders/{order_id}", response_model=Dict[str, Any])
async def get_admin_order_detail(
    order_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin retrieves complete order detail with timeline."""
    o_oid = MerchandiseService._to_object_id(order_id)
    query = {"$or": [{"_id": o_oid}, {"_id": order_id}, {"order_number": order_id}]} if o_oid else {"$or": [{"_id": order_id}, {"order_number": order_id}]}
    order = await merchandise_order_collection.find_one(query)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    return serialize_mongo(order)

@router.get("/orders/{order_id}/invoice")
@router.get("/orders/{order_id}/invoice.pdf")
@router.get("/orders/{order_id}/pdf")
@router.get("/orders/{order_id}/receipt")
@router.get("/orders/{order_id}/receipt.pdf")
@router.get("/orders/{order_id}/download")
@router.get("/order/{order_id}/invoice")
@router.get("/order/{order_id}/pdf")
@router.get("/order/{order_id}/receipt")
@router.get("/order/{order_id}/download")
async def download_admin_order_invoice(
    order_id: str,
    download: bool = Query(False, description="Whether to force attachment download"),
    admin: dict = Depends(get_current_admin)
):
    """Super Admin downloads or previews the official Tax Invoice / Receipt PDF for any order."""
    o_oid = MerchandiseService._to_object_id(order_id)
    query = {"$or": [{"_id": o_oid}, {"_id": order_id}, {"order_number": order_id}]} if o_oid else {"$or": [{"_id": order_id}, {"order_number": order_id}]}
    order = await merchandise_order_collection.find_one(query)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")

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

@router.patch("/orders/{order_id}/status", response_model=Dict[str, Any])

async def update_order_fulfillment_status(
    order_id: str,
    req: OrderFulfillmentUpdateReq,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin updates order fulfillment status, courier, and tracking number."""
    return await MerchandiseService.update_fulfillment_status(order_id, req, admin.get("email", "admin"))

# ──────────────────────────────────────────────────────────
# Inventory Management & Analytics
# ──────────────────────────────────────────────────────────

@router.get("/inventory", response_model=Dict[str, Any])
async def get_admin_inventory_overview(
    admin: dict = Depends(get_current_admin)
):
    """Super Admin inspects inventory overview and low stock alerts."""
    analytics = await MerchandiseService.get_analytics()
    return serialize_mongo({
        "total_products": analytics.get("total_products"),
        "active_products": analytics.get("active_products"),
        "low_stock_items": analytics.get("low_stock_items")
    })

@router.post("/inventory/adjustments", response_model=Dict[str, Any])
async def adjust_merchandise_inventory(
    req: StockAdjustmentReq,
    admin: dict = Depends(get_current_admin)
):
    """Super Admin adjusts stock quantity with mandatory audit reason."""
    return await MerchandiseService.adjust_stock(req, admin.get("email", "admin"))

@router.get("/analytics", response_model=Dict[str, Any])
async def get_admin_merchandise_analytics(
    admin: dict = Depends(get_current_admin)
):
    """Super Admin retrieves comprehensive sales and inventory metrics."""
    return await MerchandiseService.get_analytics()
