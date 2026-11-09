import os
import urllib.parse
from typing import Dict, Any, Optional
from bson import ObjectId

from app.core.config import settings
from app.core.database import event_collection, competition_collection


class ShareService:
    @staticmethod
    def get_frontend_base_url() -> str:
        """Resolves production frontend base URL, configurable via FRONTEND_URL env var."""
        return os.getenv("FRONTEND_URL", "https://upgradetalentsindia.in").rstrip("/")

    @classmethod
    def build_event_share_data(cls, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Builds direct shareable links, direct ticket booking deep-link,
        pre-formatted share text, and social share links for an event.
        """
        event_id = str(event.get("_id", ""))
        title = event.get("title", "Event")
        venue = event.get("venue", "")
        date = event.get("date", "")
        image_url = event.get("image_url") or event.get("cover_image")
        
        base_url = cls.get_frontend_base_url()
        event_url = f"{base_url}/events/{event_id}"
        direct_booking_url = f"{base_url}/events/{event_id}/book"
        share_url = direct_booking_url
        backend_redirect_url = f"/api/public/share/event/{event_id}"
        
        venue_str = f" at {venue}" if venue else ""
        date_str = f" on {date}" if date else ""
        share_text = f"🎟️ Join '{title}'{venue_str}{date_str}! Book your tickets directly: {direct_booking_url}"
        
        encoded_text = urllib.parse.quote(share_text)
        encoded_direct_url = urllib.parse.quote(direct_booking_url)
        encoded_title = urllib.parse.quote(title)
        
        social_links = {
            "whatsapp": f"https://api.whatsapp.com/send?text={encoded_text}",
            "facebook": f"https://www.facebook.com/sharer/sharer.php?u={encoded_direct_url}",
            "twitter": f"https://twitter.com/intent/tweet?text={encoded_title}&url={encoded_direct_url}",
            "linkedin": f"https://www.linkedin.com/sharing/share-offsite/?url={encoded_direct_url}",
            "telegram": f"https://t.me/share/url?url={encoded_direct_url}&text={encoded_text}"
        }
        
        return {
            "id": event_id,
            "type": "event",
            "item_type": "event",
            "title": title,
            "venue": venue,
            "date": date,
            "time": event.get("time"),
            "image_url": image_url,
            "page_url": event_url,
            "share_url": share_url,
            "direct_booking_url": direct_booking_url,
            "ticket_booking_url": direct_booking_url,
            "direct_action_url": direct_booking_url,
            "share_redirect_url": backend_redirect_url,
            "action_type": "book_tickets",
            "share_text": share_text,
            "social_share_links": social_links
        }

    @classmethod
    def build_competition_share_data(cls, comp: Dict[str, Any]) -> Dict[str, Any]:
        """
        Builds direct shareable links, direct registration deep-link,
        pre-formatted share text, and social share links for a competition.
        """
        comp_id = str(comp.get("_id", ""))
        title = comp.get("title", "Competition")
        category = comp.get("category", "")
        image_url = comp.get("image_url") or comp.get("banner_image")
        
        base_url = cls.get_frontend_base_url()
        comp_url = f"{base_url}/competitions/{comp_id}"
        direct_registration_url = f"{base_url}/competitions/{comp_id}?action=register#register"
        share_url = direct_registration_url
        backend_redirect_url = f"/api/public/share/competition/{comp_id}"
        
        cat_str = f" ({category})" if category else ""
        share_text = f"🏆 Participate in '{title}'{cat_str}! Direct Registration: {direct_registration_url}"
        
        encoded_text = urllib.parse.quote(share_text)
        encoded_direct_url = urllib.parse.quote(direct_registration_url)
        encoded_title = urllib.parse.quote(title)
        
        social_links = {
            "whatsapp": f"https://api.whatsapp.com/send?text={encoded_text}",
            "facebook": f"https://www.facebook.com/sharer/sharer.php?u={encoded_direct_url}",
            "twitter": f"https://twitter.com/intent/tweet?text={encoded_title}&url={encoded_direct_url}",
            "linkedin": f"https://www.linkedin.com/sharing/share-offsite/?url={encoded_direct_url}",
            "telegram": f"https://t.me/share/url?url={encoded_direct_url}&text={encoded_text}"
        }
        
        return {
            "id": comp_id,
            "type": "competition",
            "item_type": "competition",
            "title": title,
            "category": category,
            "categories": [c.get("name") if isinstance(c, dict) else c for c in comp.get("categories", [])],
            "image_url": image_url,
            "page_url": comp_url,
            "share_url": share_url,
            "direct_registration_url": direct_registration_url,
            "registration_url": direct_registration_url,
            "direct_action_url": direct_registration_url,
            "share_redirect_url": backend_redirect_url,
            "action_type": "register",
            "share_text": share_text,
            "social_share_links": social_links
        }

    @classmethod
    def enrich_event(cls, event: Dict[str, Any]) -> Dict[str, Any]:
        """Auto-injects share and direct booking metadata into an event dict."""
        if not event:
            return event
        share_data = cls.build_event_share_data(event)
        event["share_url"] = share_data["share_url"]
        event["direct_booking_url"] = share_data["direct_booking_url"]
        event["ticket_booking_url"] = share_data["direct_booking_url"]
        event["share_text"] = share_data["share_text"]
        event["social_share_links"] = share_data["social_share_links"]
        event["share_links"] = share_data
        return event

    @classmethod
    def enrich_competition(cls, comp: Dict[str, Any]) -> Dict[str, Any]:
        """Auto-injects share and direct registration metadata into a competition dict."""
        if not comp:
            return comp
        share_data = cls.build_competition_share_data(comp)
        comp["share_url"] = share_data["share_url"]
        comp["direct_registration_url"] = share_data["direct_registration_url"]
        comp["registration_url"] = share_data["direct_registration_url"]
        comp["share_text"] = share_data["share_text"]
        comp["social_share_links"] = share_data["social_share_links"]
        comp["share_links"] = share_data
        return comp

