from pydantic import BaseModel
from typing import Optional

class RegistrationReq(BaseModel):
    activity_id: str
    # 'event', 'competition', 'course', 'workshop'
    activity_type: str
    coins_to_use: Optional[int] = None
    order_reference: Optional[str] = None
    payment_method: Optional[str] = "ONLINE"  # "ONLINE" or "CASH"

class BookingReq(BaseModel):
    ticket_id: str
    quantity: int
    payment_method: Optional[str] = "ONLINE"  # "ONLINE" or "CASH"

class OrderReq(BaseModel):
    merchandise_id: str
    quantity: int
    shipping_address: str
