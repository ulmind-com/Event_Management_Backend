import logging
from app.core.database import (
    user_collection,
    competition_registration_collection,
    registration_collection,
    competition_collection,
    event_collection,
    issued_ticket_collection,
    booking_collection,
    judge_score_collection,
    leaderboard_result_collection,
    referral_collection,
    banner_collection,
    otp_collection,
    idempotency_collection,
    webhook_event_collection,
    payment_recovery_collection,
    competition_submission_collection,
    audience_vote_collection,
    judge_assignment_collection,
    ticket_collection,
    seat_hold_collection,
)

logger = logging.getLogger(__name__)

async def init_core_database_indexes():
    """
    Ensures high-performance B-tree indexes across all high-throughput collections.
    Critical for traffic surges (1,000+ to 10,000+ concurrent users) to eliminate
    full collection scans (COLLSCAN) and prevent database connection exhaustion.
    """
    try:
        # 1. Users collection (O(1) authentication, profile lookups, duplicate protection)
        await user_collection.create_index([("email", 1)], unique=True, background=True)
        await user_collection.create_index([("phone_number", 1)], sparse=True, background=True)
        await user_collection.create_index([("role", 1)], background=True)
        await user_collection.create_index([("is_deleted", 1)], background=True)
        await user_collection.create_index([("created_at", -1)], background=True)

        # 2. Competition registrations (Guarantees zero duplicate registrations during concurrency spikes)
        await competition_registration_collection.create_index(
            [("competition_id", 1), ("user_email", 1)],
            unique=True,
            background=True
        )
        await competition_registration_collection.create_index(
            [("competition_id", 1), ("status", 1), ("category", 1)],
            background=True
        )
        await competition_registration_collection.create_index(
            [("registration_reference", 1)],
            unique=True,
            sparse=True,
            background=True
        )
        await competition_registration_collection.create_index([("user_email", 1)], background=True)
        await competition_registration_collection.create_index([("competition_id", 1), ("registered_at", -1)], background=True)
        await competition_registration_collection.create_index([("competition_id", 1), ("is_present", 1), ("attended_at", 1)], background=True)
        await competition_registration_collection.create_index([("competition_id", 1), ("stage_order", 1), ("registered_at", 1)], background=True)

        # 3. Standard Activity Registrations
        await registration_collection.create_index(
            [("activity_id", 1), ("email", 1), ("activity_type", 1)],
            unique=True,
            background=True
        )
        await registration_collection.create_index([("email", 1)], background=True)
        await registration_collection.create_index([("registration_reference", 1)], unique=True, sparse=True, background=True)

        # 4. Competitions & Events (Public discovery & listing queries)
        await competition_collection.create_index(
            [("is_published", 1), ("is_deleted", 1), ("display_order", 1)],
            background=True
        )
        await competition_collection.create_index([("start_date", 1)], background=True)
        await event_collection.create_index(
            [("is_published", 1), ("is_deleted", 1), ("display_order", 1)],
            background=True
        )
        await event_collection.create_index([("date", 1)], background=True)
        await event_collection.create_index([("volunteers.email", 1)], background=True)

        # 5. Tickets & Bookings (Gate High-Volume Scanning & Concurrency)
        await issued_ticket_collection.create_index([("ticket_ref", 1)], unique=True, background=True)
        await issued_ticket_collection.create_index([("user_email", 1)], background=True)
        await issued_ticket_collection.create_index([("event_id", 1)], background=True)
        await issued_ticket_collection.create_index([("booking_id", 1)], background=True)
        await issued_ticket_collection.create_index([("checked_in_by", 1), ("event_id", 1)], background=True)
        await issued_ticket_collection.create_index([("scanned_by", 1), ("event_id", 1)], background=True)
        await issued_ticket_collection.create_index([("cash_collected_by", 1), ("event_id", 1)], background=True)
        await issued_ticket_collection.create_index([("event_id", 1), ("is_checked_in", 1)], background=True)
        await issued_ticket_collection.create_index([("event_id", 1), ("cash_status", 1)], background=True)

        await booking_collection.create_index([("booking_reference", 1)], unique=True, background=True)
        await booking_collection.create_index([("user_email", 1)], background=True)
        await booking_collection.create_index([("event_id", 1)], background=True)
        await ticket_collection.create_index([("event_id", 1), ("category", 1)], background=True)

        # 6. Competition Submissions & Audience Votes
        await competition_submission_collection.create_index(
            [("competition_id", 1), ("user_email", 1)],
            background=True
        )
        await competition_submission_collection.create_index(
            [("competition_id", 1), ("status", 1)],
            background=True
        )
        await competition_submission_collection.create_index(
            [("competition_id", 1), ("created_at", -1)],
            background=True
        )
        await audience_vote_collection.create_index(
            [("competition_id", 1), ("round_number", 1)],
            background=True
        )
        await audience_vote_collection.create_index(
            [("competition_id", 1), ("participant_id", 1)],
            background=True
        )
        await audience_vote_collection.create_index(
            [("competition_id", 1), ("voter_email", 1)],
            background=True
        )

        # 7. Judging & Live Scoring (Fast Parallel Judge Submissions)
        # Drop legacy restrictive unique index that could conflict when multiple judges score same stage participant
        try:
            cur_indexes = await judge_score_collection.index_information()
            if "competition_id_1_round_number_1_submission_id_1_judge_id_1" in cur_indexes:
                await judge_score_collection.drop_index("competition_id_1_round_number_1_submission_id_1_judge_id_1")
                logger.info("Successfully dropped legacy restrictive unique index on judge_scores.")
        except Exception:
            pass

        await judge_assignment_collection.create_index(
            [("competition_id", 1), ("judge_email", 1)],
            background=True
        )
        await judge_assignment_collection.create_index([("judge_email", 1)], background=True)

        await judge_score_collection.create_index(
            [("competition_id", 1), ("round_number", 1), ("submission_id", 1), ("judge_id", 1)],
            background=True
        )
        await judge_score_collection.create_index(
            [("competition_id", 1), ("round_number", 1), ("judge_email", 1), ("participant_id", 1)],
            background=True
        )
        await judge_score_collection.create_index(
            [("competition_id", 1), ("round_number", 1), ("judge_email", 1), ("submission_id", 1)],
            background=True
        )
        await judge_score_collection.create_index([("competition_id", 1), ("round_number", 1)], background=True)

        # 7. Leaderboards & Standings
        await leaderboard_result_collection.create_index(
            [("competition_id", 1), ("category", 1), ("round_number", 1)],
            background=True
        )

        # 8. Referrals
        await referral_collection.create_index([("referred_email", 1)], background=True)
        await referral_collection.create_index([("referrer_email", 1)], background=True)
        await referral_collection.create_index([("referral_code", 1)], background=True)

        # 9. Banners
        await banner_collection.create_index(
            [("is_published", 1), ("is_visible", 1), ("is_deleted", 1), ("display_order", 1)],
            background=True
        )

        # 10. OTPs (TTL index for automatic self-deletion)
        await otp_collection.create_index([("email", 1)], background=True)
        await otp_collection.create_index([("expires_at", 1)], expireAfterSeconds=0, background=True)

        # 11. Production Idempotency Records (Unique Key + 24-hr TTL index)
        await idempotency_collection.create_index([("idempotency_key", 1)], unique=True, background=True)
        await idempotency_collection.create_index([("created_at", 1)], expireAfterSeconds=86400, background=True)
        await idempotency_collection.create_index([("user_email", 1), ("endpoint", 1)], background=True)

        # 12. Webhook Events (Deduplication + 7-day TTL index)
        await webhook_event_collection.create_index([("event_key", 1)], unique=True, background=True)
        await webhook_event_collection.create_index([("received_at", 1)], expireAfterSeconds=604800, background=True)

        # 13. Payment Recovery Queue (Fast indexing for background reconciliation worker)
        await payment_recovery_collection.create_index([("status", 1), ("recovered_at", -1)], background=True)
        await payment_recovery_collection.create_index([("reference", 1)], background=True)

        # 14. Seat Holds (BookMyShow-style 5-minute atomic holds with auto-expiring TTL)
        await seat_hold_collection.create_index(
            [("event_id", 1), ("seat_id", 1)],
            unique=True,
            background=True
        )
        await seat_hold_collection.create_index([("hold_token", 1)], background=True)
        await seat_hold_collection.create_index([("expires_at", 1)], expireAfterSeconds=0, background=True)

        logger.info("High-performance core database indexes initialized successfully.")
    except Exception as e:
        logger.warning(f"Core database index initialization notice: {e}")

