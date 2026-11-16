from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum

class BookingStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"

class PaymentStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"

class BookingCategoryItem(BaseModel):
    ticket_id: Optional[str] = None
    category: Optional[str] = None
    quantity: int = Field(1, gt=0, le=10)
    holder_names: Optional[List[str]] = None

class BookingCreateReq(BaseModel):
    event_id: str
    ticket_id: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[int] = Field(None, gt=0, le=10)
    items: Optional[List[BookingCategoryItem]] = None
    categories: Optional[List[str]] = None
    holder_names: Optional[List[str]] = None
    seats: Optional[List[str]] = None
    payment_method: Optional[str] = "ONLINE"  # "ONLINE" or "CASH"

class BookingResponse(BaseModel):
    id: str
    booking_reference: str
    event_id: str
    ticket_id: Optional[str] = None
    category: Optional[str] = None
    categories: Optional[List[str]] = None
    user_email: str
    quantity: int
    unit_price: float
    total_amount: float
    booking_status: BookingStatus
    payment_status: PaymentStatus
    booked_at: datetime
    
class PaymentVerifyReq(BaseModel):
    transaction_id: str
    status: str # "success" or "failed"

class TicketScanReq(BaseModel):
    qr_token: str
    admit_count: Optional[int] = None
    event_id: Optional[str] = None
    collect_cash: Optional[bool] = Field(None, description="Set True to record cash payment collection upon entry")
    cash_collected: Optional[bool] = Field(None, description="Alias for collect_cash")
    cash_amount: Optional[float] = Field(None, description="Optional custom cash amount collected")
    notes: Optional[str] = Field(None, description="Optional volunteer check-in notes")


class CashCollectReq(BaseModel):
    ticket_ref: Optional[str] = None
    qr_token: Optional[str] = None
    amount: Optional[float] = None
    notes: Optional[str] = None
