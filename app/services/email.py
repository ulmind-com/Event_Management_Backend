import resend
from app.core.config import settings
import logging
import html as html_lib
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

if settings.RESEND_API_KEY:
    resend.api_key = settings.RESEND_API_KEY


def _escape(text: Any) -> str:
    """Escapes user provided text for safe HTML embedding."""
    if text is None:
        return ""
    return html_lib.escape(str(text))


def _get_sender(sender_title: str = "Eventora") -> str:
    """Returns formatted RFC 5322 From address."""
    mail_addr = settings.MAIL_ADDRESS or "onboarding@resend.dev"
    return f"{sender_title} <{mail_addr}>"


# ═══════════════════════════════════════════════════════════════════
# RESPONSIVE, LUXURY EMAIL LAYOUT GENERATOR
# ═══════════════════════════════════════════════════════════════════

def _render_base_email(
    title: str,
    preheader: str,
    badge_text: str,
    badge_color: str,
    badge_bg: str,
    badge_border: str,
    content_html: str,
    recipient_email: str
) -> str:
    """
    Renders an ultra-premium, dark-themed responsive email container
    compatible with Gmail, Apple Mail, Outlook, and mobile clients.
    """
    safe_title = _escape(title)
    safe_preheader = _escape(preheader)
    safe_email = _escape(recipient_email)

    return f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="color-scheme" content="dark only">
  <meta name="supported-color-schemes" content="dark only">
  <title>{safe_title}</title>
  <!--[if mso]>
  <style type="text/css">
    body, table, td, p, a {{ font-family: -apple-system, BlinkMacSystemFont, Arial, sans-serif !important; }}
  </style>
  <![endif]-->
</head>
<body style="margin: 0; padding: 0; background-color: #070A12; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; color: #E2E8F0; width: 100% !important;">
  
  <!-- Preheader text (appears in inbox preview line) -->
  <div style="display: none; font-size: 1px; color: #070A12; line-height: 1px; max-height: 0px; max-width: 0px; opacity: 0; overflow: hidden; mso-hide: all;">
    {safe_preheader} &zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;
  </div>

  <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #070A12; padding: 36px 12px;">
    <tr>
      <td align="center" valign="top">
        
        <!-- Main Card Container (560px) -->
        <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 560px; background-color: #0F172A; border-radius: 20px; border: 1px solid #1E293B; overflow: hidden; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.85);">
          
          <!-- Top Brand Gradient Line -->
          <tr>
            <td height="4" style="background: linear-gradient(90deg, #6366F1 0%, #A855F7 50%, #EC4899 100%); line-height: 4px; font-size: 4px;">&nbsp;</td>
          </tr>
          
          <!-- Brand Header -->
          <tr>
            <td style="padding: 32px 36px 20px 36px;">
              <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td align="left" valign="middle">
                    <div style="font-size: 24px; font-weight: 900; letter-spacing: -0.5px; color: #FFFFFF; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                      EVENT<span style="color: #818CF8;">ORA</span>
                    </div>
                    <div style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 2.2px; color: #64748B; margin-top: 3px;">
                      Premier Experience Platform
                    </div>
                  </td>
                  <td align="right" valign="middle">
                    <span style="display: inline-block; padding: 5px 12px; background-color: {badge_bg}; border: 1px solid {badge_border}; border-radius: 9999px; font-size: 10px; font-weight: 700; letter-spacing: 1.2px; text-transform: uppercase; color: {badge_color};">
                      {badge_text}
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Subtle Divider -->
          <tr>
            <td style="padding: 0 36px;">
              <div style="border-top: 1px solid #1E293B; height: 1px; line-height: 1px; font-size: 1px;">&nbsp;</div>
            </td>
          </tr>

          <!-- Main Content Body -->
          <tr>
            <td style="padding: 28px 36px 36px 36px;">
              {content_html}
            </td>
          </tr>

          <!-- Footer Area -->
          <tr>
            <td style="background-color: #0B0F19; border-top: 1px solid #1E293B; padding: 26px 36px; text-align: center;">
              <p style="margin: 0 0 10px 0; font-size: 12px; color: #64748B; line-height: 1.6;">
                This message was securely transmitted to <span style="color: #94A3B8; font-family: Consolas, monospace;">{safe_email}</span>.<br>
                If you did not initiate this request, you can safely ignore this email.
              </p>
              <p style="margin: 0 0 14px 0; font-size: 12px; color: #64748B;">
                Need assistance? Reach us at <a href="mailto:support@eventora.com" style="color: #818CF8; text-decoration: none; font-weight: 600;">support@eventora.com</a>
              </p>
              <div style="border-top: 1px solid #1E293B; margin: 16px auto; width: 60%; height: 1px; line-height: 1px; font-size: 1px;">&nbsp;</div>
              <p style="margin: 0; font-size: 11px; color: #475569; text-transform: uppercase; letter-spacing: 1.2px;">
                &copy; 2026 Eventora Inc. &bull; Live Competitions, Passes & Certificates
              </p>
            </td>
          </tr>

        </table>
        <!-- /Main Card Container -->

      </td>
    </tr>
  </table>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════
