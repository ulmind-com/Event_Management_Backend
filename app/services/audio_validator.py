import os
import re
from typing import Optional, Tuple
from fastapi import HTTPException, UploadFile

ALLOWED_MP3_EXTENSIONS = {".mp3"}

ALLOWED_MP3_MIMES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/x-mpeg",
    "audio/x-mp3",
    "application/octet-stream",  # Fallback for systems that treat mp3 as octet-stream
}

YOUTUBE_REGEX = re.compile(
    r"^(https?://)?(www\.|m\.|music\.)?(youtube\.com/(watch\?v=|embed/|shorts/|v/)|youtu\.be/)([a-zA-Z0-9_-]{11})([?&][^\s]*)?$",
    re.IGNORECASE,
)

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

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".flv",
    ".wmv",
    ".m4v",
    ".3gp",
}


class AudioValidator:
    @staticmethod
    def is_valid_youtube_url(url: str) -> bool:
        """Checks if a URL is a valid YouTube video link."""
        if not url or not isinstance(url, str):
            return False
        clean = url.strip()
        return bool(YOUTUBE_REGEX.match(clean))

    @staticmethod
    async def validate_and_extract_link(file_obj: UploadFile, field_name: str) -> Optional[str]:
        """
        Detects if this upload is a link reference (e.g. YouTube link).
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
                detail=f"Invalid link submitted for '{field_name}'. Must be a valid URL starting with http:// or https:// (e.g. YouTube video link).",
            )

        return None

    @classmethod
    async def validate_mp3_file(cls, file_obj: UploadFile, field_name: str) -> None:
        """
        Validates that the uploaded file is strictly an MP3 audio file:
        1. Checks extension against .mp3.
        2. Checks MIME type against audio/mpeg.
        3. Inspects binary magic bytes for ID3 tag or MPEG sync header.
        """
        filename = file_obj.filename or ""
        ext = os.path.splitext(filename)[1].lower()

        if ext != ".mp3":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid file type '{ext or 'unknown'}' for '{field_name}'. "
                    f"Only MP3 audio files (.mp3) are permitted for audio submissions."
                ),
            )

        # Check content type if specified
        if file_obj.content_type:
            ct = file_obj.content_type.lower()
            if ct not in ALLOWED_MP3_MIMES and not ct.startswith("audio/"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid content type '{file_obj.content_type}' for '{field_name}'. Expected an MP3 audio file.",
                )

        # Read first 512 bytes for magic byte verification
        header = await file_obj.read(512)
        await file_obj.seek(0)

        if not header:
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded file for '{field_name}' is empty.",
            )

        # Reject known non-audio formats
        for sig, label in FORBIDDEN_BINARY_SIGNATURES:
            if header.startswith(sig):
                raise HTTPException(
                    status_code=400,
                    detail=f"Uploaded file for '{field_name}' is a {label}, not a supported MP3 audio file.",
                )

        # Reject RIFF WAV / AVI / WEBP
        if header.startswith(b"RIFF"):
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded file for '{field_name}' is a RIFF container (WAV/AVI), not an MP3 audio file. Only .mp3 is permitted.",
            )

        # Reject MP4 / M4A / MOV video/audio container
        if len(header) >= 8 and header[4:8] == b"ftyp":
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded file for '{field_name}' is an MP4/M4A container, not an MP3 audio file. Only .mp3 is permitted.",
            )

        # Verify MP3 Header: Starts with ID3v2 tag or MPEG audio frame sync byte (0xFF)
        has_id3 = header.startswith(b"ID3") or b"ID3" in header[:64]
        has_mpeg_sync = len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0

        if not (has_id3 or has_mpeg_sync):
            raise HTTPException(
                status_code=400,
                detail=f"Uploaded file for '{field_name}' does not contain valid MP3 audio stream headers.",
            )

    @classmethod
    async def validate_audio_file(cls, file_obj: UploadFile, field_name: str) -> None:
        """Backward-compatible alias for validate_mp3_file."""
        await cls.validate_mp3_file(file_obj, field_name)
