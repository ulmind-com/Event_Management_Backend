import qrcode
import base64
import hmac
import hashlib
import logging
from io import BytesIO
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from bson import ObjectId

from app.core.config import settings
from app.core.database import booking_collection, ticket_collection, event_collection

logger = logging.getLogger("eventora.ticket_service")

class TicketService:
    @staticmethod
    def generate_qr_token(ticket_ref: str, booking_ref: str) -> str:
        """
        Generates a cryptographically secure token for the QR code.
        """
        payload = f"{ticket_ref}:{booking_ref}".encode('utf-8')
        signature = hmac.new(
            settings.SECRET_KEY.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        return f"{ticket_ref}:{booking_ref}:{signature}"

    @staticmethod
    def verify_qr_token(qr_token: str) -> dict:
        """
        Verifies the QR token and extracts ticket_ref.
        """
        parts = qr_token.split(":")
        if len(parts) != 3:
            return {"valid": False, "error": "Invalid token format"}
            
        ticket_ref, booking_ref, signature = parts
        
        expected_sig = hmac.new(
            settings.SECRET_KEY.encode('utf-8'),
            f"{ticket_ref}:{booking_ref}".encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        if hmac.compare_digest(expected_sig, signature):
            return {"valid": True, "ticket_ref": ticket_ref, "booking_ref": booking_ref}
        return {"valid": False, "error": "Signature mismatch"}

    @staticmethod
    def generate_qr_image_base64(data: str) -> str:
        """
        Generates a base64 encoded PNG of the QR code.
        """
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{img_str}"

    @staticmethod
    def normalize_ticket_data(doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Standardizes ticket document fields for total tickets, available, remaining,
        sold count, and stock status so client applications always receive accurate metrics.
        """
        if not doc:
            return {}
        
        # Handle _id
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
            
        # Determine total quantity
        total_q = doc.get("total_quantity")
        if total_q is None:
            total_q = doc.get("quantity", 100)
        try:
            total_q = int(total_q)
        except (ValueError, TypeError):
            total_q = 100

        # Determine available (remaining)
        avail = doc.get("available")
        if avail is None:
            avail = total_q
        try:
            avail = max(0, int(avail))
        except (ValueError, TypeError):
            avail = 0

        sold_count = max(0, total_q - avail)
        is_sold_out = (avail <= 0)

        if is_sold_out:
            status = "SOLD_OUT"
        elif avail <= 5:
            status = "LOW_STOCK"
        else:
            status = "AVAILABLE"

        doc["total_quantity"] = total_q
        doc["available"] = avail
        doc["remaining"] = avail
        doc["remaining_tickets"] = avail
        doc["sold_count"] = sold_count
        doc["is_sold_out"] = is_sold_out
        doc["stock_status"] = status
        return doc

    @classmethod
    async def expire_pending_bookings(cls, reservation_timeout_minutes: int = 15) -> Dict[str, Any]:
        """
        Production background worker cycle that expires abandoned pending ticket bookings
        and automatically restores ticket inventory.
        """
        now_utc = datetime.now(timezone.utc)
        threshold_time = now_utc - timedelta(minutes=reservation_timeout_minutes)

        # Match pending bookings where expires_at has passed OR booked_at is older than timeout
        query = {
            "booking_status": "PENDING",
            "$or": [
                {"expires_at": {"$lt": now_utc}},
                {"expires_at": {"$exists": False}, "booked_at": {"$lt": threshold_time}}
            ]
        }

        expired_bookings = await booking_collection.find(query).to_list(length=500)
        released_bookings = 0
        restored_tickets_total = 0

        for b in expired_bookings:
            b_id = b["_id"]
            qty = int(b.get("quantity", 1))
            ticket_id = b.get("ticket_id")

            # Mark booking as EXPIRED
            up_res = await booking_collection.update_one(
                {"_id": b_id, "booking_status": "PENDING"},
                {"$set": {
                    "booking_status": "EXPIRED",
                    "payment_status": "EXPIRED",
                    "expired_at": now_utc
                }}
            )

            if up_res.modified_count > 0:
                released_bookings += 1
                # Restore ticket inventory atomically
                if ticket_id and ObjectId.is_valid(ticket_id):
                    await ticket_collection.update_one(
                        {"_id": ObjectId(ticket_id)},
                        {"$inc": {"available": qty}}
                    )
                    restored_tickets_total += qty
                    logger.info(
                        f"Expired pending booking {b.get('booking_reference', str(b_id))}. "
                        f"Restored {qty} ticket(s) to ticket {ticket_id}."
                    )

        return {
            "expired_bookings_count": released_bookings,
            "restored_tickets_count": restored_tickets_total
        }

