import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from fastapi import HTTPException
from bson import ObjectId

from app.main import app
from app.core.config import settings
from app.services.cloudinary_srv import is_valid_cloudinary_url, upload_image
from app.services.audio_validator import AudioValidator
from app.schemas.cms import BannerCreate
from app.schemas.merchandise import ProductCreateReq
from app.schemas.competition import CompetitionCreateReq


def test_is_valid_cloudinary_url_unit():
    """Unit test checking strict validation of genuine vs fake Cloudinary URLs."""
    cloud = settings.CLOUDINARY_CLOUD_NAME or "mkbilzxz"

    # Genuine URLs
    assert is_valid_cloudinary_url(f"https://res.cloudinary.com/{cloud}/image/upload/v1792173363/eventora/profiles/test.jpg") is True
    assert is_valid_cloudinary_url(f"https://res.cloudinary.com/{cloud}/raw/upload/v1791568025/eventora/gov_ids/test.pdf") is True
    assert is_valid_cloudinary_url(f"https://res.cloudinary.com/{cloud}/video/upload/v1792502062/eventora/submissions/audio.mp3") is True

    # Fake / Demo URLs
    assert is_valid_cloudinary_url("https://res.cloudinary.com/demo/image/upload/sample.jpg") is False
    assert is_valid_cloudinary_url("https://res.cloudinary.com/demo/video/upload/eventora_test.mp3") is False
    assert is_valid_cloudinary_url("https://res.cloudinary.com/fake_cloud/image/upload/sample.jpg") is False
    assert is_valid_cloudinary_url("https://res.cloudinary.com/othercloud/image/upload/sample.jpg") is False

    # Non-Cloudinary or malformed URLs
    assert is_valid_cloudinary_url("https://example.com/banner.jpg") is False
    assert is_valid_cloudinary_url("https://via.placeholder.com/300") is False
    assert is_valid_cloudinary_url("") is False
    assert is_valid_cloudinary_url(None) is False


def test_upload_image_rejects_fake_response():
    """Verify upload_image rejects any fake or /demo/ response returned from uploaders."""
    # Mock cloudinary upload returning fake demo URL
    with patch("cloudinary.uploader.upload", return_value={"secure_url": "https://res.cloudinary.com/demo/image/upload/fake.jpg", "resource_type": "image"}):
        result = upload_image(b"dummy image bytes", folder="test")
        assert result is None, "upload_image must reject fake /demo/ URL returned by uploader"

    # Mock cloudinary upload returning cloud name mismatch
    with patch("cloudinary.uploader.upload", return_value={"secure_url": "https://res.cloudinary.com/attacker_cloud/image/upload/test.jpg", "resource_type": "image"}):
        result = upload_image(b"dummy image bytes", folder="test")
        assert result is None, "upload_image must reject mismatched cloud name"


@pytest.mark.asyncio
async def test_audio_validator_rejects_fake_demo_link():
    """Verify that AudioValidator rejects text link files containing fake demo Cloudinary URLs."""
    from fastapi import UploadFile
    import io

    fake_link_bytes = b"https://res.cloudinary.com/demo/video/upload/v1234/test.mp3"
    file_obj = UploadFile(
        filename="audition_link.txt",
        file=io.BytesIO(fake_link_bytes),
        headers={"content-type": "text/plain"}
    )

    with pytest.raises(HTTPException) as exc_info:
        await AudioValidator.validate_and_extract_link(file_obj, "Audition Audio")
    assert exc_info.value.status_code == 400
    assert "fake or demo" in exc_info.value.detail.lower()


def test_schema_level_rejection_of_fake_urls():
    """Verify that Pydantic schemas reject fake /demo/ Cloudinary URLs upon creation."""
    # 1. Merchandise images validation
    with pytest.raises(ValueError, match="Fake demo Cloudinary URLs are strictly prohibited"):
        ProductCreateReq(
            name="Test T-Shirt",
            price=499.0,
            images=["https://res.cloudinary.com/demo/image/upload/sample.jpg"]
        )

    # 2. CMS Banner validation
    with pytest.raises(ValueError, match="Fake demo Cloudinary URLs are strictly prohibited"):
        BannerCreate(
            title="Summer Sale",
            image_url="https://res.cloudinary.com/demo/image/upload/banner.png"
        )
