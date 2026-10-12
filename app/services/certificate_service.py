import secrets
from datetime import datetime, timezone
from bson import ObjectId
from typing import Dict, Any, List, Optional
import logging

from app.core.database import (
    competition_collection,
    competition_registration_collection,
    competition_submission_collection,
    leaderboard_result_collection,
    certificate_collection,
    award_certificate_config_collection,
    user_collection,
)
from app.services.certificate_pdf_service import CertificatePDFService
from app.services.email import EmailService

logger = logging.getLogger(__name__)

class CertificateService:
    @staticmethod
    def generate_certificate_id(activity_type: str = 'COMPETITION') -> str:
        year = datetime.now(timezone.utc).year
        suffix = secrets.token_hex(4).upper()
        return f'EVT-CERT-{year}-{suffix}'

    @staticmethod
    def generate_verification_token() -> str:
        return secrets.token_urlsafe(24)

    @staticmethod
    async def get_award_config(competition_id: str) -> Dict[str, Any]:
        cfg = await award_certificate_config_collection.find_one({'competition_id': competition_id})
        if not cfg:
            return {
                'competition_id': competition_id,
                'enabled': True,
                'top_rank_limit': 3,
                'tie_handling': 'OFFICIAL_RANKING',
                'award_title_template': 'Rank {rank} Winner',
                'custom_titles': {
                    '1': 'Champion',
                    '2': '1st Runner Up',
                    '3': '2nd Runner Up',
                }
            }
        cfg.pop('_id', None)
        return cfg

    @staticmethod
    async def save_award_config(competition_id: str, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        config_dict['competition_id'] = competition_id
        config_dict['updated_at'] = datetime.now(timezone.utc)
        await award_certificate_config_collection.update_one(
            {'competition_id': competition_id},
            {'$set': config_dict},
            upsert=True
        )
        saved = await award_certificate_config_collection.find_one({'competition_id': competition_id})
        saved.pop('_id', None)
        return saved

    @staticmethod
    async def get_certificate_eligibility(competition_id: str) -> Dict[str, Any]:
        try:
            c_oid = ObjectId(competition_id)
            comp = await competition_collection.find_one({'_id': c_oid})
        except Exception:
            comp = await competition_collection.find_one({'_id': competition_id})

        if not comp:
            return {'eligible': False, 'status': 'NOT_FOUND', 'reason': 'Competition not found.'}

        results_published = comp.get('results_published', False)
        if not results_published:
            return {
                'competition_id': competition_id,
                'competition_title': comp.get('title', 'Unknown'),
                'is_completed': False,
                'results_published': False,
                'eligible_count': 0,
                'eligible_participants': [],
                'ineligible_participants': [],
                'reason': 'Official results are not yet published for this competition.'
            }

        submissions = await competition_submission_collection.find({
            'competition_id': competition_id,
            'status': 'SUBMITTED'
        }).to_list(length=1000)

        # Leaderboard entries for ranks
        leaderboard = await leaderboard_result_collection.find({
            'competition_id': competition_id
        }).to_list(length=1000)

        sub_rank_map = {}
        for lb in leaderboard:
            sub_id_str = str(lb.get('submission_id'))
            sub_rank_map[sub_id_str] = lb.get('rank', 0)

        existing_certs = await certificate_collection.find({
            'activity_id': competition_id,
            'activity_type': 'COMPETITION'
        }).to_list(length=2000)

        cert_lookup = {}
        for c in existing_certs:
            key = f"{c.get('user_email')}_{c.get('certificate_type')}"
            cert_lookup[key] = c

        award_cfg = await CertificateService.get_award_config(competition_id)
        top_limit = award_cfg.get('top_rank_limit', 3)

        eligible_participants = []
        ineligible_participants = []

        # Find registered users who didn't submit
        registrations = await competition_registration_collection.find({
            'competition_id': competition_id
        }).to_list(length=1000)

        submitted_emails = set()

        for sub in submissions:
            user_email = sub.get('user_email')
            submitted_emails.add(user_email)
            sub_id = str(sub.get('_id'))

            # Check user record for display name
            user = await user_collection.find_one({'email': user_email})
            participant_name = user.get('name') if user else user_email.split('@')[0]

            rank = sub_rank_map.get(sub_id, 0)
            has_part = f"{user_email}_PARTICIPATION" in cert_lookup
            has_award = f"{user_email}_AWARD" in cert_lookup

            is_top_rank = (rank > 0 and rank <= top_limit)

            eligible_participants.append({
                'user_email': user_email,
                'participant_name': participant_name,
                'submission_id': sub_id,
                'rank': rank,
                'is_top_rank': is_top_rank,
                'has_participation_certificate': has_part,
                'has_award_certificate': has_award,
                'eligibility_reason': 'Valid submission submitted and evaluated.'
            })

        for reg in registrations:
            reg_email = reg.get('user_email')
            if reg_email not in submitted_emails:
                ineligible_participants.append({
                    'user_email': reg_email,
                    'reason': 'Registered but no valid submission found.'
                })

        return {
            'competition_id': competition_id,
            'competition_title': comp.get('title', 'Unknown'),
            'is_completed': True,
            'results_published': True,
            'award_config': award_cfg,
            'eligible_count': len(eligible_participants),
            'eligible_participants': eligible_participants,
            'ineligible_participants': ineligible_participants
        }

    @staticmethod
    async def generate_competition_certificates(
        competition_id: str,
        admin_email: str,
        notify_participants: bool = True
    ) -> Dict[str, Any]:
        eligibility = await CertificateService.get_certificate_eligibility(competition_id)
        if not eligibility.get('results_published'):
            return {
                'success': False,
                'message': eligibility.get('reason', 'Results not published yet.')
            }

        award_cfg = eligibility.get('award_config', {})
        top_limit = award_cfg.get('top_rank_limit', 3)
        tie_handling = award_cfg.get('tie_handling', 'OFFICIAL_RANKING')
        custom_titles = award_cfg.get('custom_titles', {})
        comp_title = eligibility.get('competition_title', 'Competition')

        now = datetime.now(timezone.utc)
        now_str = now.strftime('%Y-%m-%d')

        part_created = 0
        award_created = 0
        already_existing = 0

        for p in eligibility.get('eligible_participants', []):
            user_email = p['user_email']
            user_name = p['participant_name']
            rank = p.get('rank', 0)

            # 1. PARTICIPATION CERTIFICATE (Idempotent)
            existing_part = await certificate_collection.find_one({
                'user_email': user_email,
                'activity_id': competition_id,
                'certificate_type': 'PARTICIPATION'
            })

            if not existing_part:
                cert_id = CertificateService.generate_certificate_id('COMPETITION')
                token = CertificateService.generate_verification_token()
                v_url = f'https://eventora.com/verify-certificate/{token}'

                pdf_bytes = CertificatePDFService.generate_certificate_pdf(
                    certificate_id=cert_id,
                    certificate_type='PARTICIPATION',
                    participant_name=user_name,
                    activity_title=comp_title,
                    issue_date_str=now_str,
                    verification_url=v_url
                )

                doc = {
                    'certificate_id': cert_id,
                    'verification_token': token,
                    'certificate_type': 'PARTICIPATION',
                    'user_email': user_email,
                    'email': user_email,
                    'participant_name': user_name,
                    'activity_type': 'COMPETITION',
                    'activity_id': competition_id,
                    'activity_title': comp_title,
                    'rank': None,
                    'award_title': None,
                    'status': 'VALID',
                    'delivery_status': 'GENERATED',
                    'template_version': '1.0',
                    'issued_at': now,
                    'issued_by': admin_email,
                    'verification_url': v_url
                }

                if notify_participants:
                    sent = EmailService.send_certificate_email(
                        email=user_email,
                        name=user_name,
                        activity_title=comp_title,
                        cert_type='PARTICIPATION',
                        verification_url=v_url,
                        pdf_bytes=pdf_bytes
                    )
                    doc['delivery_status'] = 'DELIVERED' if sent else 'DELIVERY_FAILED'

                await certificate_collection.insert_one(doc)
                part_created += 1

                # Authoritative trigger: CERTIFICATE_AVAILABLE
                try:
                    from app.services.notification_service import NotificationService
                    from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
                    from app.schemas.notification import NotificationCreate

                    await NotificationService.create_notification(NotificationCreate(
                        recipient_user_id=user_email,
                        notification_type=NotificationType.CERTIFICATE_AVAILABLE,
                        title="Certificate Available",
                        message=f"Your Participation Certificate for '{comp_title}' is ready to download.",
                        resource_type="certificate",
                        resource_id=cert_id,
                        action_url=f"/profile/certificates/{cert_id}",
                        priority=NotificationPriority.HIGH,
                        sound_category=SoundCategory.SUCCESS,
                        deduplication_key=f"cert_avail_{cert_id}",
                        source_module="certificates",
                        source_event_id=competition_id,
                    ))
                except Exception:
                    pass
            else:
                already_existing += 1

            # 2. AWARD CERTIFICATE (If Top-Ranked)
            is_eligible_for_award = False
            if rank and rank > 0:
                if tie_handling == 'STRICT_LIMIT':
                    is_eligible_for_award = (rank <= top_limit)
                elif tie_handling == 'INCLUDE_ALL_TIES':
                    is_eligible_for_award = (rank <= top_limit)
                else: # OFFICIAL_RANKING
                    is_eligible_for_award = (rank <= top_limit)

            if is_eligible_for_award:
                existing_award = await certificate_collection.find_one({
                    'user_email': user_email,
                    'activity_id': competition_id,
                    'certificate_type': 'AWARD'
                })

                if not existing_award:
                    award_cert_id = CertificateService.generate_certificate_id('COMPETITION')
                    award_token = CertificateService.generate_verification_token()
                    award_v_url = f'https://eventora.com/verify-certificate/{award_token}'

                    award_title = custom_titles.get(str(rank), f'Rank {rank} Winner')

                    award_pdf_bytes = CertificatePDFService.generate_certificate_pdf(
                        certificate_id=award_cert_id,
                        certificate_type='AWARD',
                        participant_name=user_name,
                        activity_title=comp_title,
                        rank=rank,
                        award_title=award_title,
                        issue_date_str=now_str,
                        verification_url=award_v_url
                    )

                    award_doc = {
                        'certificate_id': award_cert_id,
                        'verification_token': award_token,
                        'certificate_type': 'AWARD',
                        'user_email': user_email,
                        'email': user_email,
                        'participant_name': user_name,
                        'activity_type': 'COMPETITION',
                        'activity_id': competition_id,
                        'activity_title': comp_title,
                        'rank': rank,
                        'award_title': award_title,
                        'status': 'VALID',
                        'delivery_status': 'GENERATED',
                        'template_version': '1.0',
                        'issued_at': now,
                        'issued_by': admin_email,
                        'verification_url': award_v_url
                    }

                    if notify_participants:
                        sent = EmailService.send_certificate_email(
                            email=user_email,
                            name=user_name,
                            activity_title=comp_title,
                            cert_type='AWARD',
                            rank=rank,
                            award_title=award_title,
                            verification_url=award_v_url,
                            pdf_bytes=award_pdf_bytes
                        )
                        award_doc['delivery_status'] = 'DELIVERED' if sent else 'DELIVERY_FAILED'

                    await certificate_collection.insert_one(award_doc)
                    award_created += 1

                    # Authoritative trigger: CERTIFICATE_AVAILABLE (Award)
                    try:
                        from app.services.notification_service import NotificationService
                        from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
                        from app.schemas.notification import NotificationCreate

                        await NotificationService.create_notification(NotificationCreate(
                            recipient_user_id=user_email,
                            notification_type=NotificationType.CERTIFICATE_AVAILABLE,
                            title="Award Certificate Available!",
                            message=f"Congratulations! Your {award_title} certificate for '{comp_title}' is ready to download.",
                            resource_type="certificate",
                            resource_id=award_cert_id,
                            action_url=f"/profile/certificates/{award_cert_id}",
                            priority=NotificationPriority.HIGH,
                            sound_category=SoundCategory.SUCCESS,
                            deduplication_key=f"cert_avail_{award_cert_id}",
                            source_module="certificates",
                            source_event_id=competition_id,
                        ))
                    except Exception:
                        pass
                else:
                    already_existing += 1

            # Trigger Badge evaluation for this user
            from app.services.badge_service import BadgeService
            await BadgeService.evaluate_user_badge_eligibility(user_email)

        return {
            'success': True,
            'message': 'Certificates generated successfully.',
            'competition_id': competition_id,
            'participation_certificates_created': part_created,
            'award_certificates_created': award_created,
            'already_existing_certificates_skipped': already_existing,
            'total_issued_in_this_run': part_created + award_created
        }

    @staticmethod
    async def revoke_certificate(certificate_id: str, reason: str, admin_email: str) -> Dict[str, Any]:
        cert = await certificate_collection.find_one({'certificate_id': certificate_id})
        if not cert:
            return {'success': False, 'message': 'Certificate not found.'}

        now = datetime.now(timezone.utc)
        await certificate_collection.update_one(
            {'certificate_id': certificate_id},
            {'$set': {
                'status': 'REVOKED',
                'revocation_reason': reason,
                'revoked_by': admin_email,
                'revoked_at': now
            }}
        )
        return {
            'success': True,
            'message': 'Certificate revoked successfully.',
            'certificate_id': certificate_id,
            'status': 'REVOKED',
            'revoked_at': now
        }

    @staticmethod
    async def get_public_verification(verification_token: str) -> Dict[str, Any]:
        cert = await certificate_collection.find_one({'verification_token': verification_token})
        if not cert:
            return {
                'verification_status': 'NOT_FOUND',
                'message': 'Invalid verification token. Certificate does not exist.'
            }

        status = cert.get('status', 'VALID')
        if status == 'REVOKED':
            return {
                'verification_status': 'REVOKED',
                'certificate_id': cert.get('certificate_id'),
                'certificate_type': cert.get('certificate_type'),
                'activity_title': cert.get('activity_title'),
                'status': 'REVOKED',
                'revocation_reason': cert.get('revocation_reason'),
                'message': 'This certificate has been officially revoked by the Super Admin.'
            }

        issued_dt = cert.get('issued_at')
        issue_date_str = issued_dt.strftime('%Y-%m-%d') if issued_dt else 'N/A'

        return {
            'verification_status': 'VALID',
            'certificate_id': cert.get('certificate_id'),
            'certificate_type': cert.get('certificate_type'),
            'participant_name': cert.get('participant_name'),
            'activity_title': cert.get('activity_title'),
            'activity_type': cert.get('activity_type'),
            'rank': cert.get('rank'),
            'award_title': cert.get('award_title'),
            'issue_date': issue_date_str,
            'status': 'VALID'
        }
