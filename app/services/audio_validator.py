import os
import re
from typing import Optional
from fastapi import HTTPException, UploadFile

ALLOWED_AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".wma",
    ".opus",
    ".aiff",
    ".alac",
}

ALLOWED_AUDIO_MIMES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/x-pn-wav",
    "audio/aac",
    "audio/x-aac",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/flac",
    "audio/x-flac",
    "audio/ogg",
    "application/ogg",
    "audio/opus",
    "audio/wma",
    "audio/x-ms-wma",
    "audio/aiff",
    "audio/x-aiff",
    "audio/webm",
    "application/octet-stream",  # Some browsers/OS send octet-stream for .wav or .m4a
}

URL_REGEX = re.compile(
    r"^https?://[^\s/$.?#].[^\s]*$",
    re.IGNORECASE,
)

FORBIDDEN_BINARY_SIGNATURES = [
    (b"%PDF", "PDF document"),
    (b"\x89PNG\r\n\x1a\n", "PNG image"),
    (b"\xff\xd8\xff", "JPEG image"),
    (b"GIF87a", "GIF image"),
    (b"GIF89a", "GIF image"),
    (b"BM", "Bitmap image"),
    (b"MZ", "Windows executable"),
    (b"\x7fELF", "Linux binary executable"),
    (b"PK\x03\x04", "ZIP or Office archive"),
]


class AudioValidator:
    @staticmethod
    async def validate_and_extract_link(file_obj: UploadFile, field_name: str) -> Optional[str]:
        """
        Detects if this upload is a link reference (e.g. YouTube or Google Drive link).
        Returns the extracted URL if valid, or None if it is a regular binary file.
        Raises HTTPException if it is a text link file with an invalid URL.
        """
        filename = (file_obj.filename or "").lower()
        content_type = (file_obj.content_type or "").lower()

        is_link_candidate = (
            filename.endswith("_link.txt")
            or filename.endswith(".link")
            or content_type in ("text/plain", "text/uri-list")
        )

        if not is_link_candidate:
            return None

        # Read content to verify if it contains a web link
        raw_bytes = await file_obj.read()
        await file_obj.seek(0)

        text = raw_bytes.decode("utf-8", errors="ignore").strip()
        if text.startswith("http://") or text.startswith("https://"):
            if not URL_REGEX.match(text):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid link URL format for '{field_name}'.",
                )
            if "res.cloudinary.com/demo" in text or "cloudinary.com/demo" in text:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid link submitted for '{field_name}'. Fake or demo Cloudinary URLs are strictly prohibited.",
                )
            return text

        # If it was named *_link.txt specifically but does not have a URL
        if filename.endswith("_link.txt") or filename.endswith(".link"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid link submitted for '{field_name}'. Must be a valid URL starting with http:// or https:// (e.g. YouTube or Google Drive).",
            )

        return None

    @staticmethod
    async def validate_audio_file(file_obj: UploadFile, field_name: str) -> None:
        """
        Validates that the uploaded file is strictly an audio file:
        1. Checks extension against allowed audio extensions.
        2. Checks MIME type against audio mimes.
        3. Inspects binary magic bytes to reject disguised non-audio files (images, PDFs, executables, video).
        """
        filename = file_obj.filename or ""
        ext = os.path.splitext(filename)[1].lower()

        if ext not in ALLOWED_AUDIO_EXTENSIONS:
            allowed_list = ", ".join(sorted(ALLOWED_AUDIO_EXTENSIONS))
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid file type '{ext or 'unknown'}' for '{field_name}'. "
                    f"Only audio files ({allowed_list}) or YouTube/Google Drive links are accepted."
                ),
            )

        # Check content type if specified
        if file_obj.content_type:
            ct = file_obj.content_type.lower()
            if not (ct.startswith("audio/") or ct in ALLOWED_AUDIO_MIMES):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid content type '{file_obj.content_type}' for '{field_name}'. Expected an audio file.",
                )

        # Read first 512 bytes for magic byte verification
        header = await file_obj.read(512)
        await file_obj.seek(0)

        # Reject known non-audio formats
        for sig, label in FORBIDDEN_BINARY_SIGNATURES:
            if header.startswith(sig):
                raise HTTPException(
                    status_code=400,
                    detail=f"Uploaded file for '{field_name}' is a {label}, not a supported audio file.",
                )

        # Specific check for RIFF AVI / WEBP
        if header.startswith(b"RIFF") and len(header) >= 12:
            fourcc = header[8:12]
            if fourcc == b"WEBP":
                raise HTTPException(
                    status_code=400,
                    detail=f"Uploaded file for '{field_name}' is a WebP image, not an audio file.",
                )
            if fourcc == b"AVI ":
                raise HTTPException(
                    status_code=400,
                    detail=f"Uploaded file for '{field_name}' is an AVI video file, not an audio file.",
                )

        # Specific check for MP4 video container when not .m4a
        if len(header) >= 12 and header[4:8] == b"ftyp":
            brand = header[8:12]
            if brand in (b"isom", b"mp41", b"avc1", b"qt  ") and ext not in (".m4a", ".aac"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Uploaded file for '{field_name}' is a video container, not an audio file.",
                )