# CORE SEND WRAPPER
# ═══════════════════════════════════════════════════════════════════

def _send_email_payload(
    to_email: str,
    subject: str,
    html: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
    sender_name: str = "Eventora"
) -> bool:
    """Internal helper to dispatch emails via Resend SDK safely."""
    if not settings.RESEND_API_KEY:
        logger.warning(f"RESEND_API_KEY is not configured. Email to {to_email} with subject '{subject}' logged but skipped.")
        return False

    params = {
        "from": _get_sender(sender_name),
        "to": [to_email],
        "subject": subject,
        "html": html,
    }

    if attachments:
        params["attachments"] = attachments

    try:
        resend.api_key = settings.RESEND_API_KEY
        response = resend.Emails.send(params)
        logger.info(f"Email successfully dispatched to {to_email} (Subject: {subject}) | Response: {response}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email} (Subject: {subject}): {e}", exc_info=True)
        return False


# ═══════════════════════════════════════════════════════════════════
# OTP EMAIL (CUSTOMIZED & BEAUTIFUL)
# ═══════════════════════════════════════════════════════════════════

def send_otp_email(
    to_email: str,
    otp_code: str,
    user_name: Optional[str] = None,
    purpose: Optional[str] = None
) -> bool:
    """
    Sends an ultra-clean, modern, branded verification OTP email.
    Features:
    - Large high-contrast monospace digit styling
    - Gradient border highlight box
    - Expiration countdown badge
    - Prominent security warnings
    """
    safe_otp = _escape(otp_code)
    greeting = f"Hello {_escape(user_name)}," if user_name else "Hello,"
    reason = f"to complete your {purpose}" if purpose else "to verify your Eventora account and secure your access"

    content = f"""
      <h1 style="margin: 0 0 10px 0; font-size: 22px; font-weight: 800; color: #F8FAFC; letter-spacing: -0.4px;">
        Verify Your Account
      </h1>
      <p style="margin: 0 0 6px 0; font-size: 15px; color: #CBD5E1; line-height: 1.6;">
        {greeting}
      </p>
      <p style="margin: 0 0 24px 0; font-size: 14px; color: #94A3B8; line-height: 1.6;">
        Please enter the one-time verification passcode below {reason}.
      </p>

      <!-- High-Impact Stylized OTP Code Box -->
      <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 24px 0;">
        <tr>
          <td align="center" style="background: linear-gradient(135deg, rgba(99, 102, 241, 0.12) 0%, rgba(168, 85, 247, 0.15) 100%); border: 1.5px solid #6366F1; border-radius: 16px; padding: 26px 20px;">
            <div style="font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 2.5px; color: #A5B4FC; margin-bottom: 8px;">
              Your One-Time Passcode
            </div>
            <div style="font-family: 'SF Mono', Consolas, Menlo, Monaco, 'Courier New', monospace; font-size: 38px; font-weight: 900; letter-spacing: 12px; color: #FFFFFF; text-shadow: 0 0 20px rgba(99, 102, 241, 0.5); padding-left: 12px; margin: 10px 0 14px 0;">
              {safe_otp}
            </div>
            <span style="display: inline-block; background-color: rgba(245, 158, 11, 0.16); border: 1px solid rgba(245, 158, 11, 0.35); border-radius: 9999px; padding: 5px 14px; font-size: 12px; font-weight: 700; color: #FCD34D;">
              &#9201; Valid for 10 minutes only
            </span>
          </td>
        </tr>
      </table>

      <!-- Security Notice Card -->
      <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #172033; border: 1px solid #23314E; border-left: 4px solid #6366F1; border-radius: 10px; margin: 24px 0;">
        <tr>
          <td style="padding: 14px 18px;">
            <p style="margin: 0; font-size: 13px; color: #CBD5E1; line-height: 1.5;">
              <strong style="color: #FFFFFF;">&#128274; Security Notice:</strong> Never share this code with anyone. Eventora staff or administrators will never ask for your verification code.
            </p>
          </td>
        </tr>
      </table>

      <p style="margin: 20px 0 0 0; font-size: 13px; color: #64748B; line-height: 1.6;">
        If you didn't request this verification code, please ignore this email or review your account security.
      </p>
    """

    html = _render_base_email(
        title="Eventora Verification Code",
        preheader=f"Your verification code is {otp_code} — valid for 10 minutes.",
        badge_text="SECURITY VERIFICATION",
        badge_color="#818CF8",
        badge_bg="rgba(99, 102, 241, 0.15)",
        badge_border="rgba(99, 102, 241, 0.35)",
        content_html=content,
        recipient_email=to_email
    )

    subject = f"🔐 {otp_code} is your Eventora verification code"
    return _send_email_payload(to_email=to_email, subject=subject, html=html, sender_name="Eventora Security")


