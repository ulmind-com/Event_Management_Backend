import os
import random
import string
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from bson import ObjectId

from app.core.database import (
    user_collection,
    referral_program_config_collection,
    referral_collection,
    coin_ledger_collection
)
from app.services.wallet_service import WalletService

class ReferralService:
    @staticmethod
    def generate_unique_code(prefix: str = "EVNT", length: int = 6) -> str:
        chars = string.ascii_uppercase + string.digits
        random_suffix = "".join(random.choices(chars, k=length))
        return f"{prefix}{random_suffix}"

    @classmethod
    async def get_or_create_user_referral_code(cls, user_email: str) -> str:
        user = await user_collection.find_one({"email": user_email})
        if not user:
            raise ValueError("User not found")
        
        if user.get("referral_code"):
            return user["referral_code"]
        
        # Collision-safe code generation
        for _ in range(10):
            code = cls.generate_unique_code()
            exists = await user_collection.find_one({"referral_code": code})
            if not exists:
                await user_collection.update_one(
                    {"email": user_email},
                    {"$set": {"referral_code": code, "referral_code_created_at": datetime.now(timezone.utc)}}
                )
                return code
        raise RuntimeError("Failed to generate unique referral code after multiple attempts")

    @classmethod
    def build_website_referral_link(cls, referral_code: str) -> str:
        base_url = os.getenv("FRONTEND_URL", "https://eventora.com").rstrip("/")
        return f"{base_url}/register?ref={referral_code}"

    @classmethod
    def build_app_referral_link(cls, referral_code: str) -> str:
        app_base = os.getenv("APP_DOWNLOAD_URL", "https://app.eventora.com/download").rstrip("/")
        return f"{app_base}?ref={referral_code}"

    @classmethod
    def build_referral_link(cls, referral_code: str) -> str:
        return cls.build_website_referral_link(referral_code)

    @classmethod
    async def get_config(cls) -> Dict[str, Any]:
        config = await referral_program_config_collection.find_one({"_id": "current_config"})
        if not config:
            # Default production configuration
            default_config = {
                "_id": "current_config",
                "program_enabled": True,
                "campaign_start_at": None,
                "campaign_end_at": None,
                "attribution_expiry_days": 30,
                "referrer_reward": {
                    "enabled": True,
                    "coins": 100,
                    "activation_delay_hours": 0,
                    "require_admin_approval": False
                },
                "welcome_reward": {
                    "enabled": True,
                    "coins": 50,
                    "activation_delay_hours": 0,
                    "require_admin_approval": False
                },
                "coin_valuation": {
                    "currency": "INR",
                    "value_per_coin": 0.50
                },
                "coin_expiry": {
                    "enabled": True,
                    "duration_value": 90,
                    "duration_unit": "DAYS",
                    "warning_days": 7,
                    "notify_user": True
                },
                "redemption_rules": {
                    "course_redemption_enabled": True,
                    "workshop_redemption_enabled": True,
                    "min_coins_required": 10,
                    "max_coins_per_order": 500,
                    "min_purchase_amount": 100.0,
                    "max_discount_percentage": 50.0,
                    "max_discount_amount": 1000.0,
                    "allow_with_coupons": False,
                    "eligible_course_ids": [],
                    "eligible_workshop_ids": [],
                    "excluded_item_ids": []
                },
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
                "updated_by": "system"
            }
            try:
                await referral_program_config_collection.update_one(
                    {"_id": "current_config"},
                    {"$setOnInsert": default_config},
                    upsert=True
                )
            except Exception:
                pass
            return await referral_program_config_collection.find_one({"_id": "current_config"}) or default_config
        return config

    @classmethod
    async def update_config(cls, update_data: Dict[str, Any], admin_email: str) -> Dict[str, Any]:
        update_data["updated_at"] = datetime.now(timezone.utc)
        update_data["updated_by"] = admin_email
        await referral_program_config_collection.update_one(
            {"_id": "current_config"},
            {"$set": update_data},
            upsert=True
        )
        return await cls.get_config()

    @classmethod
    async def attribute_referral(
        cls,
        referred_email: str,
        referral_code: str,
        attribution_source: str = "web_link"
    ) -> Dict[str, Any]:
        config = await cls.get_config()
        if not config.get("program_enabled", True):
            return {"status": "REJECTED", "reason": "Referral program is currently disabled"}

        now = datetime.now(timezone.utc)
        start_at = config.get("campaign_start_at")
        end_at = config.get("campaign_end_at")
        if start_at and now < (start_at if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)):
            return {"status": "REJECTED", "reason": "Referral campaign has not started yet"}
        if end_at and now > (end_at if end_at.tzinfo else end_at.replace(tzinfo=timezone.utc)):
            return {"status": "REJECTED", "reason": "Referral campaign has ended"}

        # Find referrer user by code
        referrer = await user_collection.find_one({"referral_code": referral_code.strip()})
        if not referrer:
            return {"status": "REJECTED", "reason": "Invalid referral code"}

        referrer_email = referrer["email"]
        if referrer_email.lower() == referred_email.lower():
            return {"status": "REJECTED", "reason": "Self-referral is strictly prohibited"}

        # Check if referred user already completed registration prior to this attribution
        existing_user = await user_collection.find_one({"email": referred_email})
        if existing_user and existing_user.get("registration_complete"):
            return {"status": "REJECTED", "reason": "User already has an active registered account"}

        # Check if attribution already exists for this referred user
        existing_attr = await referral_collection.find_one({"referred_email": referred_email})
        if existing_attr:
            return {
                "status": existing_attr.get("status", "ATTRIBUTION_CAPTURED"),
                "referral_id": str(existing_attr["_id"]),
                "message": "Referral already attributed"
            }

        attribution_expiry = now + timedelta(days=config.get("attribution_expiry_days", 30))
        new_referral = {
            "referrer_email": referrer_email,
            "referred_email": referred_email,
            "referral_code": referral_code.strip(),
            "attribution_source": attribution_source,
            "status": "ATTRIBUTION_CAPTURED",
            "attributed_at": now,
            "attribution_expires_at": attribution_expiry,
            "referrer_reward_credited": False,
            "welcome_reward_credited": False,
            "referrer_coins_awarded": 0,
            "welcome_coins_awarded": 0,
            "created_at": now,
            "updated_at": now
        }

        result = await referral_collection.insert_one(new_referral)
        return {
            "status": "ATTRIBUTION_CAPTURED",
            "referral_id": str(result.inserted_id),
            "referrer_name": referrer.get("name", "Eventora Member"),
            "message": "Referral attribution captured successfully"
        }

    @classmethod
    async def qualify_and_reward_referral(cls, referred_email: str) -> Dict[str, Any]:
        """
        Qualifies a referral after new user completes registration & verification.
        Issues configured referrer and welcome rewards transactionally and idempotently.
        """
        referral = await referral_collection.find_one({"referred_email": referred_email})
        if not referral:
            return {"qualified": False, "reason": "No referral attribution found"}

        if referral.get("status") in ["REWARDED", "QUALIFIED"]:
            return {
                "qualified": True,
                "already_processed": True,
                "status": referral.get("status"),
                "referrer_coins": referral.get("referrer_coins_awarded", 0),
                "welcome_coins": referral.get("welcome_coins_awarded", 0)
            }

        if referral.get("status") == "REJECTED":
            return {"qualified": False, "reason": "Referral was rejected"}

        # Validate user registration and verification
        user = await user_collection.find_one({"email": referred_email})
        if not user or not user.get("registration_complete") or not user.get("email_verified"):
            return {"qualified": False, "reason": "User registration and verification not yet complete"}

        config = await cls.get_config()
        if not config.get("program_enabled", True):
            await referral_collection.update_one(
                {"_id": referral["_id"]},
                {"$set": {"status": "REJECTED", "rejection_reason": "Program disabled at qualification", "updated_at": datetime.now(timezone.utc)}}
            )
            return {"qualified": False, "reason": "Referral program is disabled"}

        referrer_email = referral["referrer_email"]
        referrer = await user_collection.find_one({"email": referrer_email})
        if not referrer or referrer.get("is_deleted"):
            await referral_collection.update_one(
                {"_id": referral["_id"]},
                {"$set": {"status": "REJECTED", "rejection_reason": "Referrer account is inactive or deleted", "updated_at": datetime.now(timezone.utc)}}
            )
            return {"qualified": False, "reason": "Referrer account not found or deleted"}

        # Atomic qualification
        now = datetime.now(timezone.utc)
        ref_id_str = str(referral["_id"])

        referrer_cfg = config.get("referrer_reward", {})
        welcome_cfg = config.get("welcome_reward", {})

        referrer_coins = referrer_cfg.get("coins", 100) if referrer_cfg.get("enabled", True) else 0
        welcome_coins = welcome_cfg.get("coins", 50) if welcome_cfg.get("enabled", True) else 0

        # Credit Referrer Reward (if enabled and not yet credited)
        referrer_credited = referral.get("referrer_reward_credited", False)
        if referrer_coins > 0 and not referrer_credited:
            await WalletService.credit_reward_lot(
                user_email=referrer_email,
                coin_amount=referrer_coins,
                source_type="REFERRER_REFERRAL_REWARD",
                reference_type="referral",
                reference_id=ref_id_str,
                description=f"Referral reward for inviting {referred_email}",
                config=config
            )
            referrer_credited = True

        # Credit Welcome Reward (if enabled and not yet credited)
        welcome_credited = referral.get("welcome_reward_credited", False)
        if welcome_coins > 0 and not welcome_credited:
            await WalletService.credit_reward_lot(
                user_email=referred_email,
                coin_amount=welcome_coins,
                source_type="NEW_USER_WELCOME_REWARD",
                reference_type="referral",
                reference_id=ref_id_str,
                description=f"Welcome bonus for joining with referral from {referrer_email}",
                config=config
            )
            welcome_credited = True

        final_status = "REWARDED" if (referrer_credited or welcome_credited or (referrer_coins == 0 and welcome_coins == 0)) else "QUALIFIED"

        await referral_collection.update_one(
            {"_id": referral["_id"]},
            {
                "$set": {
                    "status": final_status,
                    "qualified_at": now,
                    "referrer_reward_credited": referrer_credited,
                    "welcome_reward_credited": welcome_credited,
                    "referrer_coins_awarded": referrer_coins,
                    "welcome_coins_awarded": welcome_coins,
                    "updated_at": now
                }
            }
        )

        return {
            "qualified": True,
            "status": final_status,
            "referrer_coins_awarded": referrer_coins,
            "welcome_coins_awarded": welcome_coins,
            "referrer_email": referrer_email,
            "referred_email": referred_email
        }

    @classmethod
    async def get_user_referral_profile(cls, user_email: str) -> Dict[str, Any]:
        code = await cls.get_or_create_user_referral_code(user_email)
        link = cls.build_referral_link(code)

        referrals = await referral_collection.find({"referrer_email": user_email}).sort("created_at", -1).to_list(length=100)
        total_referrals = len(referrals)
        qualified_referrals = sum(1 for r in referrals if r.get("status") in ["QUALIFIED", "REWARDED"])
        pending_referrals = sum(1 for r in referrals if r.get("status") in ["ATTRIBUTION_CAPTURED", "PENDING_REGISTRATION", "PENDING_VERIFICATION"])
        total_referrer_rewards = sum(r.get("referrer_coins_awarded", 0) for r in referrals if r.get("referrer_reward_credited"))

        # Welcome rewards received by this user
        own_referral = await referral_collection.find_one({"referred_email": user_email})
        welcome_received = own_referral.get("welcome_coins_awarded", 0) if own_referral and own_referral.get("welcome_reward_credited") else 0

        history = []
        for r in referrals:
            history.append({
                "referral_id": str(r["_id"]),
                "referred_email_masked": cls._mask_email(r.get("referred_email", "")),
                "status": r.get("status"),
                "attributed_at": r.get("attributed_at"),
                "qualified_at": r.get("qualified_at"),
                "coins_earned": r.get("referrer_coins_awarded", 0) if r.get("referrer_reward_credited") else 0
            })

        web_link = cls.build_website_referral_link(code)
        app_link = cls.build_app_referral_link(code)
        return {
            "referral_code": code,
            "website_referral_link": web_link,
            "app_referral_link": app_link,
            "referral_link": web_link,
            "total_referrals": total_referrals,
            "qualified_referrals": qualified_referrals,
            "pending_referrals": pending_referrals,
            "total_referrer_rewards_earned": total_referrer_rewards,
            "total_welcome_rewards_received": welcome_received,
            "referral_history": history
        }

    @staticmethod
    def _mask_email(email: str) -> str:
        if "@" not in email:
            return email
        parts = email.split("@")
        name = parts[0]
        domain = parts[1]
        if len(name) <= 2:
            masked_name = name[0] + "*"
        else:
            masked_name = name[0] + "*" * (len(name) - 2) + name[-1]
        return f"{masked_name}@{domain}"
