import secrets
from datetime import datetime, timezone
from bson import ObjectId
from typing import Dict, Any, List, Optional
import logging

from app.core.database import (
    user_collection,
    competition_collection,
    competition_submission_collection,
    issued_ticket_collection,
    registration_collection,
    badge_definition_collection,
    badge_policy_collection,
    user_badge_award_collection,
)

logger = logging.getLogger(__name__)

class BadgeService:
    DEFAULT_BADGE_ID = 'TOP_PARTICIPATION'
    
    @staticmethod
    async def get_badge_policy() -> Dict[str, Any]:
        policy = await badge_policy_collection.find_one({'badge_id': BadgeService.DEFAULT_BADGE_ID})
        if not policy:
            return {
                'badge_id': BadgeService.DEFAULT_BADGE_ID,
                'badge_name': 'Top Participation',
                'description': 'Recognizes participants who complete multiple verified Eventora competitions or events.',
                'category': 'MILESTONE',
                'image_url': '/badges/top-participation.svg',
                'enabled': True,
                'qualification_mode': 'THRESHOLD',
                'minimum_qualifying_participations': 5, # Configurable, default 5
                'count_distinct_activities': True,
                'require_completed_activity': True,
                'policy_version': '1.0'
            }
        policy.pop('_id', None)
        return policy

    @staticmethod
    async def save_badge_policy(policy_dict: Dict[str, Any]) -> Dict[str, Any]:
        policy_dict['badge_id'] = BadgeService.DEFAULT_BADGE_ID
        policy_dict['updated_at'] = datetime.now(timezone.utc)
        await badge_policy_collection.update_one(
            {'badge_id': BadgeService.DEFAULT_BADGE_ID},
            {'$set': policy_dict},
            upsert=True
        )
        saved = await badge_policy_collection.find_one({'badge_id': BadgeService.DEFAULT_BADGE_ID})
        saved.pop('_id', None)
        return saved

    @staticmethod
    async def get_user_qualifying_activities(user_email: str) -> Dict[str, Any]:
        policy = await BadgeService.get_badge_policy()
        require_completed = policy.get('require_completed_activity', True)

        distinct_activities = set()

        # 1. Competitions: Valid submission
        subs = await competition_submission_collection.find({
            'user_email': user_email,
            'status': 'SUBMITTED'
        }).to_list(length=500)

        for s in subs:
            comp_id = s.get('competition_id')
            if require_completed:
                try:
                    c_oid = ObjectId(comp_id)
                    comp = await competition_collection.find_one({'_id': c_oid})
                except Exception:
                    comp = await competition_collection.find_one({'_id': comp_id})
                
                # Must have published results
                if comp and comp.get('results_published'):
                    distinct_activities.add(f'COMPETITION_{comp_id}')
            else:
                distinct_activities.add(f'COMPETITION_{comp_id}')

        # 2. Events: Ticket was checked-in (actual participation)
        tickets = await issued_ticket_collection.find({
            'holder_email': user_email,
            'is_checked_in': True
        }).to_list(length=500)

        for t in tickets:
            event_id = t.get('event_id') or t.get('booking_id')
            if event_id:
                distinct_activities.add(f'EVENT_{event_id}')

        # 3. Courses / Workshops: Confirmed registration
        regs = await registration_collection.find({
            'user_email': user_email,
            'status': 'CONFIRMED'
        }).to_list(length=500)

        for r in regs:
            item_type = r.get('type', 'ACTIVITY')
            item_id = r.get('item_id')
            if item_id:
                distinct_activities.add(f'{item_type}_{item_id}')

        return {
            'qualifying_count': len(distinct_activities),
            'qualifying_activities': list(distinct_activities)
        }

    @staticmethod
    async def evaluate_user_badge_eligibility(user_email: str) -> Dict[str, Any]:
        policy = await BadgeService.get_badge_policy()
        if not policy.get('enabled', True):
            return {'awarded': False, 'reason': 'Badge program disabled.'}

        # Check existing active award
        existing = await user_badge_award_collection.find_one({
            'user_email': user_email,
            'badge_id': BadgeService.DEFAULT_BADGE_ID,
            'status': 'ACTIVE'
        })
        if existing:
            return {
                'awarded': False,
                'already_awarded': True,
                'award_id': str(existing['_id']),
                'message': 'User already holds this badge.'
            }

        activity_info = await BadgeService.get_user_qualifying_activities(user_email)
        count = activity_info['qualifying_count']
        threshold = policy.get('minimum_qualifying_participations', 5)

        if count >= threshold:
            award_doc = {
                'badge_id': BadgeService.DEFAULT_BADGE_ID,
                'badge_name': policy.get('badge_name', 'Top Participation'),
                'description': policy.get('description', ''),
                'category': policy.get('category', 'MILESTONE'),
                'image_url': policy.get('image_url', '/badges/top-participation.svg'),
                'user_email': user_email,
                'awarded_at': datetime.now(timezone.utc),
                'status': 'ACTIVE',
                'qualification_criteria': f'Completed at least {threshold} distinct qualifying activities.',
                'qualification_count': count,
                'qualifying_activities': activity_info['qualifying_activities'],
                'policy_version': policy.get('policy_version', '1.0'),
            }
            res = await user_badge_award_collection.insert_one(award_doc)
            return {
                'awarded': True,
                'award_id': str(res.inserted_id),
                'badge_name': award_doc['badge_name'],
                'qualification_count': count
            }

        return {
            'awarded': False,
            'current_count': count,
            'threshold': threshold,
            'message': f'Threshold not yet reached ({count}/{threshold}).'
        }

    @staticmethod
    async def reconcile_all_badges() -> Dict[str, Any]:
        users = await user_collection.find({}).to_list(length=5000)
        evaluated = 0
        newly_awarded = 0
        already_awarded = 0

        for u in users:
            email = u.get('email')
            if not email:
                continue
            evaluated += 1
            res = await BadgeService.evaluate_user_badge_eligibility(email)
            if res.get('awarded'):
                newly_awarded += 1
            elif res.get('already_awarded'):
                already_awarded += 1

        return {
            'evaluated_users': evaluated,
            'newly_awarded': newly_awarded,
            'already_awarded': already_awarded
        }

    @staticmethod
    async def revoke_badge_award(award_id: str, reason: str, admin_email: str) -> Dict[str, Any]:
        try:
            a_oid = ObjectId(award_id)
            award = await user_badge_award_collection.find_one({'_id': a_oid})
        except Exception:
            award = await user_badge_award_collection.find_one({'_id': award_id})

        if not award:
            return {'success': False, 'message': 'Badge award record not found.'}

        now = datetime.now(timezone.utc)
        await user_badge_award_collection.update_one(
            {'_id': award['_id']},
            {'$set': {
                'status': 'REVOKED',
                'revocation_reason': reason,
                'revoked_by': admin_email,
                'revoked_at': now
            }}
        )
        return {
            'success': True,
            'message': 'Badge award revoked successfully.',
            'status': 'REVOKED',
            'revoked_at': now
        }
