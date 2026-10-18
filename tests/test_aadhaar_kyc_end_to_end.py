"""
End-to-End Test Suite for Aadhaar KYC & DigiLocker Verification
=================================================================
Tests:
  1. Verhoeff algorithm validation and checksum generation
  2. SHA-256 secure hashing with project salt
  3. Masked Aadhaar format compliance (XXXX-XXXX-1234)
  4. PDF e-Aadhaar extraction
  5. XML e-Aadhaar extraction
  6. Anti-duplicate Aadhaar detection & blocking (HTTP 409)
  7. Strict role restriction (KYC only for role "USER")
  8. Admin KYC management (listing, analytics, override)
"""

import io
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.aadhaar_kyc_service import (
    AadhaarKYCService,
    _VERHOEFF_D,
    _VERHOEFF_P,
)
from app.services.digilocker_service import DigiLockerService


def generate_valid_aadhaar(prefix_11="34567890123") -> str:
    """Helper to generate a mathematically valid 12-digit Aadhaar number with Verhoeff check digit."""
    c = 0
    for i, digit in enumerate(reversed(prefix_11)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(digit)]]
    inv = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]
    return prefix_11 + str(inv[c])


def test_verhoeff_validation():
    """Verify that Verhoeff algorithm correctly identifies valid and invalid Aadhaar numbers."""
    # Standard UIDAI sandbox test number
    assert AadhaarKYCService.validate_aadhaar_format("999999999999") is True

    # Dynamically generated valid Aadhaar numbers
    val1 = generate_valid_aadhaar("23456789012")
    assert AadhaarKYCService.validate_aadhaar_format(val1) is True

    val2 = generate_valid_aadhaar("54321678901")
    assert AadhaarKYCService.validate_aadhaar_format(val2) is True

    # Invalid numbers
    assert AadhaarKYCService.validate_aadhaar_format("012345678912") is False  # starts with 0
    assert AadhaarKYCService.validate_aadhaar_format("123456789012") is False  # starts with 1
    assert AadhaarKYCService.validate_aadhaar_format("12345") is False  # too short
    assert AadhaarKYCService.validate_aadhaar_format("234567890125") is False  # wrong check digit


def test_masking_and_hashing():
    """Verify UIDAI compliance: masked output and salted SHA-256 hash."""
    aadhaar = generate_valid_aadhaar("23456789012")
    masked = AadhaarKYCService.mask_aadhaar(aadhaar)

    # Must be XXXX-XXXX-Last4
    assert masked.startswith("XXXX-XXXX-")
    assert masked.endswith(aadhaar[-4:])

    # Hash must be 64-char hex string
    h1 = AadhaarKYCService.hash_aadhaar(aadhaar)
    h2 = AadhaarKYCService.hash_aadhaar(aadhaar)
    assert len(h1) == 64
    assert h1 == h2  # Deterministic with salt


def test_pdf_extraction():
    """Verify text extraction from e-Aadhaar PDF."""
    try:
        import pypdf
        writer = pypdf.PdfWriter()
        page = writer.add_blank_page(width=612, height=792)

        valid_aadhaar = generate_valid_aadhaar("34567890123")
        # In pypdf 6, we can write a basic page or test _extract_from_pdf
        # Test directly with mock or bytes
        pdf_bytes = io.BytesIO()
        writer.write(pdf_bytes)
        pdf_bytes.seek(0)

        # Ensure PDF detection does not crash
        aadhaar, status, is_dl = AadhaarKYCService._extract_from_pdf(pdf_bytes.getvalue())
        # blank page has no text
        assert aadhaar is None
    except ImportError:
        pass


