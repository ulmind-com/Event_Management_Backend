from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import Optional, List, Dict, Any
from app.api.deps import get_current_user
from app.core.database import certificate_collection, reward_collection, coin_ledger_collection, coin_reward_lot_collection
from app.services.wallet_service import WalletService
from app.services.referral_service import ReferralService
from app.schemas.referral import (
    ReferralAttributeRequest,
    DiscountQuoteRequest,
    ReserveDiscountRequest
)

router = APIRouter()

# --- Existing Endpoints (Preserved for 100% Backward Compatibility) ---

@router.get("/certificates")
async def get_my_certificates(current_user: dict = Depends(get_current_user)):
    certs = await certificate_collection.find({"email": current_user["email"]}).to_list(length=100)
    for c in certs:
        c["_id"] = str(c["_id"])
    return certs

@router.get("/coins")
async def get_my_coins(current_user: dict = Depends(get_current_user)):
    """Legacy coins query preserved; also integrates with wallet service."""
    wallet = await WalletService.get_or_create_wallet(current_user["email"])
    rewards = await reward_collection.find({"email": current_user["email"]}).to_list(length=100)
    for r in rewards:
        r["_id"] = str(r["_id"])
    return {
        "total_coins": wallet.get("available_coins", 0),
        "history": rewards
    }

# --- New Referral & Coin Wallet Endpoints ---

@router.post("/referral/attribute")
async def attribute_referral(payload: ReferralAttributeRequest, current_user: dict = Depends(get_current_user)):
    """
    Captures referral attribution for the current user.
    Enforces self-referral prevention and single-referrer rules.
    """
    user_email = current_user["email"]
    result = await ReferralService.attribute_referral(
        referred_email=user_email,
        referral_code=payload.referral_code,
        attribution_source="app_or_web"
    )
    if result.get("status") == "REJECTED":
        raise HTTPException(status_code=400, detail=result.get("reason", "Referral rejected"))
    return result

@router.get("/wallet")
async def get_user_wallet(current_user: dict = Depends(get_current_user)):
    """
    Authoritative user wallet summary with ledger-based balances,
    active valuation snapshot, upcoming expiry summary, and cash withdrawal restrictions.
    """
    user_email = current_user["email"]
    summary = await WalletService.get_wallet_summary(user_email)
    return summary

@router.get("/referral-profile")
async def get_referral_profile(current_user: dict = Depends(get_current_user)):
    """
    Returns user's unique referral code, personal referral link,
    referral stats (total, qualified, pending, rewards earned), and referral history.
    """
    user_email = current_user["email"]
    profile = await ReferralService.get_user_referral_profile(user_email)
    return profile

@router.get("/transactions")
async def get_my_transactions(
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """Immutable coin ledger transaction history for authenticated user."""
    user_email = current_user["email"]
    query = {"email": user_email}
    total = await coin_ledger_collection.count_documents(query)
    cursor = coin_ledger_collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
    items = await cursor.to_list(length=limit)
    for it in items:
        it["_id"] = str(it["_id"])
    return {"total": total, "limit": limit, "skip": skip, "transactions": items}

@router.get("/lots")
async def get_my_reward_lots(
    current_user: dict = Depends(get_current_user)
):
    """Returns individual unexpired reward lots with credited and expiry dates."""
    user_email = current_user["email"]
    lots = await coin_reward_lot_collection.find({
        "email": user_email,
        "is_expired": False,
        "remaining_coins": {"$gt": 0}
    }).sort("expires_at", 1).to_list(length=100)
    for l in lots:
        l["_id"] = str(l["_id"])
    return lots

# --- Course & Workshop Discount Endpoints ---

@router.post("/calculate-discount")
async def calculate_discount(
    payload: DiscountQuoteRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Computes discount quote based on item price, user available coins,
    and Super Admin redemption rules (percentage and amount caps).
    """
    user_email = current_user["email"]
    quote = await WalletService.calculate_discount_quote(
        user_email=user_email,
        item_id=payload.item_id,
        item_type=payload.item_type,
        coins_to_use=payload.coins_to_use
    )
    if not quote.get("is_eligible") and quote.get("ineligible_reason"):
        return quote
    return quote

@router.post("/reserve-discount")
async def reserve_discount(
    payload: ReserveDiscountRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Atomically reserves unexpired coins during course/workshop checkout.
    """
    user_email = current_user["email"]
    result = await WalletService.reserve_coins_for_order(
        user_email=user_email,
        item_id=payload.item_id,
        item_type=payload.item_type,
        coins_to_reserve=payload.coins_to_reserve,
        order_reference=payload.order_reference
    )
    if not result.get("reserved"):
        raise HTTPException(status_code=400, detail=result.get("reason", "Could not reserve coins"))
    return result
