"""
Google Wallet — "Add to Google Wallet" for issued tickets.

Builds a signed "save to Google Wallet" link for one ticket. The JWT carries
the event ticket class (one per event) and the ticket object inline, so no
extra Wallet REST calls are needed: Google creates them when the user saves.

Env:
  GOOGLE_WALLET_ISSUER_ID              Issuer ID from the Google Pay & Wallet Console (required)
  GOOGLE_WALLET_SERVICE_ACCOUNT_JSON   Service account JSON that has access to that issuer.
                                       Falls back to FIREBASE_SERVICE_ACCOUNT_JSON when unset
                                       (that account must then be added as a user in the Wallet Console).
  GOOGLE_WALLET_LOGO_URL               Optional public https URL of a square logo (shown in a circle).
  GOOGLE_WALLET_ISSUER_NAME            Optional, defaults to "Upgrade Talents India".
"""
import json
import os
import re
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import jwt

logger = logging.getLogger("eventora.google_wallet")

SAVE_URL = "https://pay.google.com/gp/v/save/"
IST_OFFSET = "+05:30"


class WalletNotConfigured(Exception):
    pass


def _load_service_account() -> Optional[Dict[str, Any]]:
    raw = os.getenv("GOOGLE_WALLET_SERVICE_ACCOUNT_JSON") or os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None
    cleaned = raw.strip()
    if (cleaned.startswith("'") and cleaned.endswith("'")) or (cleaned.startswith('"') and cleaned.endswith('"')):
        cleaned = cleaned[1:-1].strip()
    try:
        sa = json.loads(cleaned)
    except Exception as e:
        logger.warning(f"Google Wallet: service account JSON is not valid JSON: {e}")
        return None
    if not sa.get("client_email") or not sa.get("private_key"):
        return None
    return sa


def is_configured() -> bool:
    return bool(os.getenv("GOOGLE_WALLET_ISSUER_ID")) and _load_service_account() is not None


def _safe_id(value: str) -> str:
    """Wallet ids allow only letters, digits, '.', '_' and '-'."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(value))[:120]


def _localized(text: str) -> Dict[str, Any]:
    return {"defaultValue": {"language": "en-IN", "value": str(text)[:500]}}


def _parse_start(date_str: Optional[str], time_str: Optional[str]) -> Optional[str]:
    """Best-effort ISO 8601 start (IST) from the event's free-form date/time strings."""
    if not date_str:
        return None
    d = None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            d = datetime.strptime(str(date_str).strip()[:20], fmt)
            break
        except ValueError:
            continue
    if d is None:
        try:
            d = datetime.fromisoformat(str(date_str).strip().replace("Z", "+00:00"))
            if d.tzinfo is not None:
                return d.isoformat()
        except ValueError:
            return None
    hh, mm = 0, 0
    if time_str:
        t = str(time_str).strip().upper()
        for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M:%S"):
            try:
                parsed = datetime.strptime(t, fmt)
                hh, mm = parsed.hour, parsed.minute
                break
            except ValueError:
                continue
    return d.replace(hour=hh, minute=mm, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S") + IST_OFFSET


def build_save_url(*, ticket: Dict[str, Any], event: Optional[Dict[str, Any]], holder_name: str) -> str:
    issuer_id = os.getenv("GOOGLE_WALLET_ISSUER_ID", "").strip()
    sa = _load_service_account()
    if not issuer_id or not sa:
        raise WalletNotConfigured("Google Wallet isn't enabled on the server yet.")

    issuer_name = os.getenv("GOOGLE_WALLET_ISSUER_NAME", "Upgrade Talents India")
    logo_url = os.getenv("GOOGLE_WALLET_LOGO_URL", "").strip()

    ticket_ref = ticket["ticket_ref"]
    event_id = str(ticket.get("event_id") or (event or {}).get("_id") or "event")
    title = (event or {}).get("title") or ticket.get("event_title") or "Event"
    venue = (event or {}).get("venue") or ticket.get("event_venue")
    date_str = (event or {}).get("date") or ticket.get("event_date")
    time_str = (event or {}).get("time") or ticket.get("event_time")
    cover = None
    if event:
        cover = event.get("card_image_url") or event.get("image_url") or event.get("banner_url") or event.get("banner_image_url") or event.get("cover_image")
    category = ticket.get("category") or ticket.get("ticket_type") or "General"

    class_id = f"{issuer_id}.{_safe_id('event_' + event_id)}"
    object_id = f"{issuer_id}.{_safe_id('ticket_' + ticket_ref)}"

    ticket_class: Dict[str, Any] = {
        "id": class_id,
        "issuerName": issuer_name,
        "reviewStatus": "UNDER_REVIEW",
        "eventName": _localized(title),
        "hexBackgroundColor": "#0b0b10",
    }
    if venue:
        ticket_class["venue"] = {"name": _localized(venue), "address": _localized(venue)}
    start = _parse_start(date_str, time_str)
    if start:
        ticket_class["dateTime"] = {"start": start}
    if logo_url.startswith("https://"):
        ticket_class["logo"] = {"sourceUri": {"uri": logo_url}, "contentDescription": _localized(issuer_name)}
    if isinstance(cover, str) and cover.startswith("https://"):
        ticket_class["heroImage"] = {"sourceUri": {"uri": cover}, "contentDescription": _localized(title)}

    ticket_object: Dict[str, Any] = {
        "id": object_id,
        "classId": class_id,
        "state": "ACTIVE",
        "ticketHolderName": str(holder_name)[:100],
        "ticketNumber": ticket_ref,
        "ticketType": _localized(category),
        # same value the app and the PDF put in the QR, so the gate scanner reads it unchanged
        "barcode": {"type": "QR_CODE", "value": ticket_ref, "alternateText": ticket_ref},
        "hexBackgroundColor": "#0b0b10",
    }
    seats = ticket.get("seats") or []
    if seats:
        ticket_object["seatInfo"] = {"seat": _localized(", ".join(str(s) for s in seats)[:100])}

    claims = {
        "iss": sa["client_email"],
        "aud": "google",
        "typ": "savetowallet",
        "iat": int(time.time()),
        "origins": [],
        "payload": {
            "eventTicketClasses": [ticket_class],
            "eventTicketObjects": [ticket_object],
        },
    }
    token = jwt.encode(claims, sa["private_key"], algorithm="RS256", headers={"kid": sa.get("private_key_id")} if sa.get("private_key_id") else None)
    return SAVE_URL + token
