import resend
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)
resend.api_key = settings.RESEND_API_KEY

def send_otp_email(to_email: str, otp_code: str):
    params = {
        'from': f'Eventora <{settings.MAIL_ADDRESS}>',
        'to': [to_email],
        'subject': 'Your Eventora Verification OTP',
        'html': f'<strong>Your verification code is: {otp_code}</strong><br>It will expire in 10 minutes.',
    }
    try:
        response = resend.Emails.send(params)
        return response
    except Exception as e:
        logger.error(f'Error sending email: {e}')
        return None

class EmailService:
    @staticmethod
    def send_judge_invitation(email: str, name: str):
        login_url = 'https://eventora.com/login'
        params = {
            'from': f'Eventora <{settings.MAIL_ADDRESS}>',
            'to': [email],
            'subject': 'Welcome to Eventora Judges Panel',
            'html': f'<strong>Hello {name},</strong><br><br>Congratulations! You have been officially appointed as a Judge for Eventora competitions.<br><br>You can now access your private judges panel by logging into our website at <a href="{login_url}">Eventora Login</a>.<br><br><b>How to login:</b><br>1. Enter this email address ({email}).<br>2. You will receive a secure OTP.<br>3. Enter the OTP to access your assigned competitions and start scoring.<br><br>Thank you!',
        }
        try:
            resend.Emails.send(params)
            logger.info(f'Sent Judge Welcome Email to {email}')
            return True
        except Exception as e:
            logger.error(f'Error sending email: {e}')
            return False

    @staticmethod
    def send_volunteer_invitation(email: str):
        login_url = 'https://eventora.com/volunteer/login'
        params = {
            'from': f'Eventora <{settings.MAIL_ADDRESS}>',
            'to': [email],
            'subject': 'You have been added as a Volunteer (Ticket Scanner)',
            'html': f'<strong>Hello,</strong><br><br>You have been assigned as a Volunteer for Eventora!<br><br>Your primary role is to scan and verify attendee tickets at the entrance.<br><br><b>How to login:</b><br>1. Go to <a href="{login_url}">Volunteer Login</a>.<br>2. Enter your email ({email}).<br>3. You will receive a secure OTP to log in.<br>4. Once logged in, your device camera will turn on to scan tickets.<br><br>Thank you!',
        }
        try:
            resend.Emails.send(params)
            logger.info(f'Sent Volunteer Email to {email}')
            return True
        except Exception as e:
            logger.error(f'Error sending email: {e}')
            return False

    @staticmethod
    def send_certificate_email(
        email: str,
        name: str,
        activity_title: str,
        cert_type: str,
        rank: int = None,
        award_title: str = None,
        verification_url: str = None,
        pdf_bytes: bytes = None
    ) -> bool:
        display_type = cert_type.replace('_', ' ').title()
        subject = f'Congratulations! Your Eventora {display_type} Certificate is Ready'
        rank_info = f'<br><b>Rank Achieved:</b> Rank {rank} ({award_title})' if rank else ''
        v_link = f'<br>You can verify and view your certificate online: <a href="{verification_url}">{verification_url}</a>' if verification_url else ''
        
        html_body = (
            f'<strong>Hello {name},</strong><br><br>'
            f'Congratulations! Your official <b>{display_type} Certificate</b> for <b>{activity_title}</b> has been generated and issued.{rank_info}<br>'
            f'{v_link}<br><br>'
            f'Your official digital certificate PDF is attached to this email. You can also view and download it at any time from your Eventora user dashboard.<br><br>'
            f'Best regards,<br>'
            f'<b>Team Eventora</b>'
        )

        params = {
            'from': f'Eventora <{settings.MAIL_ADDRESS}>',
            'to': [email],
            'subject': subject,
            'html': html_body,
        }

        if pdf_bytes:
            try:
                params['attachments'] = [{
                    'filename': f'{cert_type.lower()}_certificate.pdf',
                    'content': list(pdf_bytes)
                }]
            except Exception as att_err:
                logger.warning(f'Could not attach PDF bytes: {att_err}')

        try:
            resend.Emails.send(params)
            logger.info(f'Sent certificate email to {email}')
            return True
        except Exception as e:
            logger.error(f'Error sending certificate email to {email}: {e}')
            return False

    @staticmethod
    def send_vip_pass_confirmation(email: str, name: str, event_title: str, pass_name: str, quantity: int, booking_reference: str) -> bool:
        params = {
            'from': f'Eventora VIP <{settings.MAIL_ADDRESS}>',
            'to': [email],
            'subject': f'VIP Pass Confirmed: {event_title}',
            'html': f'''<strong>Hello {name},</strong><br><br>
Your VIP Pass booking for <b>{event_title}</b> has been successfully confirmed and payment verified!<br><br>
<b>Booking Details:</b><br>
- Reference: <code>{booking_reference}</code><br>
- Pass Type: {pass_name}<br>
- Quantity: {quantity}<br><br>
Your digital VIP Pass with your secure entrance QR Code is now ready in your Eventora account under My VIP Passes.<br>
Please present your QR code at the VIP Gate / Entrance on event day for priority access.<br><br>
Thank you,<br>The Eventora Team'''
        }
        try:
            resend.Emails.send(params)
            logger.info(f"Sent VIP Pass confirmation email to {email}")
            return True
        except Exception as e:
            logger.error(f"Error sending VIP Pass confirmation email: {e}")
            return False
