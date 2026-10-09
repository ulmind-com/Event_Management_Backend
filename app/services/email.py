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
