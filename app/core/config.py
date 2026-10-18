
import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # MongoDB
    MONGODB_URI = os.getenv("MONGODB_URI")

    # Cloudinary
    CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

    # Email (Resend)
    RESEND_API_KEY = os.getenv("RESEND_API_KEY")
    MAIL_ADDRESS = os.getenv("MAIL_ADDRESS")

    # JWT
    SECRET_KEY = os.getenv("SECRET_KEY", "yoursecretkeyhere")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    ADMIN_TOKEN_EXPIRE_MINUTES = int(os.getenv("ADMIN_TOKEN_EXPIRE_MINUTES", "60"))

    # Super Admin (from .env only — never hardcode)
    SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL")
    SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD")

    # CORS
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

    # Razorpay & Payment Gateway Configuration (from .env only - never hardcode)
    RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
    RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
    PAYMENT_GATEWAY_PROVIDER = os.getenv("PAYMENT_GATEWAY_PROVIDER", "RAZORPAY")
    PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "RAZORPAY")
    MERCHANDISE_PAYMENT_PROVIDER = os.getenv("MERCHANDISE_PAYMENT_PROVIDER", "RAZORPAY")
    PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET")

    # Project
    FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "event-management-4fe6f")
    PROJECT_NAME = "Eventora API"

    # Notification System Configuration
    NOTIFICATIONS_ENABLED = os.getenv("NOTIFICATIONS_ENABLED", "true").lower() in ("true", "1", "yes")
    NOTIFICATION_WEBSOCKET_ENABLED = os.getenv("NOTIFICATION_WEBSOCKET_ENABLED", "true").lower() in ("true", "1", "yes")
    NOTIFICATION_SCHEDULER_ENABLED = os.getenv("NOTIFICATION_SCHEDULER_ENABLED", "true").lower() in ("true", "1", "yes")
    NOTIFICATION_MAX_RETRIES = int(os.getenv("NOTIFICATION_MAX_RETRIES", "3"))
    NOTIFICATION_BATCH_SIZE = int(os.getenv("NOTIFICATION_BATCH_SIZE", "100"))
    NOTIFICATION_SCHEDULER_INTERVAL_SECONDS = int(os.getenv("NOTIFICATION_SCHEDULER_INTERVAL_SECONDS", "60"))

    # Push Providers (FCM / APNs)
    PUSH_PROVIDER = os.getenv("PUSH_PROVIDER", "FCM")
    FCM_CREDENTIALS_PATH = os.getenv("FCM_CREDENTIALS_PATH", "firebase_service_account.json")

    # Web Push
    WEB_PUSH_ENABLED = os.getenv("WEB_PUSH_ENABLED", "false").lower() in ("true", "1", "yes")
    WEB_PUSH_VAPID_PUBLIC_KEY = os.getenv("WEB_PUSH_VAPID_PUBLIC_KEY", "")
    WEB_PUSH_VAPID_PRIVATE_KEY = os.getenv("WEB_PUSH_VAPID_PRIVATE_KEY", "")
    WEB_PUSH_SUBJECT = os.getenv("WEB_PUSH_SUBJECT", "mailto:noreply@eventora.com")

    # Aadhaar KYC & DigiLocker Verification
    AADHAAR_HASH_SALT = os.getenv("AADHAAR_HASH_SALT", "eventora_kyc_salt_v1_production")
    TESSERACT_CMD = os.getenv("TESSERACT_CMD", "")  # e.g. "C:\\Program Files\\Tesseract-OCR\\tesseract.exe" on Windows
    DIGILOCKER_CLIENT_ID = os.getenv("DIGILOCKER_CLIENT_ID", "EVENTORA_DIGILOCKER_PROD")
    DIGILOCKER_CLIENT_SECRET = os.getenv("DIGILOCKER_CLIENT_SECRET", "")
    DIGILOCKER_REDIRECT_URI = os.getenv(
        "DIGILOCKER_REDIRECT_URI",
        "https://event-management-company-project.onrender.com/api/kyc/digilocker/callback",
    )
    DIGILOCKER_AUTH_URL = os.getenv(
        "DIGILOCKER_AUTH_URL",
        "https://api.digitallocker.gov.in/public/oauth2/1/authorize",
    )
    DIGILOCKER_TOKEN_URL = os.getenv(
        "DIGILOCKER_TOKEN_URL",
        "https://api.digitallocker.gov.in/public/oauth2/1/token",
    )
    DIGILOCKER_EAADHAAR_URL = os.getenv(
        "DIGILOCKER_EAADHAAR_URL",
        "https://api.digitallocker.gov.in/public/oauth2/2/xml/eaadhaar",
    )
    DIGILOCKER_ENV = os.getenv("DIGILOCKER_ENV", "production")  # "production" or "sandbox"

    # SMS Gateway Configuration (Fast2SMS / Twilio)
    FAST2SMS_API_KEY = os.getenv("FAST2SMS_API_KEY", "")
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "")

settings = Settings()