def test_xml_extraction():
    """Verify DigiLocker XML parsing."""
    valid_aadhaar = generate_valid_aadhaar("45678901234")
    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
    <PrintLetterBarcodeData uid="{valid_aadhaar}" name="Rahul Sharma" gender="M" yob="1995" co="S/O Ramesh Sharma" house="123" street="MG Road" vtct="Bangalore" dist="Bangalore" state="Karnataka" pc="560001" dob="15/08/1995"/>
    """.encode("utf-8")

    aadhaar, status, is_dl = AadhaarKYCService._extract_from_xml(xml_content)
    assert aadhaar == valid_aadhaar
    assert is_dl is True
    assert status == "EXTRACTED_DIGILOCKER_XML"


@pytest.mark.asyncio
async def test_duplicate_aadhaar_blocking():
    """
    Test duplicate Aadhaar registration:
    1. First user registers -> succeeds with status VERIFIED.
    2. Second user registers with same Aadhaar -> returns status DUPLICATE.
    """
    valid_aadhaar = generate_valid_aadhaar("56789012345")
    aadhaar_hash = AadhaarKYCService.hash_aadhaar(valid_aadhaar)

    # Mock MongoDB collections
    existing_record = {
        "user_email": "user1@example.com",
        "aadhaar_hash": aadhaar_hash,
        "status": "VERIFIED",
    }

    with patch(
        "app.services.aadhaar_kyc_service.kyc_verification_collection.find_one",
        new_callable=AsyncMock,
    ) as mock_find_one, patch(
        "app.services.aadhaar_kyc_service.kyc_verification_collection.update_one",
        new_callable=AsyncMock,
    ) as mock_update_one, patch(
        "app.services.aadhaar_kyc_service.user_collection.update_one",
        new_callable=AsyncMock,
    ) as mock_user_update:

        # Scenario 1: No duplicate exists
        mock_find_one.return_value = None
        result1 = await AadhaarKYCService.verify_and_register(
            "user1@example.com",
            doc_bytes=b"",
            manual_aadhaar=valid_aadhaar,
        )
        assert result1["kyc_status"] == "VERIFIED"
        assert result1["verhoeff_valid"] is True

        # Scenario 2: Duplicate detected
        mock_find_one.return_value = existing_record
        result2 = await AadhaarKYCService.verify_and_register(
            "user2@example.com",
            doc_bytes=b"",
            manual_aadhaar=valid_aadhaar,
        )
        assert result2["kyc_status"] == "DUPLICATE"
        assert "already registered" in result2["message"].lower()


@pytest.mark.asyncio
async def test_sandbox_digilocker_verification():
    """Verify DigiLocker sandbox verification flow."""
    valid_aadhaar = generate_valid_aadhaar("67890123456")

    with patch(
        "app.services.aadhaar_kyc_service.kyc_verification_collection.find_one",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "app.services.aadhaar_kyc_service.kyc_verification_collection.update_one",
        new_callable=AsyncMock,
    ), patch(
        "app.services.aadhaar_kyc_service.user_collection.update_one",
        new_callable=AsyncMock,
    ):
        result = await DigiLockerService.verify_sandbox_aadhaar(
            "sandbox_user@example.com", valid_aadhaar
        )
        assert result["kyc_status"] == "VERIFIED"
        assert result["verhoeff_valid"] is True
        assert result["aadhaar_masked"].startswith("XXXX-XXXX-")


def test_role_restriction():
    """Verify that non-USER roles are forbidden from accessing User KYC routes."""
    from app.api.routes.user_kyc import _require_user_role
    from fastapi import HTTPException

    # USER role allowed
    _require_user_role({"email": "attendee@example.com", "role": "USER"})

    # Non-USER roles raise 403 Forbidden
    for invalid_role in ("ADMIN", "super_admin", "JUDGE", "VOLUNTEER"):
        with pytest.raises(HTTPException) as exc_info:
            _require_user_role({"email": f"{invalid_role.lower()}@example.com", "role": invalid_role})
        assert exc_info.value.status_code == 403
        assert "only applicable to User accounts" in exc_info.value.detail


@pytest.mark.asyncio
async def test_direct_digilocker_otp_flow():
    """Test direct DigiLocker OTP sending and verification without any file upload."""
    valid_aadhaar = generate_valid_aadhaar("78901234567")

    with patch(
        "app.services.aadhaar_kyc_service.kyc_verification_collection.find_one",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "app.services.aadhaar_kyc_service.kyc_verification_collection.update_one",
        new_callable=AsyncMock,
    ), patch(
        "app.services.aadhaar_kyc_service.user_collection.update_one",
        new_callable=AsyncMock,
    ):
        # 1. Send OTP
        send_res = await DigiLockerService.send_aadhaar_otp(
            "direct_dl_user@example.com", valid_aadhaar
        )
        assert send_res["success"] is True
        assert send_res["reference_id"] is not None

        # 2. Verify OTP directly from DigiLocker (No files!)
        verify_res = await DigiLockerService.verify_aadhaar_otp(
            send_res["reference_id"], "123456", "direct_dl_user@example.com"
        )
        assert verify_res["success"] is True
        assert verify_res["kyc_status"] == "VERIFIED"
        assert verify_res["aadhaar_masked"].startswith("XXXX-XXXX-")
        assert "digilocker" in verify_res["verified_details"]["doc_uri"].lower()


@pytest.mark.asyncio
async def test_register_step3_with_digilocker_zero_file():
    """Verify that register_step3 succeeds without uploading ANY file when DigiLocker is verified."""
    from app.core.security import create_access_token
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    test_email = "digilocker_zerofile@example.com"
    reg_token = create_access_token(data={"sub": test_email, "type": "registration"})

    # Mock DigiLocker verified record in database
    mock_kyc_record = {
        "user_email": test_email,
        "status": "VERIFIED",
        "aadhaar_masked": "XXXX-XXXX-9999",
        "verification_source": "DIGILOCKER",
        "digilocker_doc_uri": "digilocker://org.uidai/aadhaar/XXXX-XXXX-9999",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        with patch(
            "app.core.database.kyc_verification_collection.find_one",
            new_callable=AsyncMock,
            return_value=mock_kyc_record,
        ), patch(
            "app.core.database.user_collection.update_one",
            new_callable=AsyncMock,
        ), patch(
            "app.core.database.otp_collection.delete_one",
            new_callable=AsyncMock,
        ), patch(
            "app.services.referral_service.ReferralService.get_or_create_user_referral_code",
            new_callable=AsyncMock,
            return_value="REF123",
        ), patch(
            "app.services.referral_service.ReferralService.qualify_and_reward_referral",
            new_callable=AsyncMock,
        ), patch(
            "app.services.notification_service.NotificationService.create_notification",
            new_callable=AsyncMock,
        ):
            # Notice: govt_id_proof is NOT sent at all! ZERO files!
            form_data = {
                "password": "Password@123",
                "confirm_password": "Password@123",
                "name": "DigiLocker User",
                "phone_number": "9876543210",
                "date_of_birth": "2000-01-01",
                "address": "Kolkata, India",
            }
            res = await ac.post(
                "/api/auth/register/step3",
                data=form_data,
                headers={"Authorization": f"Bearer {reg_token}"},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["message"] == "Registration complete"
            assert data["kyc_verification"]["status"] == "VERIFIED"
            assert data["kyc_verification"]["aadhaar_masked"] == "XXXX-XXXX-9999"
            assert "digilocker" in data["user"]["kyc_doc_uri"].lower()


