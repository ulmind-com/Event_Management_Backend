from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import Response
from typing import Optional, Dict, Any
from bson import ObjectId

from app.core.admin_auth import get_current_admin
from app.core.database import certificate_collection, competition_collection
from app.schemas.certificate import AwardConfigReq, CertificateGenerateReq, CertificateRevokeReq
from app.services.certificate_service import CertificateService
from app.services.certificate_pdf_service import CertificatePDFService

router = APIRouter()

@router.get('/competitions/{comp_id}/certificate-eligibility')
async def get_competition_certificate_eligibility(
    comp_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Inspects eligible and ineligible participants for certificate issuance."""
    res = await CertificateService.get_certificate_eligibility(comp_id)
    return res

@router.get('/competitions/{comp_id}/award-config')
async def get_award_config(
    comp_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Retrieves dynamic Top N award configuration for a competition."""
    return await CertificateService.get_award_config(comp_id)

@router.put('/competitions/{comp_id}/award-config')
async def update_award_config(
    comp_id: str,
    req: AwardConfigReq,
    admin: dict = Depends(get_current_admin)
):
    """Updates dynamic Top N award configuration for a competition."""
    return await CertificateService.save_award_config(comp_id, req.model_dump())

@router.post('/competitions/{comp_id}/certificates/generate')
async def generate_certificates(
    comp_id: str,
    req: CertificateGenerateReq = CertificateGenerateReq(),
    admin: dict = Depends(get_current_admin)
):
    """
    Generates Participation Certificates and Top-Ranked Award Certificates.
    Sends PDF notifications if configured. Idempotent.
    """
    admin_email = admin.get('email', 'superadmin@eventora.com')
    res = await CertificateService.generate_competition_certificates(
        competition_id=comp_id,
        admin_email=admin_email,
        notify_participants=req.notify_participants
    )
    if not res.get('success'):
        raise HTTPException(status_code=400, detail=res.get('message'))
    return res

@router.get('/competitions/{comp_id}/certificates')
async def list_competition_certificates(
    comp_id: str,
    cert_type: Optional[str] = None,
    admin: dict = Depends(get_current_admin)
):
    """Lists all certificates issued for a specific competition."""
    query = {'activity_id': comp_id}
    if cert_type:
        query['certificate_type'] = cert_type
    certs = await certificate_collection.find(query).to_list(length=1000)
    for c in certs:
        c['id'] = str(c.pop('_id'))
    return {'certificates': certs, 'total': len(certs)}

@router.get('/certificates')
async def list_all_certificates(
    search: Optional[str] = None,
    cert_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
    admin: dict = Depends(get_current_admin)
):
    """Paginated list of all certificates across the platform."""
    query = {}
    if cert_type:
        query['certificate_type'] = cert_type
    if status:
        query['status'] = status
    if search:
        query['$or'] = [
            {'user_email': {'$regex': search, '$options': 'i'}},
            {'participant_name': {'$regex': search, '$options': 'i'}},
            {'certificate_id': {'$regex': search, '$options': 'i'}},
            {'activity_title': {'$regex': search, '$options': 'i'}},
        ]

    total = await certificate_collection.count_documents(query)
    certs = await certificate_collection.find(query).skip(skip).limit(limit).to_list(length=limit)
    for c in certs:
        c['id'] = str(c.pop('_id'))

    return {
        'total': total,
        'skip': skip,
        'limit': limit,
        'certificates': certs
    }

@router.get('/certificates/{certificate_id}')
async def get_certificate_details(
    certificate_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Retrieves complete details of a specific certificate."""
    cert = await certificate_collection.find_one({'certificate_id': certificate_id})
    if not cert:
        raise HTTPException(status_code=404, detail='Certificate not found')
    cert['id'] = str(cert.pop('_id'))
    return cert

@router.get('/certificates/{certificate_id}/download')
async def download_certificate_pdf_admin(
    certificate_id: str,
    admin: dict = Depends(get_current_admin)
):
    """Downloads the certificate PDF as admin."""
    cert = await certificate_collection.find_one({'certificate_id': certificate_id})
    if not cert:
        raise HTTPException(status_code=404, detail='Certificate not found')

    issued_dt = cert.get('issued_at')
    issue_date_str = issued_dt.strftime('%Y-%m-%d') if issued_dt else 'N/A'

    pdf_bytes = CertificatePDFService.generate_certificate_pdf(
        certificate_id=cert.get('certificate_id'),
        certificate_type=cert.get('certificate_type'),
        participant_name=cert.get('participant_name', 'Participant'),
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
        headers={'Content-Disposition': f'attachment; filename=\"{certificate_id}.pdf\"'}
    )

@router.post('/certificates/{certificate_id}/revoke')
async def revoke_certificate(
    certificate_id: str,
    req: CertificateRevokeReq,
    admin: dict = Depends(get_current_admin)
):
    """Revokes a certificate with reason and admin audit logging."""
    admin_email = admin.get('email', 'superadmin@eventora.com')
    res = await CertificateService.revoke_certificate(certificate_id, req.reason, admin_email)
    if not res.get('success'):
        raise HTTPException(status_code=404, detail=res.get('message'))
    return res

@router.get('/certificates/analytics/overview')
async def get_certificate_analytics(
    admin: dict = Depends(get_current_admin)
):
    """Returns analytics and distribution across all certificates."""
    total = await certificate_collection.count_documents({})
    valid = await certificate_collection.count_documents({'status': 'VALID'})
    revoked = await certificate_collection.count_documents({'status': 'REVOKED'})
    participation = await certificate_collection.count_documents({'certificate_type': 'PARTICIPATION'})
    award = await certificate_collection.count_documents({'certificate_type': 'AWARD'})
    delivered = await certificate_collection.count_documents({'delivery_status': 'DELIVERED'})

    return {
        'total_certificates': total,
        'valid_certificates': valid,
        'revoked_certificates': revoked,
        'participation_certificates': participation,
        'award_certificates': award,
        'delivered_certificates': delivered
    }
