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

class BookingCreateReq(BaseModel):
    event_id: str
    ticket_id: str
    quantity: int = Field(..., gt=0, le=10)
    holder_names: Optional[List[str]] = None
    payment_method: Optional[str] = "ONLINE"  # "ONLINE" or "CASH"

class BookingResponse(BaseModel):
    id: str
    booking_reference: str
    event_id: str
    ticket_id: str
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
