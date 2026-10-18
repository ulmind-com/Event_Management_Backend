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
        if secure_url:
            logger.info(
                f"Successfully uploaded to Cloudinary: {secure_url} (type={response.get('resource_type')})"
            )
            return secure_url
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
            if secure_url:
                logger.info(
                    f"Cloudinary fallback (raw) upload succeeded: {secure_url}"
                )
                return secure_url
        except Exception as fallback_exc:
            logger.error(
                f"Cloudinary fallback (raw) upload also failed: {fallback_exc}",
                exc_info=True,
            )

    return None

