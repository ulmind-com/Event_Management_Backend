from datetime import datetime, timezone
from bson import ObjectId
from typing import Dict, Any, List, Optional
import logging

from app.core.database import (
    user_collection,
    booking_collection,
    issued_ticket_collection,
    event_collection,
    competition_collection,
    competition_registration_collection,
    competition_submission_collection,
    leaderboard_result_collection,
    registration_collection,
    course_collection,
    certificate_collection,
    user_badge_award_collection,
)

logger = logging.getLogger(__name__)

class UserActivityService:
    @staticmethod
    async def get_user_doc(user_id: str) -> Optional[Dict[str, Any]]:
        user = None
        try:
            u_oid = ObjectId(user_id)
            user = await user_collection.find_one({'_id': u_oid})
        except Exception:
            pass

        if not user:
            user = await user_collection.find_one({'email': user_id})
        return user

    @staticmethod
    async def get_user_profile_summary(user_id: str) -> Optional[Dict[str, Any]]:
        user = await UserActivityService.get_user_doc(user_id)
        if not user:
            return None

        email = user.get('email')
        uid_str = str(user.get('_id'))

        # ── Event Metrics ──
        event_regs = await booking_collection.count_documents({'user_email': email})
        cancelled_regs = await booking_collection.count_documents({'user_email': email, 'booking_status': 'CANCELLED'})
        
        # Participated events = tickets checked in
        checked_in_tickets = await issued_ticket_collection.find({'holder_email': email, 'is_checked_in': True}).to_list(length=1000)
        distinct_events_participated = len(set(t.get('booking_id') or t.get('ticket_ref') for t in checked_in_tickets))
        events_completed = distinct_events_participated # Verified attendance

        # ── Competition Metrics ──
        comp_regs = await competition_registration_collection.count_documents({'user_email': email})
        comp_subs = await competition_submission_collection.find({'user_email': email, 'status': 'SUBMITTED'}).to_list(length=1000)
        comp_participated = len(comp_subs)

        # Completed = competitions where results are published
        comp_completed = 0
        for s in comp_subs:
            cid = s.get('competition_id')
            try:
                c = await competition_collection.find_one({'_id': ObjectId(cid)})
            except Exception:
                c = await competition_collection.find_one({'_id': cid})
            if c and c.get('results_published'):
                comp_completed += 1

        # ── Course & Workshop Metrics ──
        courses_reg = await registration_collection.count_documents({'user_email': email, 'type': 'COURSE'})
        workshops_reg = await registration_collection.count_documents({'user_email': email, 'type': 'WORKSHOP'})

        # ── Certificates Metrics ──
        certs = await certificate_collection.find({'user_email': email}).to_list(length=1000)
        total_certs = len(certs)
        part_certs = sum(1 for c in certs if c.get('certificate_type') == 'PARTICIPATION')
        award_certs = sum(1 for c in certs if c.get('certificate_type') == 'AWARD')
        course_certs = sum(1 for c in certs if c.get('certificate_type') == 'COURSE_COMPLETION')
        workshop_certs = sum(1 for c in certs if c.get('certificate_type') == 'WORKSHOP_COMPLETION')
        valid_certs = sum(1 for c in certs if c.get('status') == 'VALID')
        revoked_certs = sum(1 for c in certs if c.get('status') == 'REVOKED')

        # ── Badge Metrics ──
        badges = await user_badge_award_collection.find({'user_email': email}).to_list(length=500)
        total_badges = len(badges)
        active_badges = sum(1 for b in badges if b.get('status') == 'ACTIVE')
        top_part_earned = any(b.get('badge_id') == 'TOP_PARTICIPATION' and b.get('status') == 'ACTIVE' for b in badges)

        return {
            'user': {
                'id': uid_str,
                'name': user.get('name', 'N/A'),
                'email': email,
                'phone': user.get('phone'),
                'role': user.get('role', 'USER'),
                'account_status': user.get('status', 'ACTIVE'),
                'created_at': user.get('created_at'),
            },
            'activity_summary': {
                'total_event_registrations': event_regs,
                'total_distinct_events_participated': distinct_events_participated,
                'total_events_completed': events_completed,
                'total_upcoming_registrations': max(0, event_regs - cancelled_regs - events_completed),
                'total_cancelled_registrations': cancelled_regs,
                'total_competitions_registered': comp_regs,
                'total_competitions_participated': comp_participated,
                'total_competitions_completed': comp_completed,
                'total_courses_registered': courses_reg,
                'total_courses_completed': courses_reg, # confirmed enrollments
                'total_workshops_registered': workshops_reg,
                'total_workshops_completed': workshops_reg,
            },
            'achievements': {
                'total_certificates': total_certs,
                'total_participation_certificates': part_certs,
                'total_award_certificates': award_certs,
                'total_course_certificates': course_certs,
                'total_workshop_certificates': workshop_certs,
                'total_valid_certificates': valid_certs,
                'total_revoked_certificates': revoked_certs,
                'total_badges': total_badges,
                'total_active_badges': active_badges,
                'top_participation_badge_earned': top_part_earned,
            }
        }

    @staticmethod
    async def get_user_activity_timeline(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        user = await UserActivityService.get_user_doc(user_id)
        if not user:
            return []

        email = user.get('email')
        timeline = []

        # 1. Event Bookings
        bookings = await booking_collection.find({'user_email': email}).to_list(length=100)
        for b in bookings:
            timeline.append({
                'activity_type': 'EVENT_REGISTRATION',
                'entity_id': str(b.get('_id')),
                'entity_title': b.get('event_title', 'Event Booking'),
                'timestamp': b.get('created_at') or datetime.now(timezone.utc),
                'status': b.get('booking_status', 'CONFIRMED'),
                'metadata': {'total_amount': b.get('total_amount', 0.0)}
            })

        # 2. Check-ins
        tickets = await issued_ticket_collection.find({'holder_email': email, 'is_checked_in': True}).to_list(length=100)
        for t in tickets:
            timeline.append({
                'activity_type': 'EVENT_CHECK_IN',
                'entity_id': t.get('ticket_ref', ''),
                'entity_title': 'Ticket Checked In',
                'timestamp': t.get('check_in_time') or datetime.now(timezone.utc),
                'status': 'ATTENDED',
                'metadata': {'scanned_by': t.get('scanned_by')}
            })

        # 3. Competition Registrations & Submissions
        c_regs = await competition_registration_collection.find({'user_email': email}).to_list(length=100)
        for cr in c_regs:
            timeline.append({
                'activity_type': 'COMPETITION_REGISTRATION',
                'entity_id': str(cr.get('_id')),
                'entity_title': f"Competition: {cr.get('competition_id')}",
                'timestamp': cr.get('created_at') or datetime.now(timezone.utc),
                'status': cr.get('status', 'REGISTERED'),
                'metadata': {}
            })

        c_subs = await competition_submission_collection.find({'user_email': email}).to_list(length=100)
        for cs in c_subs:
            timeline.append({
                'activity_type': 'COMPETITION_SUBMISSION',
                'entity_id': str(cs.get('_id')),
                'entity_title': f"Submission for {cs.get('competition_id')}",
                'timestamp': cs.get('submitted_at') or datetime.now(timezone.utc),
                'status': cs.get('status', 'SUBMITTED'),
                'metadata': {'files_count': len(cs.get('files', {}))}
            })

        # 4. Certificates
        certs = await certificate_collection.find({'user_email': email}).to_list(length=100)
        for c in certs:
            timeline.append({
                'activity_type': f"CERTIFICATE_{c.get('certificate_type')}",
                'entity_id': c.get('certificate_id', ''),
                'entity_title': f"{c.get('certificate_type')} Certificate: {c.get('activity_title')}",
                'timestamp': c.get('issued_at') or datetime.now(timezone.utc),
                'status': c.get('status', 'VALID'),
                'metadata': {'rank': c.get('rank'), 'delivery': c.get('delivery_status')}
            })

        # 5. Badges
        badges = await user_badge_award_collection.find({'user_email': email}).to_list(length=50)
        for bg in badges:
            timeline.append({
                'activity_type': 'BADGE_AWARDED',
                'entity_id': str(bg.get('_id')),
                'entity_title': f"Badge: {bg.get('badge_name')}",
                'timestamp': bg.get('awarded_at') or datetime.now(timezone.utc),
                'status': bg.get('status', 'ACTIVE'),
                'metadata': {'qualification_count': bg.get('qualification_count')}
            })

        # Sort timeline descending by timestamp
        timeline.sort(key=lambda x: x.get('timestamp') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return timeline[:limit]

    @staticmethod
    async def get_user_events(user_id: str) -> List[Dict[str, Any]]:
        user = await UserActivityService.get_user_doc(user_id)
        if not user: return []
        email = user.get('email')

        bookings = await booking_collection.find({'user_email': email}).to_list(length=200)
        tickets = await issued_ticket_collection.find({'holder_email': email}).to_list(length=200)
        
        ticket_map = {}
        for t in tickets:
            bid = str(t.get('booking_id'))
            ticket_map[bid] = t

        res = []
        for b in bookings:
            bid = str(b.get('_id'))
            t = ticket_map.get(bid, {})
            is_checked = t.get('is_checked_in', False)
            res.append({
                'booking_id': bid,
                'event_title': b.get('event_title', 'Event'),
                'event_category': b.get('event_category', 'General'),
                'registration_date': b.get('created_at'),
                'registration_status': b.get('booking_status', 'CONFIRMED'),
                'participation_status': 'PARTICIPATED' if is_checked else 'REGISTERED',
                'attendance_status': 'CHECKED_IN' if is_checked else 'NOT_CHECKED_IN',
                'completion_status': 'COMPLETED' if is_checked else 'PENDING',
                'ticket_ref': t.get('ticket_ref')
            })
        return res

    @staticmethod
    async def get_user_competitions(user_id: str) -> List[Dict[str, Any]]:
        user = await UserActivityService.get_user_doc(user_id)
        if not user: return []
        email = user.get('email')

        regs = await competition_registration_collection.find({'user_email': email}).to_list(length=200)
        subs = await competition_submission_collection.find({'user_email': email}).to_list(length=200)

        sub_by_comp = {s.get('competition_id'): s for s in subs}

        results = []
        for r in regs:
            cid = r.get('competition_id')
            s = sub_by_comp.get(cid)
            
            try:
                comp = await competition_collection.find_one({'_id': ObjectId(cid)})
            except Exception:
                comp = await competition_collection.find_one({'_id': cid})

            comp_title = comp.get('title', 'Competition') if comp else cid
            results_published = comp.get('results_published', False) if comp else False

            rank = None
            if s and results_published:
                s_id = s.get('_id')
                query_lb = {'competition_id': str(cid)}
                try:
                    or_conds = [{'submission_id': s_id}, {'submission_id': str(s_id)}, {'submission_id': ObjectId(str(s_id))}]
                except Exception:
                    or_conds = [{'submission_id': s_id}, {'submission_id': str(s_id)}]
                query_lb['$or'] = or_conds
                lb = await leaderboard_result_collection.find_one(query_lb)
                if lb:
                    rank = lb.get('rank')

            part_cert = await certificate_collection.find_one({'user_email': email, 'activity_id': cid, 'certificate_type': 'PARTICIPATION'})
            award_cert = await certificate_collection.find_one({'user_email': email, 'activity_id': cid, 'certificate_type': 'AWARD'})

            results.append({
                'competition_id': cid,
                'competition_title': comp_title,
                'registration_date': r.get('created_at'),
                'registration_status': r.get('status', 'REGISTERED'),
                'participation_status': 'PARTICIPATED' if s else 'REGISTERED_ONLY',
                'submission_status': s.get('status') if s else 'NOT_SUBMITTED',
                'submission_date': s.get('submitted_at') if s else None,
                'results_published': results_published,
                'official_rank': rank,
                'has_participation_certificate': bool(part_cert),
                'has_award_certificate': bool(award_cert)
            })
        # Sort competitions newest first
        results.sort(key=lambda x: x.get('registration_date') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return results

    @staticmethod
    async def get_user_courses(user_id: str) -> List[Dict[str, Any]]:
        user = await UserActivityService.get_user_doc(user_id)
        if not user: return []
        email = user.get('email')

        regs = await registration_collection.find({'user_email': email, 'type': 'COURSE'}).to_list(length=200)
        res = []
        for r in regs:
            res.append({
                'registration_id': str(r.get('_id')),
                'course_id': r.get('item_id'),
                'title': r.get('item_title', 'Course'),
                'registration_date': r.get('created_at'),
                'status': r.get('status', 'CONFIRMED'),
                'completion_status': 'COMPLETED' if r.get('status') == 'CONFIRMED' else 'PENDING'
            })
        return res

    @staticmethod
    async def get_user_workshops(user_id: str) -> List[Dict[str, Any]]:
        user = await UserActivityService.get_user_doc(user_id)
        if not user: return []
        email = user.get('email')

        regs = await registration_collection.find({'user_email': email, 'type': 'WORKSHOP'}).to_list(length=200)
        res = []
        for r in regs:
            res.append({
                'registration_id': str(r.get('_id')),
                'workshop_id': r.get('item_id'),
                'title': r.get('item_title', 'Workshop'),
                'registration_date': r.get('created_at'),
                'status': r.get('status', 'CONFIRMED'),
                'completion_status': 'COMPLETED' if r.get('status') == 'CONFIRMED' else 'PENDING'
            })
        return res

    @staticmethod
    async def get_user_badges(user_id: str) -> List[Dict[str, Any]]:
        user = await UserActivityService.get_user_doc(user_id)
        if not user: return []
        email = user.get('email')

        badges = await user_badge_award_collection.find({'user_email': email}).to_list(length=200)
        res = []
        for b in badges:
            b_copy = dict(b)
            b_copy['id'] = str(b_copy.pop('_id'))
            res.append(b_copy)
        return res

    @staticmethod
    async def get_user_certificates(user_id: str) -> List[Dict[str, Any]]:
        user = await UserActivityService.get_user_doc(user_id)
        if not user: return []
        email = user.get('email')

        certs = await certificate_collection.find({'user_email': email}).to_list(length=200)
        res = []
        for c in certs:
            c_copy = dict(c)
            c_copy['id'] = str(c_copy.pop('_id'))
            res.append(c_copy)
        return res

    @staticmethod
    async def get_user_achievements(user_id: str) -> Dict[str, Any]:
        user = await UserActivityService.get_user_doc(user_id)
        if not user: return {}
        email = user.get('email')

        # Awards & Top ranks
        award_certs = await certificate_collection.find({'user_email': email, 'certificate_type': 'AWARD', 'status': 'VALID'}).to_list(length=100)
        badges = await user_badge_award_collection.find({'user_email': email, 'status': 'ACTIVE'}).to_list(length=100)

        awards_list = []
        for ac in award_certs:
            awards_list.append({
                'activity_title': ac.get('activity_title'),
                'rank': ac.get('rank'),
                'award_title': ac.get('award_title'),
                'certificate_id': ac.get('certificate_id'),
                'issue_date': ac.get('issued_at')
            })

        badge_list = []
        for bg in badges:
            badge_list.append({
                'badge_id': bg.get('badge_id'),
                'badge_name': bg.get('badge_name'),
                'awarded_at': bg.get('awarded_at')
            })

        return {
            'total_awards': len(awards_list),
            'awards': awards_list,
            'total_badges': len(badge_list),
            'badges': badge_list
        }