# ═══════════════════════════════════════════════════════════════════
# EMAIL SERVICE CLASS
# ═══════════════════════════════════════════════════════════════════

class EmailService:

    @staticmethod
    def _send(email: str, subject: str, html: str, attachments: Optional[List[Dict[str, Any]]] = None) -> bool:
        """General static sender for EmailService."""
        return _send_email_payload(to_email=email, subject=subject, html=html, attachments=attachments)

    @staticmethod
    def send_otp_email(to_email: str, otp_code: str, user_name: Optional[str] = None) -> bool:
        """Alias for send_otp_email."""
        return send_otp_email(to_email=to_email, otp_code=otp_code, user_name=user_name)

    @staticmethod
    def send_welcome_email(email: str, name: str) -> bool:
        """Sends an engaging welcome email upon completing registration."""
        safe_name = _escape(name)
        content = f"""
          <h1 style="margin: 0 0 10px 0; font-size: 24px; font-weight: 800; color: #F8FAFC; letter-spacing: -0.4px;">
            Welcome to the Stage, {safe_name}! &#127881;
          </h1>
          <p style="margin: 0 0 18px 0; font-size: 15px; color: #CBD5E1; line-height: 1.6;">
            Your Eventora profile registration is complete. You now have full access to explore premier events, compete in live tournaments, and unlock exclusive rewards.
          </p>

          <!-- Feature Cards -->
          <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="margin: 20px 0;">
            <tr>
              <td style="background-color: #172033; border: 1px solid #1E293B; border-radius: 12px; padding: 18px;">
                <div style="font-size: 14px; font-weight: 700; color: #FFFFFF; margin-bottom: 6px;">&#127915; Digital Passes & QR Tickets</div>
                <div style="font-size: 13px; color: #94A3B8; line-height: 1.5;">Access all your event passes instantly with verified biometric & QR entry.</div>
              </td>
            </tr>
            <tr><td height="10"></td></tr>
            <tr>
              <td style="background-color: #172033; border: 1px solid #1E293B; border-radius: 12px; padding: 18px;">
                <div style="font-size: 14px; font-weight: 700; color: #FFFFFF; margin-bottom: 6px;">&#127942; Competitions & Live Ranks</div>
                <div style="font-size: 13px; color: #94A3B8; line-height: 1.5;">Submit creative entries, view real-time judge scoring, and climb leaderboards.</div>
              </td>
            </tr>
            <tr><td height="10"></td></tr>
            <tr>
              <td style="background-color: #172033; border: 1px solid #1E293B; border-radius: 12px; padding: 18px;">
                <div style="font-size: 14px; font-weight: 700; color: #FFFFFF; margin-bottom: 6px;">&#129351; Verified PDF Certificates & Rewards</div>
                <div style="font-size: 13px; color: #94A3B8; line-height: 1.5;">Earn tamper-evident certificates with verifiable QR codes and redeem Eventora coins.</div>
              </td>
            </tr>
          </table>

          <div style="text-align: center; margin: 30px 0 10px 0;">
            <a href="https://eventora.com/dashboard" style="display: inline-block; background: linear-gradient(135deg, #6366F1 0%, #A855F7 100%); color: #FFFFFF; text-decoration: none; padding: 14px 32px; border-radius: 9999px; font-size: 14px; font-weight: 700; letter-spacing: 0.5px; box-shadow: 0 4px 14px rgba(99, 102, 241, 0.4);">
              Go to My Dashboard &rarr;
            </a>
          </div>
        """

        html = _render_base_email(
            title="Welcome to Eventora",
            preheader=f"Hi {name}, your Eventora profile is fully activated. Discover events, passes and competitions.",
            badge_text="ACCOUNT ACTIVATED",
            badge_color="#34D399",
            badge_bg="rgba(52, 211, 153, 0.15)",
            badge_border="rgba(52, 211, 153, 0.35)",
            content_html=content,
            recipient_email=email
        )
        return _send_email_payload(to_email=email, subject=f"🎉 Welcome to Eventora, {name}!", html=html)

    @staticmethod
    def send_judge_invitation(email: str, name: str) -> bool:
        """Sends official judge appointment invitation with clear login instructions."""
        login_url = "https://eventora.com/login"
        safe_name = _escape(name)
        safe_email = _escape(email)

        content = f"""
          <h1 style="margin: 0 0 10px 0; font-size: 22px; font-weight: 800; color: #F8FAFC; letter-spacing: -0.4px;">
            Official Appointment: Judges Panel &#9878;&#65039;
          </h1>
          <p style="margin: 0 0 16px 0; font-size: 15px; color: #CBD5E1; line-height: 1.6;">
            Hello <strong>{safe_name}</strong>,
          </p>
          <p style="margin: 0 0 20px 0; font-size: 14px; color: #94A3B8; line-height: 1.6;">
            Congratulations! You have been officially appointed as an accredited Judge for Eventora competitions. You now have access to review and score tournament submissions.
          </p>

          <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #172033; border: 1px solid #1E293B; border-radius: 14px; padding: 22px; margin: 20px 0;">
            <tr>
              <td>
                <div style="font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: 1.5px; color: #A5B4FC; margin-bottom: 12px;">
                  Quick Onboarding Guide:
                </div>
                <ol style="margin: 0; padding-left: 20px; font-size: 13px; color: #CBD5E1; line-height: 1.8;">
                  <li>Click the button below to open the private Judges Login.</li>
                  <li>Enter your designated email address (<code style="color: #F8FAFC; background: #0F172A; padding: 2px 6px; border-radius: 4px;">{safe_email}</code>).</li>
                  <li>You will receive a secure OTP on this email for your initial sign-in.</li>
                  <li>After logging in, set your permanent password for direct future logins.</li>
                </ol>
              </td>
            </tr>
          </table>

          <div style="text-align: center; margin: 28px 0 10px 0;">
            <a href="{login_url}" style="display: inline-block; background: linear-gradient(135deg, #6366F1 0%, #A855F7 100%); color: #FFFFFF; text-decoration: none; padding: 14px 32px; border-radius: 9999px; font-size: 14px; font-weight: 700; letter-spacing: 0.5px; box-shadow: 0 4px 14px rgba(99, 102, 241, 0.4);">
              Access Judges Panel &rarr;
            </a>
          </div>
        """

        html = _render_base_email(
            title="Eventora Judges Panel Appointment",
            preheader=f"Hello {name}, you have been appointed to the Eventora Judges Panel.",
            badge_text="OFFICIAL APPOINTMENT",
            badge_color="#A78BFA",
            badge_bg="rgba(167, 139, 250, 0.15)",
            badge_border="rgba(167, 139, 250, 0.35)",
            content_html=content,
            recipient_email=email
        )
        return _send_email_payload(to_email=email, subject="⚖️ Official Appointment: Eventora Judges Panel", html=html)

    @staticmethod
    def send_volunteer_invitation(email: str, name: Optional[str] = None) -> bool:
        """Sends official volunteer ticket scanner invitation."""
        login_url = "https://eventora.com/volunteer/login"
        safe_email = _escape(email)
        greeting = f"Hello {_escape(name)}," if name else "Hello,"

        content = f"""
          <h1 style="margin: 0 0 10px 0; font-size: 22px; font-weight: 800; color: #F8FAFC; letter-spacing: -0.4px;">
            Welcome to the Volunteer Squad &#127915;
          </h1>
          <p style="margin: 0 0 16px 0; font-size: 15px; color: #CBD5E1; line-height: 1.6;">
            {greeting}
          </p>
          <p style="margin: 0 0 20px 0; font-size: 14px; color: #94A3B8; line-height: 1.6;">
            You have been assigned as an official <strong>Eventora Volunteer</strong>. Your role includes ticket verification, entry gate validation, and attendee support.
          </p>

          <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #172033; border: 1px solid #1E293B; border-radius: 14px; padding: 22px; margin: 20px 0;">
            <tr>
              <td>
                <div style="font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: 1.5px; color: #38BDF8; margin-bottom: 12px;">
                  How to Access the Volunteer Portal:
                </div>
                <ol style="margin: 0; padding-left: 20px; font-size: 13px; color: #CBD5E1; line-height: 1.8;">
                  <li>Visit the Volunteer Portal via the link below.</li>
                  <li>Enter your assigned email: <code style="color: #F8FAFC; background: #0F172A; padding: 2px 6px; border-radius: 4px;">{safe_email}</code>.</li>
                  <li>Verify using the instant OTP sent to your inbox.</li>
                  <li>Use the in-app scanner to validate attendee QR codes at entrance gates.</li>
                </ol>
              </td>
            </tr>
          </table>

          <div style="text-align: center; margin: 28px 0 10px 0;">
            <a href="{login_url}" style="display: inline-block; background: linear-gradient(135deg, #0284C7 0%, #38BDF8 100%); color: #FFFFFF; text-decoration: none; padding: 14px 32px; border-radius: 9999px; font-size: 14px; font-weight: 700; letter-spacing: 0.5px; box-shadow: 0 4px 14px rgba(2, 132, 199, 0.4);">
              Open Volunteer Scanner &rarr;
            </a>
          </div>
        """

        html = _render_base_email(
            title="Eventora Volunteer Assignment",
            preheader="You have been added as an official Eventora Volunteer for ticket scanning.",
            badge_text="VOLUNTEER ACCESS",
            badge_color="#38BDF8",
            badge_bg="rgba(56, 189, 248, 0.15)",
            badge_border="rgba(56, 189, 248, 0.35)",
            content_html=content,
            recipient_email=email
        )
        return _send_email_payload(to_email=email, subject="🎟️ Volunteer Access Granted: Eventora Ticket Scanner", html=html)

    @staticmethod
    def send_certificate_email(
        email: str,
        name: str,
        activity_title: str,
        cert_type: str,
        rank: Optional[int] = None,
        award_title: Optional[str] = None,
        verification_url: Optional[str] = None,
        pdf_bytes: Optional[bytes] = None
    ) -> bool:
        """Sends certificate issuance email with optional PDF attachment and online verification link."""
        display_type = cert_type.replace("_", " ").title()
        safe_name = _escape(name)
        safe_activity = _escape(activity_title)
        safe_display = _escape(display_type)

        rank_badge = ""
        if rank:
            award_txt = f" &bull; {_escape(award_title)}" if award_title else ""
            rank_badge = f"""
            <div style="display: inline-block; background-color: rgba(245, 158, 11, 0.15); border: 1px solid rgba(245, 158, 11, 0.35); border-radius: 8px; padding: 8px 16px; margin: 12px 0; font-size: 14px; font-weight: 700; color: #FCD34D;">
              &#127942; Rank {rank}{award_txt}
            </div>
            """

        verify_btn = ""
        if verification_url:
            verify_btn = f"""
            <div style="text-align: center; margin: 26px 0 10px 0;">
              <a href="{verification_url}" style="display: inline-block; background: linear-gradient(135deg, #6366F1 0%, #A855F7 100%); color: #FFFFFF; text-decoration: none; padding: 14px 32px; border-radius: 9999px; font-size: 14px; font-weight: 700; letter-spacing: 0.5px; box-shadow: 0 4px 14px rgba(99, 102, 241, 0.4);">
                View &amp; Verify Certificate &rarr;
              </a>
            </div>
            """

        content = f"""
          <h1 style="margin: 0 0 10px 0; font-size: 22px; font-weight: 800; color: #F8FAFC; letter-spacing: -0.4px;">
            Certificate of Achievement &#127941;
          </h1>
          <p style="margin: 0 0 16px 0; font-size: 15px; color: #CBD5E1; line-height: 1.6;">
            Congratulations <strong>{safe_name}</strong>!
          </p>
          <p style="margin: 0 0 20px 0; font-size: 14px; color: #94A3B8; line-height: 1.6;">
            Your official digital <strong>{safe_display} Certificate</strong> for <strong>{safe_activity}</strong> has been issued and permanently recorded on Eventora.
          </p>

          <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #172033; border: 1px solid #1E293B; border-radius: 14px; padding: 22px; margin: 20px 0; text-align: center;">
            <tr>
              <td>
                <div style="font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 2px; color: #A5B4FC; margin-bottom: 6px;">
                  Issued For
                </div>
                <div style="font-size: 18px; font-weight: 800; color: #FFFFFF; margin-bottom: 6px;">
                  {safe_activity}
                </div>
                <div style="font-size: 13px; color: #94A3B8;">
                  Category: <span style="color: #CBD5E1; font-weight: 600;">{safe_display}</span>
                </div>
                {rank_badge}
              </td>
            </tr>
          </table>

          {verify_btn}

          <p style="margin: 22px 0 0 0; font-size: 13px; color: #64748B; line-height: 1.6; text-align: center;">
            Your official high-resolution certificate PDF is attached to this email. You can also view and download it at any time from your user dashboard.
          </p>
        """

        html = _render_base_email(
            title=f"Eventora {display_type} Certificate",
            preheader=f"Congratulations {name}! Your official certificate for {activity_title} is ready.",
            badge_text="OFFICIAL CERTIFICATE",
            badge_color="#F59E0B",
            badge_bg="rgba(245, 158, 11, 0.15)",
            badge_border="rgba(245, 158, 11, 0.35)",
            content_html=content,
            recipient_email=email
        )

        attachments = None
        if pdf_bytes:
            attachments = [{
                "filename": f"{cert_type.lower()}_certificate.pdf",
                "content": list(pdf_bytes)
            }]

        subject = f"🏆 Congratulations! Your Eventora {display_type} Certificate is Ready"
        return _send_email_payload(to_email=email, subject=subject, html=html, attachments=attachments)

    @staticmethod
    def send_vip_pass_confirmation(
        email: str,
        name: str,
        event_title: str,
        pass_name: str,
        quantity: int,
        booking_reference: str
    ) -> bool:
        """Sends official VIP Pass booking confirmation with entrance instructions."""
        safe_name = _escape(name)
        safe_event = _escape(event_title)
        safe_pass = _escape(pass_name)
        safe_ref = _escape(booking_reference)

        content = f"""
          <h1 style="margin: 0 0 10px 0; font-size: 22px; font-weight: 800; color: #F8FAFC; letter-spacing: -0.4px;">
            VIP Pass Booking Confirmed &#128081;
          </h1>
          <p style="margin: 0 0 16px 0; font-size: 15px; color: #CBD5E1; line-height: 1.6;">
            Hello <strong>{safe_name}</strong>,
          </p>
          <p style="margin: 0 0 20px 0; font-size: 14px; color: #94A3B8; line-height: 1.6;">
            Your VIP Admission Pass for <strong>{safe_event}</strong> has been confirmed and verified. You have guaranteed priority access at the event entrance.
          </p>

          <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #172033; border: 1.5px solid rgba(245, 158, 11, 0.3); border-radius: 14px; padding: 22px; margin: 20px 0;">
            <tr>
              <td>
                <div style="font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 2px; color: #FCD34D; margin-bottom: 14px;">
                  Pass Credentials
                </div>
                <table role="presentation" border="0" cellpadding="4" cellspacing="0" width="100%" style="font-size: 13px;">
                  <tr>
                    <td style="color: #64748B; width: 40%;">Pass Type:</td>
                    <td style="color: #FFFFFF; font-weight: 700;">{safe_pass}</td>
                  </tr>
                  <tr>
                    <td style="color: #64748B;">Quantity:</td>
                    <td style="color: #FFFFFF; font-weight: 700;">{quantity} Pass(es)</td>
                  </tr>
                  <tr>
                    <td style="color: #64748B;">Reference Code:</td>
                    <td style="color: #F8FAFC; font-family: monospace; font-weight: 700; background: #0F172A; padding: 3px 8px; border-radius: 4px;">{safe_ref}</td>
                  </tr>
                </table>
              </td>
            </tr>
          </table>

          <div style="text-align: center; margin: 28px 0 10px 0;">
            <a href="https://eventora.com/dashboard?tab=vip" style="display: inline-block; background: linear-gradient(135deg, #D97706 0%, #F59E0B 100%); color: #000000; text-decoration: none; padding: 14px 32px; border-radius: 9999px; font-size: 14px; font-weight: 800; letter-spacing: 0.5px; box-shadow: 0 4px 14px rgba(245, 158, 11, 0.35);">
              View Digital VIP Pass &rarr;
            </a>
          </div>

          <p style="margin: 22px 0 0 0; font-size: 13px; color: #64748B; line-height: 1.6; text-align: center;">
            &#128081; Please present your digital QR code at the VIP Entrance Gate on event day for priority check-in.
          </p>
        """

        html = _render_base_email(
            title="VIP Pass Confirmed",
            preheader=f"VIP Pass confirmed for {event_title}. Booking Reference: {booking_reference}",
            badge_text="VIP ADMISSION",
            badge_color="#FCD34D",
            badge_bg="rgba(245, 158, 11, 0.15)",
            badge_border="rgba(245, 158, 11, 0.35)",
            content_html=content,
            recipient_email=email
        )
        return _send_email_payload(to_email=email, subject=f"👑 VIP Pass Confirmed: {event_title}", html=html, sender_name="Eventora VIP")

    @staticmethod
    def send_merchandise_order_confirmation(
        email: str,
        name: str,
        order_number: str,
        total_amount: float,
        currency: str,
        items_count: int
    ) -> bool:
        """Sends official receipt for merchandise store purchase."""
        safe_name = _escape(name) or "Customer"
        safe_order = _escape(order_number)

        content = f"""
          <h1 style="margin: 0 0 10px 0; font-size: 22px; font-weight: 800; color: #F8FAFC; letter-spacing: -0.4px;">
            Order Confirmed! &#128092;
          </h1>
          <p style="margin: 0 0 16px 0; font-size: 15px; color: #CBD5E1; line-height: 1.6;">
            Dear <strong>{safe_name}</strong>,
          </p>
          <p style="margin: 0 0 20px 0; font-size: 14px; color: #94A3B8; line-height: 1.6;">
            Thank you for purchasing official Eventora merchandise! Your payment has been verified and your order is currently being prepared.
          </p>

          <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #172033; border: 1px solid #1E293B; border-radius: 14px; padding: 22px; margin: 20px 0;">
            <tr>
              <td>
                <div style="font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 2px; color: #A5B4FC; margin-bottom: 14px;">
                  Order Summary
                </div>
                <table role="presentation" border="0" cellpadding="4" cellspacing="0" width="100%" style="font-size: 13px;">
                  <tr>
                    <td style="color: #64748B; width: 40%;">Order Number:</td>
                    <td style="color: #F8FAFC; font-family: monospace; font-weight: 700;">{safe_order}</td>
                  </tr>
                  <tr>
                    <td style="color: #64748B;">Total Items:</td>
                    <td style="color: #FFFFFF; font-weight: 700;">{items_count} Item(s)</td>
                  </tr>
                  <tr>
                    <td style="color: #64748B;">Total Amount:</td>
                    <td style="color: #34D399; font-weight: 800; font-size: 15px;">{total_amount} {currency}</td>
                  </tr>
                  <tr>
                    <td style="color: #64748B;">Payment Status:</td>
                    <td style="color: #34D399; font-weight: 700;">PAID &bull; VERIFIED</td>
                  </tr>
                </table>
              </td>
            </tr>
          </table>

          <div style="text-align: center; margin: 28px 0 10px 0;">
            <a href="https://eventora.com/dashboard?tab=orders" style="display: inline-block; background: linear-gradient(135deg, #6366F1 0%, #A855F7 100%); color: #FFFFFF; text-decoration: none; padding: 14px 32px; border-radius: 9999px; font-size: 14px; font-weight: 700; letter-spacing: 0.5px; box-shadow: 0 4px 14px rgba(99, 102, 241, 0.4);">
              Track My Order &rarr;
            </a>
          </div>
        """

        html = _render_base_email(
            title="Merchandise Order Confirmed",
            preheader=f"Order {order_number} confirmed. Total: {total_amount} {currency}",
            badge_text="ORDER CONFIRMED",
            badge_color="#34D399",
            badge_bg="rgba(52, 211, 153, 0.15)",
            badge_border="rgba(52, 211, 153, 0.35)",
            content_html=content,
            recipient_email=email
        )
        return _send_email_payload(to_email=email, subject=f"🛍️ Order Confirmed: {order_number} - Eventora Official Merchandise", html=html)

    @staticmethod
    def send_merchandise_fulfillment_update(
        email: str,
        name: str,
        order_number: str,
        status: str,
        courier_name: Optional[str] = None,
        tracking_number: Optional[str] = None
    ) -> bool:
        """Sends shipping / fulfillment status update for a merchandise order."""
        safe_name = _escape(name) or "Customer"
        safe_order = _escape(order_number)
        safe_status = _escape(status).upper()

        tracking_rows = ""
        if courier_name or tracking_number:
            c_val = _escape(courier_name) or "Standard Logistics"
            t_val = _escape(tracking_number) or "Pending"
            tracking_rows = f"""
              <tr>
                <td style="color: #64748B;">Courier Partner:</td>
                <td style="color: #FFFFFF; font-weight: 700;">{c_val}</td>
              </tr>
              <tr>
                <td style="color: #64748B;">Tracking Number:</td>
                <td style="color: #818CF8; font-family: monospace; font-weight: 700;">{t_val}</td>
              </tr>
            """

        content = f"""
          <h1 style="margin: 0 0 10px 0; font-size: 22px; font-weight: 800; color: #F8FAFC; letter-spacing: -0.4px;">
            Order Status Update &#128230;
          </h1>
          <p style="margin: 0 0 16px 0; font-size: 15px; color: #CBD5E1; line-height: 1.6;">
            Dear <strong>{safe_name}</strong>,
          </p>
          <p style="margin: 0 0 20px 0; font-size: 14px; color: #94A3B8; line-height: 1.6;">
            Your merchandise order <strong>{safe_order}</strong> has been updated to: <span style="color: #818CF8; font-weight: 800;">{safe_status}</span>.
          </p>

          <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #172033; border: 1px solid #1E293B; border-radius: 14px; padding: 22px; margin: 20px 0;">
            <tr>
              <td>
                <div style="font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 2px; color: #A5B4FC; margin-bottom: 14px;">
                  Fulfillment Details
                </div>
                <table role="presentation" border="0" cellpadding="4" cellspacing="0" width="100%" style="font-size: 13px;">
                  <tr>
                    <td style="color: #64748B; width: 40%;">Order Number:</td>
                    <td style="color: #F8FAFC; font-family: monospace; font-weight: 700;">{safe_order}</td>
                  </tr>
                  <tr>
                    <td style="color: #64748B;">Current Status:</td>
                    <td style="color: #38BDF8; font-weight: 800;">{safe_status}</td>
                  </tr>
                  {tracking_rows}
                </table>
              </td>
            </tr>
          </table>

          <div style="text-align: center; margin: 28px 0 10px 0;">
            <a href="https://eventora.com/dashboard?tab=orders" style="display: inline-block; background: linear-gradient(135deg, #0284C7 0%, #38BDF8 100%); color: #FFFFFF; text-decoration: none; padding: 14px 32px; border-radius: 9999px; font-size: 14px; font-weight: 700; letter-spacing: 0.5px; box-shadow: 0 4px 14px rgba(2, 132, 199, 0.4);">
              View Live Tracking &rarr;
            </a>
          </div>
        """

        html = _render_base_email(
            title="Order Status Update",
            preheader=f"Order {order_number} status updated to {status}",
            badge_text=f"STATUS: {safe_status}",
            badge_color="#38BDF8",
            badge_bg="rgba(56, 189, 248, 0.15)",
            badge_border="rgba(56, 189, 248, 0.35)",
            content_html=content,
            recipient_email=email
        )
        return _send_email_payload(to_email=email, subject=f"📦 Order Update: {order_number} is now {status}", html=html)

    @staticmethod
    def send_ticket_confirmation(
        email: str,
        name: str,
        event_title: str,
        ticket_reference: str,
        event_date: Optional[str] = None,
        venue: Optional[str] = None,
        quantity: int = 1
    ) -> bool:
        """Sends ticket booking confirmation email."""
        safe_name = _escape(name)
        safe_event = _escape(event_title)
        safe_ref = _escape(ticket_reference)
        safe_date = _escape(event_date) or "Check event schedule on dashboard"
        safe_venue = _escape(venue) or "Official Event Arena"

        content = f"""
          <h1 style="margin: 0 0 10px 0; font-size: 22px; font-weight: 800; color: #F8FAFC; letter-spacing: -0.4px;">
            Your Ticket is Confirmed! &#127915;
          </h1>
          <p style="margin: 0 0 16px 0; font-size: 15px; color: #CBD5E1; line-height: 1.6;">
            Hello <strong>{safe_name}</strong>,
          </p>
          <p style="margin: 0 0 20px 0; font-size: 14px; color: #94A3B8; line-height: 1.6;">
            Your admission ticket for <strong>{safe_event}</strong> is confirmed. Your digital QR pass is ready in your account.
          </p>

          <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #172033; border: 1px solid #1E293B; border-radius: 14px; padding: 22px; margin: 20px 0;">
            <tr>
              <td>
                <div style="font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 2px; color: #A5B4FC; margin-bottom: 14px;">
                  Event Admission Details
                </div>
                <table role="presentation" border="0" cellpadding="4" cellspacing="0" width="100%" style="font-size: 13px;">
                  <tr>
                    <td style="color: #64748B; width: 40%;">Event:</td>
                    <td style="color: #FFFFFF; font-weight: 700;">{safe_event}</td>
                  </tr>
                  <tr>
                    <td style="color: #64748B;">Date / Time:</td>
                    <td style="color: #CBD5E1; font-weight: 600;">{safe_date}</td>
                  </tr>
                  <tr>
                    <td style="color: #64748B;">Venue:</td>
                    <td style="color: #CBD5E1; font-weight: 600;">{safe_venue}</td>
                  </tr>
                  <tr>
                    <td style="color: #64748B;">Quantity:</td>
                    <td style="color: #FFFFFF; font-weight: 700;">{quantity} Ticket(s)</td>
                  </tr>
                  <tr>
                    <td style="color: #64748B;">Ticket Reference:</td>
                    <td style="color: #818CF8; font-family: monospace; font-weight: 700;">{safe_ref}</td>
                  </tr>
                </table>
              </td>
            </tr>
          </table>

          <div style="text-align: center; margin: 28px 0 10px 0;">
            <a href="https://eventora.com/dashboard?tab=tickets" style="display: inline-block; background: linear-gradient(135deg, #6366F1 0%, #A855F7 100%); color: #FFFFFF; text-decoration: none; padding: 14px 32px; border-radius: 9999px; font-size: 14px; font-weight: 700; letter-spacing: 0.5px; box-shadow: 0 4px 14px rgba(99, 102, 241, 0.4);">
              View My QR Pass &rarr;
            </a>
          </div>
        """

        html = _render_base_email(
            title="Ticket Confirmed",
            preheader=f"Your ticket for {event_title} is confirmed. Reference: {ticket_reference}",
            badge_text="ADMISSION CONFIRMED",
            badge_color="#818CF8",
            badge_bg="rgba(99, 102, 241, 0.15)",
            badge_border="rgba(99, 102, 241, 0.35)",
            content_html=content,
            recipient_email=email
        )
        return _send_email_payload(to_email=email, subject=f"🎟️ Ticket Confirmed: {event_title}", html=html)
