import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from bson import ObjectId
from decimal import Decimal, ROUND_HALF_UP

from app.core.database import (
    user_collection,
    user_wallet_collection,
    coin_ledger_collection,
    coin_reward_lot_collection,
    coin_reservation_collection,
    referral_program_config_collection,
    course_collection
)

class WalletService:
    @classmethod
    async def get_or_create_wallet(cls, user_email: str) -> Dict[str, Any]:
        wallet = await user_wallet_collection.find_one({"email": user_email})
        if not wallet:
            new_wallet = {
                "email": user_email,
                "available_coins": 0,
                "pending_coins": 0,
                "reserved_coins": 0,
                "total_earned_coins": 0,
                "total_redeemed_coins": 0,
                "total_expired_coins": 0,
                "total_reversed_coins": 0,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            }
            await user_wallet_collection.insert_one(new_wallet)
            return new_wallet
        return wallet

    @classmethod
    async def get_wallet_summary(cls, user_email: str) -> Dict[str, Any]:
        wallet = await cls.get_or_create_wallet(user_email)
        
        # Load active valuation & expiry from config
        config = await referral_program_config_collection.find_one({"_id": "current_config"}) or {}
        valuation = config.get("coin_valuation", {"currency": "INR", "value_per_coin": 0.50})
        currency = valuation.get("currency", "INR")
        value_per_coin = Decimal(str(valuation.get("value_per_coin", 0.50)))
        
        available_coins = wallet.get("available_coins", 0)
        # Exact decimal arithmetic
        est_discount = (Decimal(available_coins) * value_per_coin).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        coin_val_str = str(value_per_coin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        
        # Expiry summary
        expiry_cfg = config.get("coin_expiry", {"enabled": True, "warning_days": 7})
        expiry_enabled = expiry_cfg.get("enabled", True)
        warning_days = expiry_cfg.get("warning_days", 7)
        
        now = datetime.now(timezone.utc)
        warning_threshold = now + timedelta(days=warning_days)
        
        # Query active lots for this user
        active_lots = await coin_reward_lot_collection.find({
            "email": user_email,
            "is_expired": False,
            "remaining_coins": {"$gt": 0}
        }).sort("expires_at", 1).to_list(length=100)
        
        next_expiry_at = None
        expiring_soon_coins = 0
        if active_lots:
            first_lot = active_lots[0]
            if first_lot.get("expires_at"):
                exp_date = first_lot["expires_at"]
                if exp_date.tzinfo is None:
                    exp_date = exp_date.replace(tzinfo=timezone.utc)
                next_expiry_at = exp_date.isoformat()
            
            for lot in active_lots:
                exp_date = lot.get("expires_at")
                if exp_date:
                    if exp_date.tzinfo is None:
                        exp_date = exp_date.replace(tzinfo=timezone.utc)
                    if exp_date <= warning_threshold:
                        expiring_soon_coins += lot.get("remaining_coins", 0)
        
        # Calculate referral rewards breakdown
        referrer_rewards_earned = wallet.get("total_referrer_rewards", 0)
        welcome_rewards_received = wallet.get("total_welcome_rewards", 0)
        
        return {
            "wallet": {
                "available_coins": available_coins,
                "pending_coins": wallet.get("pending_coins", 0),
                "reserved_coins": wallet.get("reserved_coins", 0),
                "total_earned_coins": wallet.get("total_earned_coins", 0),
                "total_redeemed_coins": wallet.get("total_redeemed_coins", 0),
                "total_expired_coins": wallet.get("total_expired_coins", 0),
                "total_reversed_coins": wallet.get("total_reversed_coins", 0),
                "coin_value": coin_val_str,
                "currency": currency,
                "estimated_discount_value": str(est_discount),
                "discount_label": "Estimated Course/Workshop Discount Value"
            },
            "referral_rewards": {
                "referrer_rewards_earned": referrer_rewards_earned,
                "welcome_rewards_received": welcome_rewards_received
            },
            "expiry": {
                "enabled": expiry_enabled,
                "next_expiry_at": next_expiry_at,
                "expiring_soon_coins": expiring_soon_coins,
                "expiring_soon_days": warning_days
            },
            "cash_withdrawal": {
                "enabled": False,
                "message": "Cash withdrawal, bank transfer, and cash conversion are strictly prohibited. Coins represent Course/Workshop discount value only."
            }
        }

    @classmethod
    async def credit_reward_lot(
        cls,
        user_email: str,
        coin_amount: int,
        source_type: str,
        reference_type: str,
        reference_id: str,
        description: str,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Credits coins to a user with an immutable ledger entry and an individual expiry lot.
        """
        if coin_amount <= 0:
            return {"credited": False, "reason": "Coin amount must be greater than zero"}

        if not config:
            config = await referral_program_config_collection.find_one({"_id": "current_config"}) or {}

        now = datetime.now(timezone.utc)
        
        # Calculate expiry timestamp
        expiry_cfg = config.get("coin_expiry", {})
        expiry_enabled = expiry_cfg.get("enabled", True)
        duration_value = expiry_cfg.get("duration_value", 90)
        duration_unit = expiry_cfg.get("duration_unit", "DAYS").upper()
        
        if not expiry_enabled:
            # 10 years fallback for non-expiring lots
            expires_at = now + timedelta(days=3650)
        elif duration_unit == "WEEKS":
            expires_at = now + timedelta(weeks=duration_value)
        elif duration_unit == "MONTHS":
            expires_at = now + timedelta(days=duration_value * 30)
        else:
            expires_at = now + timedelta(days=duration_value)

        # Snapshot of valuation
        valuation_cfg = config.get("coin_valuation", {"currency": "INR", "value_per_coin": 0.50})
        rate_per_coin = float(valuation_cfg.get("value_per_coin", 0.50))
        currency = valuation_cfg.get("currency", "INR")
        monetary_equivalent = float(Decimal(coin_amount) * Decimal(str(rate_per_coin)))

        # 1. Create Reward Lot
        lot_doc = {
            "email": user_email,
            "source_type": source_type,
            "reference_type": reference_type,
            "reference_id": reference_id,
            "initial_coins": coin_amount,
            "remaining_coins": coin_amount,
            "credited_at": now,
            "expires_at": expires_at,
            "is_expired": False,
            "status": "ACTIVE",
            "valuation_snapshot": {
                "rate_per_coin": rate_per_coin,
                "currency": currency,
                "monetary_equivalent": monetary_equivalent
            }
        }
        lot_result = await coin_reward_lot_collection.insert_one(lot_doc)
        lot_id = str(lot_result.inserted_id)

        # 2. Create Immutable Ledger Transaction
        txn_id = f"TXN_{source_type}_{int(now.timestamp())}_{ObjectId()}"
        ledger_entry = {
            "transaction_id": txn_id,
            "email": user_email,
            "transaction_type": source_type,
            "coin_amount": coin_amount,
            "direction": "CREDIT",
            "status": "COMPLETED",
            "reference_type": reference_type,
            "reference_id": reference_id,
            "reward_lot_id": lot_id,
            "valuation_snapshot": {
                "rate_per_coin": rate_per_coin,
                "currency": currency,
                "monetary_equivalent": monetary_equivalent
            },
            "expires_at": expires_at,
            "description": description,
            "created_at": now
        }
        await coin_ledger_collection.insert_one(ledger_entry)

        # 3. Update Wallet Balance atomically
        inc_fields = {
            "available_coins": coin_amount,
            "total_earned_coins": coin_amount
        }
        if source_type == "REFERRER_REFERRAL_REWARD":
            inc_fields["total_referrer_rewards"] = coin_amount
        elif source_type == "NEW_USER_WELCOME_REWARD":
            inc_fields["total_welcome_rewards"] = coin_amount

        await user_wallet_collection.update_one(
            {"email": user_email},
            {
                "$inc": inc_fields,
                "$set": {"updated_at": now},
                "$setOnInsert": {
                    "pending_coins": 0,
                    "reserved_coins": 0,
                    "total_redeemed_coins": 0,
                    "total_expired_coins": 0,
                    "total_reversed_coins": 0,
                    "created_at": now
                }
            },
            upsert=True
        )

        return {
            "credited": True,
            "lot_id": lot_id,
            "transaction_id": txn_id,
            "coins": coin_amount,
            "expires_at": expires_at
        }

    @classmethod
    async def process_expired_coins(cls) -> Dict[str, Any]:
        """
        Production-safe Idempotent Automatic Coin Expiry Worker.
        Expires eligible reward lots whose expires_at <= current_time and remaining_coins > 0.
        """
        now = datetime.now(timezone.utc)
        
        # Query unexpired lots that have reached their expiration date
        expired_candidates = await coin_reward_lot_collection.find({
            "is_expired": False,
            "remaining_coins": {"$gt": 0},
            "expires_at": {"$lte": now}
        }).to_list(length=500)

        total_lots_processed = 0
        total_coins_expired = 0
        user_expired_map: Dict[str, int] = {}

        for lot in expired_candidates:
            lot_id = lot["_id"]
            user_email = lot["email"]
            remaining = lot.get("remaining_coins", 0)
            if remaining <= 0:
                continue

            # Atomic check-and-update to prevent race conditions across multiple workers
            updated = await coin_reward_lot_collection.find_one_and_update(
                {
                    "_id": lot_id,
                    "is_expired": False,
                    "remaining_coins": remaining
                },
                {
                    "$set": {
                        "is_expired": True,
                        "remaining_coins": 0,
                        "expired_at": now,
                        "status": "EXPIRED"
                    }
                }
            )

            if not updated:
                # Lot was concurrently updated/spent by another worker
                continue

            total_lots_processed += 1
            total_coins_expired += remaining
            user_expired_map[user_email] = user_expired_map.get(user_email, 0) + remaining

            # Create immutable COIN_EXPIRY ledger record
            txn_id = f"TXN_EXPIRY_{int(now.timestamp())}_{ObjectId()}"
            ledger_entry = {
                "transaction_id": txn_id,
                "email": user_email,
                "transaction_type": "COIN_EXPIRY",
                "coin_amount": remaining,
                "direction": "DEBIT",
                "status": "COMPLETED",
                "reference_type": "coin_reward_lot",
                "reference_id": str(lot_id),
                "reward_lot_id": str(lot_id),
                "valuation_snapshot": lot.get("valuation_snapshot", {}),
                "description": f"Automated coin expiry for reward lot {lot_id}",
                "created_at": now
            }
            await coin_ledger_collection.insert_one(ledger_entry)

        # Batch update user wallet balances atomically
        for u_email, exp_amount in user_expired_map.items():
            await user_wallet_collection.update_one(
                {"email": u_email},
                {
                    "$inc": {
                        "available_coins": -exp_amount,
                        "total_expired_coins": exp_amount
                    },
                    "$set": {"updated_at": now}
                }
            )
            # Ensure available_coins never drops below 0
            await user_wallet_collection.update_one(
                {"email": u_email, "available_coins": {"$lt": 0}},
                {"$set": {"available_coins": 0}}
            )

        return {
            "processed_at": now.isoformat(),
            "lots_expired_count": total_lots_processed,
            "total_coins_expired": total_coins_expired,
            "users_affected_count": len(user_expired_map)
        }

    @classmethod
    async def calculate_discount_quote(
        cls,
        user_email: str,
        item_id: str,
        item_type: str,
        coins_to_use: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Authoritative server-side discount calculation for courses and workshops only.
        """
        # Validate item exists in course_collection
        try:
            obj_id = ObjectId(item_id)
        except Exception:
            return {"is_eligible": False, "ineligible_reason": "Invalid item ID format"}

        item = await course_collection.find_one({"_id": obj_id, "course_type": item_type})
        if not item:
            return {"is_eligible": False, "ineligible_reason": f"{item_type.capitalize()} not found"}

        original_price = float(item.get("price", 0.0))
        if original_price <= 0.0:
            return {
                "item_id": item_id,
                "item_type": item_type,
                "original_price": 0.0,
                "coins_requested": 0,
                "coins_applied": 0,
                "coin_value_rate": 0.0,
                "discount_amount": 0.0,
                "final_payable_amount": 0.0,
                "available_coins": 0,
                "is_eligible": False,
                "ineligible_reason": "Item is free or has zero price"
            }

        config = await referral_program_config_collection.find_one({"_id": "current_config"}) or {}
        rules = config.get("redemption_rules", {})
        valuation = config.get("coin_valuation", {"currency": "INR", "value_per_coin": 0.50})
        value_per_coin = Decimal(str(valuation.get("value_per_coin", 0.50)))

        # Check enabled switch
        if item_type == "course" and not rules.get("course_redemption_enabled", True):
            return {"is_eligible": False, "ineligible_reason": "Course coin redemption is disabled"}
        if item_type == "workshop" and not rules.get("workshop_redemption_enabled", True):
            return {"is_eligible": False, "ineligible_reason": "Workshop coin redemption is disabled"}

        # Item exclusions
        excluded = rules.get("excluded_item_ids", [])
        if item_id in excluded:
            return {"is_eligible": False, "ineligible_reason": "This item is excluded from coin discounts"}

        # Eligible item lists (if configured)
        if item_type == "course" and rules.get("eligible_course_ids"):
            if item_id not in rules["eligible_course_ids"]:
                return {"is_eligible": False, "ineligible_reason": "This course is not eligible for coin discounts"}
        if item_type == "workshop" and rules.get("eligible_workshop_ids"):
            if item_id not in rules["eligible_workshop_ids"]:
                return {"is_eligible": False, "ineligible_reason": "This workshop is not eligible for coin discounts"}

        # Minimum purchase amount
        min_purchase = float(rules.get("min_purchase_amount", 0.0))
        if original_price < min_purchase:
            return {"is_eligible": False, "ineligible_reason": f"Minimum purchase amount of ₹{min_purchase} required"}

        # User available coins
        wallet = await cls.get_or_create_wallet(user_email)
        available_coins = wallet.get("available_coins", 0)

        min_coins_req = rules.get("min_coins_required", 10)
        if available_coins < min_coins_req:
            return {
                "is_eligible": False,
                "available_coins": available_coins,
                "ineligible_reason": f"At least {min_coins_req} coins required to redeem discount"
            }

        # Calculate limits
        max_coins_per_order = rules.get("max_coins_per_order", 500)
        max_discount_pct = Decimal(str(rules.get("max_discount_percentage", 50.0)))
        max_discount_amt = Decimal(str(rules.get("max_discount_amount", 1000.0)))

        # Max discount by percentage
        price_dec = Decimal(str(original_price))
        max_discount_by_pct = (price_dec * (max_discount_pct / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        max_allowed_discount = min(max_discount_by_pct, max_discount_amt)

        # Max coins that can be absorbed by allowed discount
        max_coins_by_discount = int(max_allowed_discount / value_per_coin)

        effective_max_coins = min(available_coins, max_coins_per_order, max_coins_by_discount)

        if coins_to_use is not None:
            coins_requested = max(0, coins_to_use)
            coins_applied = min(coins_requested, effective_max_coins)
        else:
            coins_requested = effective_max_coins
            coins_applied = effective_max_coins

        discount_amount = float((Decimal(coins_applied) * value_per_coin).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        final_payable = max(0.0, float((price_dec - Decimal(str(discount_amount))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)))

        return {
            "item_id": item_id,
            "item_type": item_type,
            "original_price": original_price,
            "coins_requested": coins_requested,
            "coins_applied": coins_applied,
            "coin_value_rate": float(value_per_coin),
            "discount_amount": discount_amount,
            "final_payable_amount": final_payable,
            "available_coins": available_coins,
            "is_eligible": True,
            "ineligible_reason": None
        }

    @classmethod
    async def reserve_coins_for_order(
        cls,
        user_email: str,
        item_id: str,
        item_type: str,
        coins_to_reserve: int,
        order_reference: str
    ) -> Dict[str, Any]:
        """
        Atomically reserves unexpired coins during checkout.
        """
        if coins_to_reserve <= 0:
            return {"reserved": False, "reason": "Coins to reserve must be greater than zero"}

        now = datetime.now(timezone.utc)

        # Check existing reservation for this order
        existing = await coin_reservation_collection.find_one({"order_reference": order_reference})
        if existing:
            if existing.get("status") == "RESERVED":
                return {
                    "reserved": True,
                    "reservation_id": str(existing["_id"]),
                    "order_reference": order_reference,
                    "coins": existing.get("coins", 0),
                    "discount_amount": existing.get("discount_amount", 0.0),
                    "status": "RESERVED"
                }

        # Check quote eligibility
        quote = await cls.calculate_discount_quote(user_email, item_id, item_type, coins_to_reserve)
        if not quote.get("is_eligible"):
            return {"reserved": False, "reason": quote.get("ineligible_reason", "Not eligible for discount")}

        allowed_coins = quote.get("coins_applied", 0)
        if coins_to_reserve > allowed_coins:
            return {"reserved": False, "reason": f"Maximum allowable coins for this order is {allowed_coins}"}

        # Atomic reservation from user wallet
        res = await user_wallet_collection.find_one_and_update(
            {
                "email": user_email,
                "available_coins": {"$gte": coins_to_reserve}
            },
            {
                "$inc": {
                    "available_coins": -coins_to_reserve,
                    "reserved_coins": coins_to_reserve
                },
                "$set": {"updated_at": now}
            }
        )

        if not res:
            return {"reserved": False, "reason": "Insufficient available unreserved coins"}

        expires_at = now + timedelta(minutes=30)  # 30-minute checkout hold
        reservation_doc = {
            "email": user_email,
            "order_reference": order_reference,
            "item_id": item_id,
            "item_type": item_type,
            "coins": coins_to_reserve,
            "discount_amount": quote.get("discount_amount", 0.0),
            "status": "RESERVED",
            "created_at": now,
            "expires_at": expires_at
        }
        res_ins = await coin_reservation_collection.insert_one(reservation_doc)

        return {
            "reserved": True,
            "reservation_id": str(res_ins.inserted_id),
            "order_reference": order_reference,
            "coins": coins_to_reserve,
            "discount_amount": quote.get("discount_amount", 0.0),
            "status": "RESERVED",
            "expires_at": expires_at
        }

    @classmethod
    async def commit_reserved_coins(cls, order_reference: str) -> Dict[str, Any]:
        """
        Called when order payment succeeds: finalizes discount redemption.
        Consumes coins from active lots (FIFO order) and creates COURSE/WORKSHOP_DISCOUNT ledger transaction.
        """
        reservation = await coin_reservation_collection.find_one({"order_reference": order_reference})
        if not reservation:
            return {"committed": False, "reason": "Reservation not found"}

        if reservation.get("status") == "COMMITTED":
            return {"committed": True, "already_committed": True}

        if reservation.get("status") != "RESERVED":
            return {"committed": False, "reason": f"Invalid reservation status: {reservation.get('status')}"}

        user_email = reservation["email"]
        coins_to_spend = reservation["coins"]
        item_type = reservation.get("item_type", "course")
        now = datetime.now(timezone.utc)

        # FIFO lot consumption
        active_lots = await coin_reward_lot_collection.find({
            "email": user_email,
            "is_expired": False,
            "remaining_coins": {"$gt": 0}
        }).sort("expires_at", 1).to_list(length=100)

        remaining_to_deduct = coins_to_spend
        for lot in active_lots:
            if remaining_to_deduct <= 0:
                break
            lot_rem = lot.get("remaining_coins", 0)
            deduct_from_lot = min(lot_rem, remaining_to_deduct)
            new_rem = lot_rem - deduct_from_lot
            await coin_reward_lot_collection.update_one(
                {"_id": lot["_id"]},
                {"$set": {"remaining_coins": new_rem, "updated_at": now}}
            )
            remaining_to_deduct -= deduct_from_lot

        # Create Ledger Entry
        txn_type = "COURSE_DISCOUNT" if item_type == "course" else "WORKSHOP_DISCOUNT"
        txn_id = f"TXN_{txn_type}_{int(now.timestamp())}_{ObjectId()}"
        ledger_entry = {
            "transaction_id": txn_id,
            "email": user_email,
            "transaction_type": txn_type,
            "coin_amount": coins_to_spend,
            "direction": "DEBIT",
            "status": "COMPLETED",
            "reference_type": "order",
            "reference_id": order_reference,
            "discount_amount": reservation.get("discount_amount", 0.0),
            "description": f"Redeemed {coins_to_spend} coins for {item_type} discount on order {order_reference}",
            "created_at": now
        }
        await coin_ledger_collection.insert_one(ledger_entry)

        # Update wallet: move from reserved to redeemed
        await user_wallet_collection.update_one(
            {"email": user_email},
            {
                "$inc": {
                    "reserved_coins": -coins_to_spend,
                    "total_redeemed_coins": coins_to_spend
                },
                "$set": {"updated_at": now}
            }
        )

        await coin_reservation_collection.update_one(
            {"_id": reservation["_id"]},
            {"$set": {"status": "COMMITTED", "committed_at": now, "transaction_id": txn_id}}
        )

        return {"committed": True, "transaction_id": txn_id, "coins_redeemed": coins_to_spend}

    @classmethod
    async def release_reserved_coins(cls, order_reference: str) -> Dict[str, Any]:
        """
        Called when order is cancelled or payment fails: unlocks reserved coins back to user.
        """
        reservation = await coin_reservation_collection.find_one({"order_reference": order_reference})
        if not reservation or reservation.get("status") != "RESERVED":
            return {"released": False, "reason": "No active reservation to release"}

        user_email = reservation["email"]
        coins = reservation["coins"]
        now = datetime.now(timezone.utc)

        # Move back from reserved to available
        await user_wallet_collection.update_one(
            {"email": user_email},
            {
                "$inc": {
                    "reserved_coins": -coins,
                    "available_coins": coins
                },
                "$set": {"updated_at": now}
            }
        )

        await coin_reservation_collection.update_one(
            {"_id": reservation["_id"]},
            {"$set": {"status": "RELEASED", "released_at": now}}
        )

        return {"released": True, "coins_released": coins}
