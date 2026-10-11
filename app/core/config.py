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

    # Project
    FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "event-management-4fe6f")
    PROJECT_NAME = "Eventora API"

settings = Settings()
