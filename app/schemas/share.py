from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class SocialShareLinks(BaseModel):
    whatsapp: str
    facebook: str
    twitter: str
    linkedin: str
    telegram: str

class ShareLinkResponse(BaseModel):
    id: str
    type: str  # "event" or "competition"
    item_type: Optional[str] = None
    title: str
    venue: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    category: Optional[str] = None
    categories: Optional[List[str]] = None
    image_url: Optional[str] = None
    page_url: Optional[str] = None
    share_url: str
    direct_action_url: str
    ticket_booking_url: Optional[str] = None
    direct_booking_url: Optional[str] = None
    registration_url: Optional[str] = None
    direct_registration_url: Optional[str] = None
    share_redirect_url: str
    action_type: str  # "book_tickets" or "register"
    share_text: str
    social_share_links: SocialShareLinks

