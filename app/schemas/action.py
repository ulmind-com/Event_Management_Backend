from pydantic import BaseModel
from typing import Optional

class RegistrationReq(BaseModel):
    activity_id: str
    # 'event', 'competition', 'course'
    activity_type: str

class BookingReq(BaseModel):
    ticket_id: str
    quantity: int

class OrderReq(BaseModel):
    merchandise_id: str
    quantity: int
    shipping_address: str
