from pydantic import BaseModel
from typing import Optional

class RegistrationReq(BaseModel):
    activity_id: str
    # 'event', 'competition', 'course', 'workshop'
    activity_type: str
    coins_to_use: Optional[int] = None
    order_reference: Optional[str] = None

class BookingReq(BaseModel):
    ticket_id: str
    quantity: int

class OrderReq(BaseModel):
    merchandise_id: str
    quantity: int
    shipping_address: str
