import resend
from app.core.config import settings

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
        print(f"Error sending email: {e}")
        return None
import logging
logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    def send_judge_invitation(email: str, name: str, setup_token: str):
        setup_url = f"https://eventora.com/judge-setup?token={setup_token}"
        params = {
            "from": f"Eventora <{settings.MAIL_ADDRESS}>",
            "to": [email],
            "subject": "You have been invited to judge at Eventora",
            "html": f"<strong>Hello {name},</strong><br>You have been invited to judge competitions at Eventora.<br>Please set up your password using this secure link: <a href='{setup_url}'>Set up Account</a>",
        }
        try:
            resend.Emails.send(params)
            logger.info(f"Sent Judge Invitation Email to {email}")
            return True
        except Exception as e:
            logger.error(f"Error sending email: {e}")
            return False
