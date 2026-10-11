from fastapi import APIRouter, HTTPException
from app.services.certificate_service import CertificateService

router = APIRouter()

@router.get('/verify/{verification_token}')
async def verify_certificate_public(verification_token: str):
    """
    Publicly verifies certificate validity using the secure verification token or QR code.
    Exposes only minimal, non-sensitive verification details.
    """
    return await CertificateService.get_public_verification(verification_token)
