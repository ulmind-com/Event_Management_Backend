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
    merchandise_order_collection,
    user_wallet_collection,
    coin_ledger_collection,
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
        courses_reg = await registration_collection.count_documents({
            '$or': [{'email': email}, {'user_email': email}, {'participant_email': email}],
            'activity_type': {'$in': ['course', 'COURSE']}
        })
        workshops_reg = await registration_collection.count_documents({
            '$or': [{'email': email}, {'user_email': email}, {'participant_email': email}],
            'activity_type': {'$in': ['workshop', 'WORKSHOP']}
        })

        # ── Merchandise Metrics ──
        merch_orders = await merchandise_order_collection.find({
            '$or': [{'email': email}, {'user_email': email}]
        }).to_list(length=500)
        total_merch_orders = len(merch_orders)
        confirmed_merch_orders = sum(
            1 for m in merch_orders
            if m.get('status') in ['CONFIRMED', 'DELIVERED', 'SHIPPED', 'PAID'] or m.get('payment_status') == 'PAID'
        )
        total_merch_spent = sum(
            float(m.get('total_amount', 0.0))
            for m in merch_orders
            if m.get('status') in ['CONFIRMED', 'DELIVERED', 'SHIPPED', 'PAID'] or m.get('payment_status') == 'PAID'
        )

        # ── Coin & Wallet Metrics ──
        wallet = await user_wallet_collection.find_one({'email': email}) or {}
        available_coins = wallet.get('available_coins', 0)
        total_earned_coins = wallet.get('total_earned_coins', 0)
        total_redeemed_coins = wallet.get('total_redeemed_coins', 0)
        reserved_coins = wallet.get('reserved_coins', 0)

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

        # ── Coin History, Courses, and Merchandise Breakdown ──
        coin_txns = await coin_ledger_collection.find({'email': email}).sort('created_at', -1).to_list(length=100)
        serialized_coin_txns = []
        for t in coin_txns:
            t_copy = dict(t)
            t_copy['_id'] = str(t_copy.get('_id'))
            serialized_coin_txns.append(t_copy)

        user_courses = await UserActivityService.get_user_courses(user_id)
        user_merch = await UserActivityService.get_user_merchandise_orders(user_id)

        is_verified = bool(
            user.get('is_verified')
            or user.get('is_id_verified')
            or user.get('is_govt_id_verified')
            or user.get('kyc_status') == 'VERIFIED'
        )

        return {
            'user': {
                'id': uid_str,
                'name': user.get('name', 'N/A'),
                'email': email,
                'phone': user.get('phone'),
                'role': user.get('role', 'USER'),
                'account_status': user.get('status', 'ACTIVE'),
                'is_verified': is_verified,
                'is_id_verified': is_verified,
                'is_govt_id_verified': is_verified,
                'verified_badge': is_verified,
                'verified_tick': is_verified,
                'verification_status': 'VERIFIED' if is_verified else user.get('verification_status', 'NOT_VERIFIED'),
                'verified_at': user.get('verified_at'),
                'verified_by': user.get('verified_by'),
                'verification_details': user.get('verification_details'),
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
                'total_merchandise_orders': total_merch_orders,
                'total_merchandise_confirmed': confirmed_merch_orders,
                'total_merchandise_spent': round(total_merch_spent, 2),
            },
            'wallet': {
                'available_coins': available_coins,
                'total_earned_coins': total_earned_coins,
                'total_redeemed_coins': total_redeemed_coins,
                'reserved_coins': reserved_coins,
                'coin_history': serialized_coin_txns,
            },
            'courses': user_courses,
            'merchandise_orders': user_merch,
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

        # 6. Merchandise Orders
        m_orders = await merchandise_order_collection.find({
            '$or': [{'email': email}, {'user_email': email}]
        }).to_list(length=100)
        for mo in m_orders:
            items_summary = ", ".join([f"{it.get('title', 'Item')} (x{it.get('quantity', 1)})" for it in mo.get('items', [])]) or "Merchandise Order"
            timeline.append({
                'activity_type': 'MERCHANDISE_ORDER',
                'entity_id': mo.get('order_number') or str(mo.get('_id')),
                'entity_title': f"Order #{mo.get('order_number', '')}: {items_summary}",
                'timestamp': mo.get('created_at') or datetime.now(timezone.utc),
                'status': mo.get('status', 'PENDING_PAYMENT'),
                'metadata': {
                    'total_amount': mo.get('total_amount', 0.0),
                    'payment_status': mo.get('payment_status'),
                    'items_count': len(mo.get('items', []))
                }
            })

        # 7. Coin Transactions (Earned / Spent)
        c_txns = await coin_ledger_collection.find({'email': email}).to_list(length=100)
        for ct in c_txns:
            timeline.append({
                'activity_type': f"COIN_{ct.get('direction', 'CREDIT')}",
                'entity_id': ct.get('transaction_id') or str(ct.get('_id')),
                'entity_title': f"{ct.get('direction', 'CREDIT')}: {ct.get('coin_amount', 0)} coins - {ct.get('description', '')}",
                'timestamp': ct.get('created_at') or datetime.now(timezone.utc),
                'status': ct.get('status', 'COMPLETED'),
                'metadata': {
                    'coins': ct.get('coin_amount', 0),
                    'direction': ct.get('direction'),
                    'transaction_type': ct.get('transaction_type'),
                    'reference_id': ct.get('reference_id')
                }
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

        regs = await registration_collection.find({
            '$or': [{'email': email}, {'user_email': email}, {'participant_email': email}],
            'activity_type': {'$in': ['course', 'COURSE']}
        }).sort('created_at', -1).to_list(length=200)

        res = []
        for r in regs:
            cid = r.get('activity_id') or r.get('item_id')
            course_title = r.get('item_title') or r.get('activity_title')
            course_category = None
            course_price = r.get('original_price') or r.get('price_snapshot')
            
            # Lookup course if title not in registration
            if cid:
                try:
                    c_doc = await course_collection.find_one({'_id': ObjectId(cid)})
                except Exception:
                    c_doc = await course_collection.find_one({'_id': cid})
                if c_doc:
                    course_title = course_title or c_doc.get('title')
                    course_category = c_doc.get('category')
                    if course_price is None:
                        course_price = c_doc.get('price')

            res.append({
                'registration_id': str(r.get('_id')),
                'course_id': str(cid) if cid else None,
                'title': course_title or 'Course',
                'category': course_category,
                'price': course_price,
                'payable_amount': r.get('payable_amount'),
                'coins_applied': r.get('coins_applied', 0),
                'discount_amount': r.get('discount_amount', 0.0),
                'order_reference': r.get('order_reference'),
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

        regs = await registration_collection.find({
            '$or': [{'email': email}, {'user_email': email}, {'participant_email': email}],
            'activity_type': {'$in': ['workshop', 'WORKSHOP']}
        }).sort('created_at', -1).to_list(length=200)

        res = []
        for r in regs:
            wid = r.get('activity_id') or r.get('item_id')
            w_title = r.get('item_title') or r.get('activity_title')
            w_category = None
            w_price = r.get('original_price') or r.get('price_snapshot')

            if wid:
                try:
                    w_doc = await course_collection.find_one({'_id': ObjectId(wid)})
                except Exception:
                    w_doc = await course_collection.find_one({'_id': wid})
                if w_doc:
                    w_title = w_title or w_doc.get('title')
                    w_category = w_doc.get('category')
                    if w_price is None:
                        w_price = w_doc.get('price')

            res.append({
                'registration_id': str(r.get('_id')),
                'workshop_id': str(wid) if wid else None,
                'title': w_title or 'Workshop',
                'category': w_category,
                'price': w_price,
                'payable_amount': r.get('payable_amount'),
                'coins_applied': r.get('coins_applied', 0),
                'discount_amount': r.get('discount_amount', 0.0),
                'order_reference': r.get('order_reference'),
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

    @staticmethod
    async def get_user_merchandise_orders(user_id: str) -> List[Dict[str, Any]]:
        user = await UserActivityService.get_user_doc(user_id)
        if not user: return []
        email = user.get('email')

        orders = await merchandise_order_collection.find({
            '$or': [{'user_email': email}, {'email': email}]
        }).sort('created_at', -1).to_list(length=200)

        res = []
        for o in orders:
            o_copy = dict(o)
            o_copy['order_id'] = str(o_copy.get('_id'))
            o_copy['_id'] = str(o_copy.get('_id'))
            res.append(o_copy)
        return res

    @staticmethod
    async def get_user_wallet_and_coins(user_id: str) -> Dict[str, Any]:
        user = await UserActivityService.get_user_doc(user_id)
        if not user: return {}
        email = user.get('email')

        wallet = await user_wallet_collection.find_one({'email': email}) or {}
        txns = await coin_ledger_collection.find({'email': email}).sort('created_at', -1).to_list(length=300)

        serialized_txns = []
        for t in txns:
            t_copy = dict(t)
            t_copy['_id'] = str(t_copy.get('_id'))
            serialized_txns.append(t_copy)

        return {
            'user_id': user_id,
            'email': email,
            'wallet': {
                'available_coins': wallet.get('available_coins', 0),
                'pending_coins': wallet.get('pending_coins', 0),
                'reserved_coins': wallet.get('reserved_coins', 0),
                'total_earned_coins': wallet.get('total_earned_coins', 0),
                'total_redeemed_coins': wallet.get('total_redeemed_coins', 0),
                'total_expired_coins': wallet.get('total_expired_coins', 0),
                'total_reversed_coins': wallet.get('total_reversed_coins', 0),
                'updated_at': wallet.get('updated_at'),
            },
            'summary': {
                'total_transactions': len(serialized_txns),
                'total_credited': sum(t.get('coin_amount', 0) for t in serialized_txns if t.get('direction') == 'CREDIT'),
                'total_debited': sum(t.get('coin_amount', 0) for t in serialized_txns if t.get('direction') == 'DEBIT'),
            },
            'transactions': serialized_txns
        }
