from pydantic import BaseModel, HttpUrl
from typing import Optional, List

class ProfileUpdateReq(BaseModel):
    name: Optional[str] = None
    phone_number: Optional[int] = None
    date_of_birth: Optional[str] = None
    address: Optional[str] = None
    parent_phone_number: Optional[int] = None

class ProfileUpdateRes(BaseModel):
    message: str
    user: dict

class DashboardRes(BaseModel):
    profile: dict
    events: List[dict]
    tickets: List[dict]
    competitions: List[dict]
    courses: List[dict]
    merchandise_orders: List[dict]
    certificates: List[dict]
    rewards: dict

    badges: Optional[List[dict]] = None
