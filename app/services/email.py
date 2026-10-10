import resend
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)
resend.api_key = settings.RESEND_API_KEY

def send_otp_email(to_email: str, otp_code: str):
    params = {
        "from": f"Eventora <{settings.MAIL_ADDRESS}>",
        "to": [to_email],
        "subject": "Your Eventora Verification OTP",
        "html": f"<strong>Your verification code is: {otp_code}</strong><br>It will expire in 10 minutes.",
    }
    try:
        response = resend.Emails.send(params)
        return response
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return None

class EmailService:
    @staticmethod
    def send_judge_invitation(email: str, name: str):
        login_url = f"https://eventora.com/login"
        params = {
            "from": f"Eventora <{settings.MAIL_ADDRESS}>",
            "to": [email],
            "subject": "Welcome to Eventora Judges Panel",
            "html": f"<strong>Hello {name},</strong><br><br>Congratulations! You have been officially appointed as a Judge for Eventora competitions.<br><br>You can now access your private judges panel by logging into our website at <a href='{login_url}'>Eventora Login</a>.<br><br><b>How to login:</b><br>1. Enter this email address ({email}).<br>2. You will receive a secure OTP.<br>3. Enter the OTP to access your assigned competitions and start scoring.<br><br>Thank you!",
        }
        try:
            resend.Emails.send(params)
            logger.info(f"Sent Judge Welcome Email to {email}")
            return True
        except Exception as e:
            logger.error(f"Error sending email: {e}")
            return False
