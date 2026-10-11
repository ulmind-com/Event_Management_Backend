from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from bson import ObjectId

from app.core.admin_auth import get_current_admin
from app.services.referral_service import ReferralService
from app.services.wallet_service import WalletService
from app.services.audit import log_admin_action
from app.core.database import (
    referral_collection,
    coin_ledger_collection,
    coin_reward_lot_collection,
    user_wallet_collection,
    user_collection
)
from app.schemas.referral import (
    ReferralProgramConfigUpdate,
    RewardSettingSchema,
    CoinExpirySchema
)

router = APIRouter()

# --- Config Endpoints ---

@router.get("/referral/config")
async def get_referral_config(current_admin: dict = Depends(get_current_admin)):
    """Retrieve full referral and reward program configuration."""
    config = await ReferralService.get_config()
    config["_id"] = str(config["_id"])
    return config

@router.put("/referral/config")
async def update_referral_config(
    payload: ReferralProgramConfigUpdate,
    current_admin: dict = Depends(get_current_admin)
):
    """
    Update full referral and reward program configuration.
    Controls referrer & welcome reward toggles, coin amounts, valuation, expiry, and redemption rules.
    """
    admin_email = current_admin.get("email", "admin")
    update_data = payload.model_dump()
    updated = await ReferralService.update_config(update_data, admin_email=admin_email)
    updated["_id"] = str(updated["_id"])
    await log_admin_action(admin_email, "update_referral_config", "referral_program", outcome="success")
    return {"message": "Referral program configuration updated successfully", "config": updated}

@router.get("/rewards/config")
async def get_rewards_config(current_admin: dict = Depends(get_current_admin)):
    """Retrieve reward specific configuration."""
    config = await ReferralService.get_config()
    return {
        "referrer_reward": config.get("referrer_reward", {}),
        "welcome_reward": config.get("welcome_reward", {}),
        "coin_valuation": config.get("coin_valuation", {}),
        "redemption_rules": config.get("redemption_rules", {})
    }

@router.put("/rewards/config")
async def update_rewards_config(
    payload: Dict[str, Any],
    current_admin: dict = Depends(get_current_admin)
):
    """Update reward amounts and independent enable/disable settings."""
    admin_email = current_admin.get("email", "admin")
    config = await ReferralService.get_config()
    
    update_data = {}
    if "referrer_reward" in payload:
        update_data["referrer_reward"] = payload["referrer_reward"]
    if "welcome_reward" in payload:
        update_data["welcome_reward"] = payload["welcome_reward"]
    if "coin_valuation" in payload:
        update_data["coin_valuation"] = payload["coin_valuation"]
    if "redemption_rules" in payload:
        update_data["redemption_rules"] = payload["redemption_rules"]

    updated = await ReferralService.update_config(update_data, admin_email=admin_email)
    updated["_id"] = str(updated["_id"])
    await log_admin_action(admin_email, "update_rewards_config", "rewards", outcome="success")
    return {"message": "Reward settings updated successfully", "config": updated}

@router.get("/rewards/expiry-config")
async def get_expiry_config(current_admin: dict = Depends(get_current_admin)):
    """Retrieve coin expiry settings."""
    config = await ReferralService.get_config()
    return config.get("coin_expiry", {})

@router.put("/rewards/expiry-config")
async def update_expiry_config(
    payload: CoinExpirySchema,
    current_admin: dict = Depends(get_current_admin)
):
    """Update coin expiry duration, units, and notification policy."""
    admin_email = current_admin.get("email", "admin")
    update_data = {"coin_expiry": payload.model_dump()}
    updated = await ReferralService.update_config(update_data, admin_email=admin_email)
    updated["_id"] = str(updated["_id"])
    await log_admin_action(admin_email, "update_expiry_config", "rewards_expiry", outcome="success")
    return {"message": "Coin expiry policy updated successfully", "coin_expiry": updated.get("coin_expiry")}

# --- Referral List & Detail ---

@router.get("/referrals")
async def list_referrals(
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    current_admin: dict = Depends(get_current_admin)
):
    """List all referrals with optional status filtering and pagination."""
    query: Dict[str, Any] = {}
    if status_filter:
        query["status"] = status_filter
    if search:
        query["$or"] = [
            {"referrer_email": {"$regex": search, "$options": "i"}},
            {"referred_email": {"$regex": search, "$options": "i"}},
            {"referral_code": {"$regex": search, "$options": "i"}}
        ]

    total = await referral_collection.count_documents(query)
    cursor = referral_collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
    items = await cursor.to_list(length=limit)
    for it in items:
        it["_id"] = str(it["_id"])

    return {"total": total, "limit": limit, "skip": skip, "items": items}

