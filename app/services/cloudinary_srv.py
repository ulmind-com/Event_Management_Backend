import cloudinary
import cloudinary.uploader
from app.core.config import settings

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
)


def upload_image(file_data, folder="eventora/profiles"):
    """Upload file to Cloudinary. Supports images and raw files (PDFs, etc.)."""
    try:
        # Try uploading as auto (Cloudinary auto-detects image/raw/video)
        response = cloudinary.uploader.upload(
            file_data,
            folder=folder,
            resource_type="auto",
        )
        return response.get("secure_url")
    except Exception as e:
        print(f"Cloudinary upload error: {e}")
        return None
