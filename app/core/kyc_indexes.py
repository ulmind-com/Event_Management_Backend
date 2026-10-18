"""
Aadhaar KYC Verification Database Indexes
==========================================
Ensures fast querying, anti-duplicate uniqueness, and audit indexing for KYC records.
"""

import logging
from pymongo import ASCENDING, DESCENDING, IndexModel
from app.core.database import kyc_verification_collection

logger = logging.getLogger("eventora.kyc_indexes")


async def init_kyc_indexes():
    """
    Idempotent initialization of KYC collection indexes.
    Called on application startup.
    """
    try:
        # Unique index on user_email: each user has at most one active KYC record
        await kyc_verification_collection.create_index(
            [("user_email", ASCENDING)],
            unique=True,
            name="uniq_user_email_kyc"
        )

        # Index on aadhaar_hash for rapid duplicate lookup
        await kyc_verification_collection.create_index(
            [("aadhaar_hash", ASCENDING), ("status", ASCENDING)],
            name="idx_aadhaar_hash_status"
        )

        # Index on status + created_at for admin listing and pagination
        await kyc_verification_collection.create_index(
            [("status", ASCENDING), ("created_at", DESCENDING)],
            name="idx_status_created"
        )

        logger.info("Aadhaar KYC indexes initialized successfully.")
    except Exception as e:
        logger.warning(f"Could not initialize KYC indexes (may already exist or DB offline): {e}")