@router.get("/referrals/{referral_id}")
async def get_referral_detail(
    referral_id: str,
    current_admin: dict = Depends(get_current_admin)
):
    """Retrieve full details of a specific referral record."""
    try:
        obj_id = ObjectId(referral_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid referral ID format")

    referral = await referral_collection.find_one({"_id": obj_id})
    if not referral:
        raise HTTPException(status_code=404, detail="Referral record not found")

    referral["_id"] = str(referral["_id"])
    return referral

# --- Rewards Transactions & Expiry Report ---

@router.get("/rewards/transactions")
async def list_reward_transactions(
    email: Optional[str] = Query(None),
    txn_type: Optional[str] = Query(None, alias="type"),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    current_admin: dict = Depends(get_current_admin)
):
    """Immutable coin ledger audit query for Super Admin."""
    query: Dict[str, Any] = {}
    if email:
        query["email"] = {"$regex": email, "$options": "i"}
    if txn_type:
        query["transaction_type"] = txn_type

    total = await coin_ledger_collection.count_documents(query)
    cursor = coin_ledger_collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
    items = await cursor.to_list(length=limit)
    for it in items:
        it["_id"] = str(it["_id"])

    return {"total": total, "limit": limit, "skip": skip, "transactions": items}

@router.get("/rewards/expiry-report")
async def get_expiry_report(
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    current_admin: dict = Depends(get_current_admin)
):
    """Report of expired reward lots and total expired coins."""
    query = {"is_expired": True}
    total = await coin_reward_lot_collection.count_documents(query)
    cursor = coin_reward_lot_collection.find(query).sort("expired_at", -1).skip(skip).limit(limit)
    lots = await cursor.to_list(length=limit)
    for l in lots:
        l["_id"] = str(l["_id"])

    # Aggregated expired sum
    pipeline = [
        {"$match": {"transaction_type": "COIN_EXPIRY"}},
        {"$group": {"_id": None, "total_expired_coins": {"$sum": "$coin_amount"}}}
    ]
    agg_res = await coin_ledger_collection.aggregate(pipeline).to_list(length=1)
    total_expired_coins = agg_res[0]["total_expired_coins"] if agg_res else 0

    return {
        "total_expired_lots": total,
        "total_expired_coins": total_expired_coins,
        "expired_lots": lots
    }

@router.post("/rewards/trigger-expiry")
async def trigger_expiry_cycle(current_admin: dict = Depends(get_current_admin)):
    """Admin trigger to execute an immediate coin expiry cycle."""
    admin_email = current_admin.get("email", "admin")
    result = await WalletService.process_expired_coins()
    await log_admin_action(admin_email, "trigger_coin_expiry", "rewards", outcome="success")
    return {"message": "Coin expiry cycle completed", "result": result}

# --- Referral & Reward Analytics ---

@router.get("/referral/analytics")
async def get_referral_analytics(current_admin: dict = Depends(get_current_admin)):
    """
    Comprehensive referral and reward analytics.
    Distinguishes attributions, registrations, qualifications, rewards, redemptions, and expiries.
    """
    total_attributions = await referral_collection.count_documents({})
    qualified_count = await referral_collection.count_documents({"status": {"$in": ["QUALIFIED", "REWARDED"]}})
    rewarded_count = await referral_collection.count_documents({"status": "REWARDED"})
    pending_count = await referral_collection.count_documents({"status": {"$in": ["ATTRIBUTION_CAPTURED", "PENDING_REGISTRATION", "PENDING_VERIFICATION"]}})
    rejected_count = await referral_collection.count_documents({"status": "REJECTED"})

    # Aggregate coin transactions from immutable ledger
    pipeline = [
        {
            "$group": {
                "_id": "$transaction_type",
                "total_coins": {"$sum": "$coin_amount"},
                "count": {"$sum": 1}
            }
        }
    ]
    txn_agg = await coin_ledger_collection.aggregate(pipeline).to_list(length=20)
    txn_map = {item["_id"]: item["total_coins"] for item in txn_agg}

    referrer_coins_issued = txn_map.get("REFERRER_REFERRAL_REWARD", 0)
    welcome_coins_issued = txn_map.get("NEW_USER_WELCOME_REWARD", 0)
    course_discount_coins = txn_map.get("COURSE_DISCOUNT", 0)
    workshop_discount_coins = txn_map.get("WORKSHOP_DISCOUNT", 0)
    total_coins_expired = txn_map.get("COIN_EXPIRY", 0)
    total_coins_reversed = txn_map.get("REWARD_REVERSAL", 0)

    total_coins_redeemed = course_discount_coins + workshop_discount_coins

    conversion_rate = (qualified_count / total_attributions * 100) if total_attributions > 0 else 0.0

    return {
        "attributions": {
            "total_referral_link_attributions": total_attributions,
            "qualified_referrals": qualified_count,
            "rewarded_referrals": rewarded_count,
            "pending_referrals": pending_count,
            "rejected_referrals": rejected_count,
            "qualification_rate_percent": round(conversion_rate, 2)
        },
        "rewards_issued": {
            "total_coins_to_referrers": referrer_coins_issued,
            "total_coins_to_welcome_users": welcome_coins_issued,
            "total_coins_issued": referrer_coins_issued + welcome_coins_issued
        },
        "redemptions": {
            "course_discount_coins": course_discount_coins,
            "workshop_discount_coins": workshop_discount_coins,
            "total_coins_redeemed": total_coins_redeemed
        },
        "expiries_and_reversals": {
            "total_coins_expired": total_coins_expired,
            "total_coins_reversed": total_coins_reversed
        }
    }
