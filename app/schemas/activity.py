from pydantic import BaseModel
from typing import Optional, List, Any

# Generic schemas for returning activities
class EventRes(BaseModel):
    id: str
    title: str
    description: str
    image_url: Optional[str] = None
    date: str
    venue: str
    price: float
    capacity: int
    registered_count: int

class CompetitionRes(BaseModel):
    id: str
    title: str
    description: str
    date: str
    prize: str

class CourseRes(BaseModel):
    id: str
    title: str
    instructor: str
    duration: str
    price: float

class MerchandiseRes(BaseModel):
    id: str
    name: str
    description: str
    price: float
    stock: int
    image_url: Optional[str] = None

class TicketRes(BaseModel):
    id: str
    event_id: str
    type: str # VIP, General, etc.
    price: float
    available: int
