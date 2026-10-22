from pydantic import BaseModel, Field, EmailStr, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from enum import Enum

class MerchandiseCategory(str, Enum):
    TSHIRTS = "T-shirts"
    HOODIES = "Hoodies"
    CAPS = "Caps"
    MUGS = "Mugs"
    BAGS = "Bags"
    BADGES = "Badges"
    POSTERS = "Posters"
    ACCESSORIES = "Accessories"
    CUSTOM = "Custom"

class ProductStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    ARCHIVED = "ARCHIVED"

class OrderStatus(str, Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    PROCESSING = "PROCESSING"
    PACKED = "PACKED"
    SHIPPED = "SHIPPED"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    REFUND_PENDING = "REFUND_PENDING"
    REFUNDED = "REFUNDED"

class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    REFUNDED = "REFUNDED"

class InventoryMovementType(str, Enum):
    INITIAL_STOCK = "INITIAL_STOCK"
    STOCK_ADJUSTMENT = "STOCK_ADJUSTMENT"
    STOCK_RESERVED = "STOCK_RESERVED"
    RESERVATION_RELEASED = "RESERVATION_RELEASED"
    SALE_COMMITTED = "SALE_COMMITTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    RETURN_RESTOCK = "RETURN_RESTOCK"

# ──────────────────────────────────────────────────────────
# Product & Variant Models
# ──────────────────────────────────────────────────────────

class ProductVariant(BaseModel):
    variant_id: str
    sku: str
    size: Optional[str] = None
    color: Optional[str] = None
    attributes: Optional[Dict[str, str]] = None
    price_override: Optional[float] = Field(default=None, gt=0.0)
    stock_quantity: int = Field(default=0, ge=0)
    reserved_quantity: int = Field(default=0, ge=0)
    is_active: bool = True

class ProductCreateReq(BaseModel):
    name: str = Field(..., min_length=2, max_length=150)
    slug: Optional[str] = None
    description: Optional[str] = ""
    category: str = Field(default="T-shirts")
    price: float = Field(..., gt=0.0, description="Product price must be strictly positive (> 0). Free merchandise is strictly prohibited.")
    currency: str = Field(default="INR", min_length=3, max_length=5)
    stock_quantity: int = Field(default=0, ge=0)
    variants: List[ProductVariant] = Field(default_factory=list)
    images: List[str] = Field(default_factory=list)
    event_id: Optional[str] = None
    sku: Optional[str] = None
    low_stock_threshold: int = Field(default=5, ge=0)
    requires_shipping: bool = True
    shipping_fee: float = Field(default=0.0, ge=0.0)
    status: ProductStatus = ProductStatus.ACTIVE

    @field_validator("images", mode="before")
    @classmethod
    def validate_merchandise_images(cls, v):
        if v and isinstance(v, list):
            for img in v:
                if isinstance(img, str) and ("res.cloudinary.com/demo" in img.lower() or "cloudinary.com/demo" in img.lower()):
                    raise ValueError("Fake demo Cloudinary URLs are strictly prohibited.")
        return v

class ProductUpdateReq(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = Field(default=None, gt=0.0)
    currency: Optional[str] = None
    stock_quantity: Optional[int] = Field(default=None, ge=0)
    variants: Optional[List[ProductVariant]] = None
    images: Optional[List[str]] = None
    event_id: Optional[str] = None
    sku: Optional[str] = None
    low_stock_threshold: Optional[int] = Field(default=None, ge=0)
    requires_shipping: Optional[bool] = None
    shipping_fee: Optional[float] = Field(default=None, ge=0.0)
    status: Optional[ProductStatus] = None

    @field_validator("images", mode="before")
    @classmethod
    def validate_merchandise_images(cls, v):
        if v and isinstance(v, list):
            for img in v:
                if isinstance(img, str) and ("res.cloudinary.com/demo" in img.lower() or "cloudinary.com/demo" in img.lower()):
                    raise ValueError("Fake demo Cloudinary URLs are strictly prohibited.")
        return v

# ──────────────────────────────────────────────────────────
# Order Models
# ──────────────────────────────────────────────────────────

class OrderItemReq(BaseModel):
    product_id: str
    variant_id: Optional[str] = None
    quantity: int = Field(default=1, ge=1, le=20)

class ShippingAddress(BaseModel):
    full_name: str = Field(..., min_length=2)
    phone: str = Field(..., min_length=7, max_length=20)
    address_line1: str = Field(..., min_length=5)
    address_line2: Optional[str] = None
    city: str = Field(..., min_length=2)
    state: str = Field(..., min_length=2)
    postal_code: str = Field(..., min_length=3)
    country: str = "India"

class OrderCreateReq(BaseModel):
    items: List[OrderItemReq] = Field(..., min_length=1)
    shipping_address: ShippingAddress
    idempotency_key: Optional[str] = None

class OrderFulfillmentUpdateReq(BaseModel):
    status: OrderStatus
    tracking_number: Optional[str] = None
    courier_name: Optional[str] = None
    notes: Optional[str] = None

class StockAdjustmentReq(BaseModel):
    product_id: str
    variant_id: Optional[str] = None
    quantity_delta: int = Field(..., description="Positive to add stock, negative to reduce stock")
    reason: str = Field(..., min_length=3, max_length=300)

class OrderCancelReq(BaseModel):
    reason: str = Field(..., min_length=3, max_length=300)
