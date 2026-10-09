import qrcode
import base64
import hmac
import hashlib
from io import BytesIO
from app.core.config import settings

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
