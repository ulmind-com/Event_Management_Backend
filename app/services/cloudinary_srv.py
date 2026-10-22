import os
import io
import logging
from typing import Any, Optional
import cloudinary
import cloudinary.uploader
from app.core.config import settings

logger = logging.getLogger(__name__)

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
)


def is_valid_cloudinary_url(url: Optional[str]) -> bool:
    """
    Strictly validates that a URL is a genuine Cloudinary secure URL
    belonging to the configured cloud name, and NOT a fake/demo/placeholder URL.
    """
    if not url or not isinstance(url, str):
        return False

    url_clean = url.strip()

    # Must start with standard Cloudinary CDN domain
    if not (
        url_clean.startswith("https://res.cloudinary.com/")
        or url_clean.startswith("http://res.cloudinary.com/")
    ):
        return False

    # Strictly reject fake demo or placeholder URLs
    forbidden_indicators = ["/demo/", "example.com", "placeholder", "fake"]
    if any(fi in url_clean.lower() for fi in forbidden_indicators):
        logger.warning(f"Rejected suspicious/fake Cloudinary URL: {url_clean}")
        return False

    # Must contain configured cloud name if configured
    if settings.CLOUDINARY_CLOUD_NAME:
        expected_cloud = settings.CLOUDINARY_CLOUD_NAME.strip().lower()
        # The cloud name appears right after res.cloudinary.com/
        # e.g., https://res.cloudinary.com/<cloud_name>/image/upload/...
        parts = url_clean.split("res.cloudinary.com/")
        if len(parts) > 1:
            cloud_segment = parts[1].split("/")[0].lower()
            if cloud_segment != expected_cloud:
                logger.warning(
                    f"Cloudinary URL cloud name mismatch: expected '{expected_cloud}', got '{cloud_segment}' in {url_clean}"
                )
                return False

    return True


def upload_image(
    file_data: Any,
    folder: str = "eventora/profiles",
    resource_type: str = "auto",
    filename: Optional[str] = None,
) -> Optional[str]:
    """
    Upload file or media stream to Cloudinary with multi-strategy fallback.
    Supports images, audio, video, and raw documents (PDFs, etc.).
    Preserves original file extensions and handles seekable streams/bytes safely.
    Strictly validates that returned URL is a genuine Cloudinary URL under configured cloud.
    """
    if not settings.CLOUDINARY_CLOUD_NAME or not settings.CLOUDINARY_API_KEY:
        logger.error("Cloudinary credentials not configured in settings.")
        return None

    # Rewind stream if it is a seekable file-like object
    if hasattr(file_data, "seek"):
        try:
            file_data.seek(0)
        except Exception:
            pass

    # Extract clean filename if provided
    clean_filename = None
    if filename:
        clean_filename = os.path.basename(filename)

    upload_options: dict[str, Any] = {
        "folder": folder,
        "resource_type": resource_type,
    }

    if clean_filename:
        upload_options["use_filename"] = True
        upload_options["unique_filename"] = True
        upload_options["filename_override"] = clean_filename

    # Strategy 1: Attempt standard upload (default: resource_type='auto')
    try:
        response = cloudinary.uploader.upload(file_data, **upload_options)
        secure_url = response.get("secure_url")
        if secure_url and is_valid_cloudinary_url(secure_url):
            logger.info(
                f"Successfully uploaded to Cloudinary: {secure_url} (type={response.get('resource_type')})"
            )
            return secure_url
        elif secure_url:
            logger.error(f"Cloudinary upload returned invalid/untrusted URL: {secure_url}")
    except Exception as exc:
        logger.warning(
            f"Cloudinary primary upload attempt failed ({exc}). Retrying with resource_type='raw'..."
        )

    # Strategy 2: Fallback to raw binary storage if auto-transcoder failed (e.g. unsupported audio/video codec)
    if resource_type == "auto":
        if hasattr(file_data, "seek"):
            try:
                file_data.seek(0)
            except Exception:
                pass

        try:
            fallback_options = dict(upload_options)
            fallback_options["resource_type"] = "raw"
            response = cloudinary.uploader.upload(file_data, **fallback_options)
            secure_url = response.get("secure_url")
            if secure_url and is_valid_cloudinary_url(secure_url):
                logger.info(
                    f"Cloudinary fallback (raw) upload succeeded: {secure_url}"
                )
                return secure_url
            elif secure_url:
                logger.error(f"Cloudinary fallback upload returned invalid/untrusted URL: {secure_url}")
        except Exception as fallback_exc:
            logger.error(
                f"Cloudinary fallback (raw) upload also failed: {fallback_exc}",
                exc_info=True,
            )

    return None

