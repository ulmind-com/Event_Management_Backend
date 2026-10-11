from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from typing import Optional, List

from app.api.deps import get_current_user
from app.core.database import certificate_collection, user_badge_award_collection
from app.services.certificate_pdf_service import CertificatePDFService

router = APIRouter()

@router.get('/certificates')
async def get_my_certificates(
    cert_type: Optional[str] = None,
    activity_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
    skip: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """Retrieves all certificates owned by the authenticated user."""
    email = current_user.get('email')
    query = {'user_email': email}
    if cert_type:
        query['certificate_type'] = cert_type
    if activity_id:
        query['activity_id'] = activity_id

    total = await certificate_collection.count_documents(query)
    certs = await certificate_collection.find(query).sort('issued_at', -1).skip(skip).limit(limit).to_list(length=limit)
    for c in certs:
        c['id'] = str(c.pop('_id'))

    return {
        'total': total,
        'skip': skip,
        'limit': limit,
        'certificates': certs
    }

@router.get('/certificates/{certificate_id}')
async def get_my_certificate_details(
    certificate_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Retrieves a single certificate with strict ownership authorization."""
    email = current_user.get('email')
    cert = await certificate_collection.find_one({'certificate_id': certificate_id, 'user_email': email})
    if not cert:
        raise HTTPException(status_code=404, detail='Certificate not found or unauthorized')

    cert['id'] = str(cert.pop('_id'))
    return cert

@router.get('/certificates/{certificate_id}/download')
async def download_my_certificate_pdf(
    certificate_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Downloads the official PDF certificate with QR code for the authenticated owner."""
    email = current_user.get('email')
    cert = await certificate_collection.find_one({'certificate_id': certificate_id, 'user_email': email})
    if not cert:
        raise HTTPException(status_code=404, detail='Certificate not found or unauthorized')

    issued_dt = cert.get('issued_at')
    issue_date_str = issued_dt.strftime('%Y-%m-%d') if issued_dt else 'N/A'

    pdf_bytes = CertificatePDFService.generate_certificate_pdf(
        certificate_id=cert.get('certificate_id'),
        certificate_type=cert.get('certificate_type'),
        participant_name=cert.get('participant_name', current_user.get('name', 'Participant')),
        activity_title=cert.get('activity_title', 'Activity'),
        activity_type=cert.get('activity_type', 'COMPETITION'),
        rank=cert.get('rank'),
        award_title=cert.get('award_title'),
        issue_date_str=issue_date_str,
        verification_url=cert.get('verification_url')
    )

    return Response(
        content=pdf_bytes,
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{certificate_id}.pdf"'}
    )

@router.get('/certificates/{certificate_id}/share')
async def get_certificate_share_metadata(
    certificate_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Generates social sharing metadata and preview link for the certificate."""
    email = current_user.get('email')
    cert = await certificate_collection.find_one({'certificate_id': certificate_id, 'user_email': email})
    if not cert:
        raise HTTPException(status_code=404, detail='Certificate not found or unauthorized')

    token = cert.get('verification_token')
    v_url = f'https://eventora.com/verify-certificate/{token}'
    display_type = cert.get('certificate_type', 'PARTICIPATION').replace('_', ' ').title()
    title = f'Earned {display_type} Certificate in {cert.get("activity_title")}'

    return {
        'certificate_id': certificate_id,
        'share_title': title,
        'description': f'Proud to receive this official certificate from Eventora for outstanding achievement in {cert.get("activity_title")}.',
        'verification_url': v_url,
        'badge_preview_url': 'https://eventora.com/static/cert-preview.png'
    }

@router.get('/badges')
async def get_my_badges(
    current_user: dict = Depends(get_current_user)
):
    """Retrieves all earned badges for the authenticated user."""
    email = current_user.get('email')
    badges = await user_badge_award_collection.find({'user_email': email}).to_list(length=100)
    for b in badges:
        b['id'] = str(b.pop('_id'))
    return {'badges': badges, 'total': len(badges)}
