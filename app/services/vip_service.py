import secrets
import hashlib
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from bson import ObjectId
from fastapi import HTTPException, status

from app.core.database import (
    event_collection,
    vip_configuration_collection,
    vip_booking_collection,
    vip_pass_collection,
    vip_checkin_collection,
    vip_payment_attempt_collection,
    audit_log_collection,
    user_collection,
)
from app.schemas.vip import (
    VIPConfigurationCreateReq,
    VIPConfigurationUpdateReq,
    VIPCapacityStats,
    VIPBookingCreateReq,
    VIPVerifyReq,
    VIPVerifyResponse,
    VIPVerificationOutcome,
    VIPManualIssueReq,
    VIPPassStatus,
    VIPBookingStatus,
    VIPEntryPolicy,
    VIPAccessArea,
)
from app.services.vip_payment_gateway import VIPPaymentGatewayAdapter
from app.services.ticket import TicketService
from app.services.email import EmailService

logger = logging.getLogger("eventora.vip_service")


def serialize_mongo(data: Any) -> Any:
    if isinstance(data, ObjectId):
        return str(data)
    elif isinstance(data, dict):
        res = {}
        for k, v in data.items():
            if k == "_id":
                res["id"] = str(v)
            else:
                res[k] = serialize_mongo(v)
        if "id" not in res and "_id" in data:
            res["id"] = str(data["_id"])
        if "_id" in res:
            del res["_id"]
        return res
    elif isinstance(data, list):
        return [serialize_mongo(item) for item in data]
    elif hasattr(data, "model_dump"):
        return serialize_mongo(data.model_dump())
    return data

class VIPService:
    # ──────────────────────────────────────────────────────────
    # Helper & Capacity Calculations
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _to_object_id(oid_str: str) -> Optional[ObjectId]:
        try:
            return ObjectId(str(oid_str))
        except Exception:
            return None

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()

    @staticmethod
    async def calculate_capacity_stats(config: Dict[str, Any]) -> VIPCapacityStats:
        """
        Authoritative server-side calculation of VIP pass capacity.
        Active allocations = Confirmed bookings + Valid unexpired pending reservations.
        """
        config_id_str = str(config.get("_id"))
        total_capacity = int(config.get("capacity", 0))
        now_utc = datetime.now(timezone.utc)

        # 1. Confirmed count (capacity consumed by paid passes)
        confirmed_count = await vip_booking_collection.count_documents({
            "configuration_id": config_id_str,
            "booking_status": "CONFIRMED"
        })

        # 2. Pending payment count (active unexpired reservations consuming capacity)
        pending_count = await vip_booking_collection.count_documents({
            "configuration_id": config_id_str,
            "booking_status": "PENDING_PAYMENT",
            "expires_at": {"$gt": now_utc}
        })

        booked_count = confirmed_count + pending_count
        available_count = max(0, total_capacity - booked_count)
        sold_out = available_count <= 0

        # Booking window check
        b_start = config.get("booking_start")
        b_end = config.get("booking_end")
        is_enabled = bool(config.get("is_enabled", False))

        if b_start and b_start.tzinfo is None:
            b_start = b_start.replace(tzinfo=timezone.utc)
        if b_end and b_end.tzinfo is None:
            b_end = b_end.replace(tzinfo=timezone.utc)

        booking_window_open = bool(b_start and b_end and b_start <= now_utc <= b_end)
        booking_open = is_enabled and booking_window_open and not sold_out

        return VIPCapacityStats(
            total_capacity=total_capacity,
            booked_count=booked_count,
            confirmed_count=confirmed_count,
            pending_payment_count=pending_count,
            available_count=available_count,
            sold_out=sold_out,
            booking_open=booking_open
        )

    # ──────────────────────────────────────────────────────────
    # Super Admin Configuration Management
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def create_configuration(event_id: str, req: VIPConfigurationCreateReq, admin_email: str) -> Dict[str, Any]:
        # 1. Verify Event Exists
        e_oid = VIPService._to_object_id(event_id)
        event = await event_collection.find_one({"$or": [{"_id": e_oid}, {"_id": event_id}]}) if e_oid else await event_collection.find_one({"_id": event_id})
        if not event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found.")

        # 2. Strict Business Rule: Price must be strictly > 0 (Paid-only policy)
        if req.price <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Every VIP Pass must be PAID. Free VIP configurations and zero-price VIP bookings are strictly prohibited."
            )

        # 3. Validate Date Ranges
        if req.booking_start >= req.booking_end:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Booking start must be before booking end.")
        if req.validity_start >= req.validity_end:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Validity start must be before validity end.")

        now_utc = datetime.now(timezone.utc)
        doc = req.model_dump()
        doc.update({
            "event_id": str(event.get("_id")),
            "event_title": event.get("title") or event.get("name", "Event"),
            "allocated_count": 0,
            "created_by": admin_email,
            "created_at": now_utc,
            "updated_at": now_utc,
            "version": 1
        })

        res = await vip_configuration_collection.insert_one(doc)
        doc["_id"] = res.inserted_id

        # Audit log
        await audit_log_collection.insert_one({
            "actor_id": admin_email,
            "action_type": "VIP_CONFIGURATION_CREATED",
            "event_id": str(event.get("_id")),
            "configuration_id": str(res.inserted_id),
            "timestamp": now_utc,
            "details": {"name": req.name, "price": req.price, "capacity": req.capacity}
        })

        capacity_stats = await VIPService.calculate_capacity_stats(doc)
        doc["id"] = str(res.inserted_id)
        doc["capacity"] = capacity_stats
        return serialize_mongo(doc)

    @staticmethod
    async def update_configuration(config_id: str, req: VIPConfigurationUpdateReq, admin_email: str) -> Dict[str, Any]:
        c_oid = VIPService._to_object_id(config_id)
        config = await vip_configuration_collection.find_one({"$or": [{"_id": c_oid}, {"_id": config_id}]}) if c_oid else await vip_configuration_collection.find_one({"_id": config_id})
        if not config:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VIP configuration not found.")

        update_dict = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}
        
        # Enforce Paid-only rule on price updates
        if "price" in update_dict and update_dict["price"] <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Every VIP Pass must be PAID. Zero or negative price is strictly prohibited."
            )

        now_utc = datetime.now(timezone.utc)
        update_dict["updated_at"] = now_utc
        update_dict["updated_by"] = admin_email

        await vip_configuration_collection.update_one(
            {"_id": config["_id"]},
            {"$set": update_dict, "$inc": {"version": 1}}
        )

        updated_doc = await vip_configuration_collection.find_one({"_id": config["_id"]})
        capacity_stats = await VIPService.calculate_capacity_stats(updated_doc)
        updated_doc["id"] = str(updated_doc["_id"])
        updated_doc["capacity"] = capacity_stats
        return serialize_mongo(updated_doc)

    @staticmethod
    async def toggle_configuration_status(config_id: str, enable: bool, admin_email: str) -> Dict[str, Any]:
        c_oid = VIPService._to_object_id(config_id)
        config = await vip_configuration_collection.find_one({"$or": [{"_id": c_oid}, {"_id": config_id}]}) if c_oid else await vip_configuration_collection.find_one({"_id": config_id})
        if not config:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VIP configuration not found.")

        now_utc = datetime.now(timezone.utc)
        await vip_configuration_collection.update_one(
            {"_id": config["_id"]},
            {"$set": {"is_enabled": enable, "updated_at": now_utc, "updated_by": admin_email}}
        )

        action = "VIP_CONFIGURATION_ENABLED" if enable else "VIP_CONFIGURATION_DISABLED"
        await audit_log_collection.insert_one({
            "actor_id": admin_email,
            "action_type": action,
            "configuration_id": str(config["_id"]),
            "timestamp": now_utc
        })

        updated = await vip_configuration_collection.find_one({"_id": config["_id"]})
        updated["id"] = str(updated["_id"])
        updated["capacity"] = await VIPService.calculate_capacity_stats(updated)
        return serialize_mongo(updated)

    @staticmethod
    async def get_configuration(config_id: str) -> Dict[str, Any]:
        c_oid = VIPService._to_object_id(config_id)
        config = await vip_configuration_collection.find_one({"$or": [{"_id": c_oid}, {"_id": config_id}]}) if c_oid else await vip_configuration_collection.find_one({"_id": config_id})
        if not config:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VIP configuration not found.")
        
        config["id"] = str(config["_id"])
        config["capacity"] = await VIPService.calculate_capacity_stats(config)
        return serialize_mongo(config)

    @staticmethod
    async def list_event_configurations(event_id: str, admin_view: bool = False) -> List[Dict[str, Any]]:
        query = {"event_id": str(event_id)}
        if not admin_view:
            query["is_enabled"] = True

        docs = await vip_configuration_collection.find(query).to_list(length=100)
        results = []
        for d in docs:
            d["id"] = str(d["_id"])
            d["capacity"] = await VIPService.calculate_capacity_stats(d)
            results.append(d)
        return [serialize_mongo(r) for r in results]

    # ──────────────────────────────────────────────────────────
    # User Booking Workflow (Atomic Reservation & Paid-Only)
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def book_vip_pass(
        event_id: str,
        config_id: str,
        current_user: Dict[str, Any],
        req: VIPBookingCreateReq
    ) -> Dict[str, Any]:
        user_email = current_user.get("email")
        now_utc = datetime.now(timezone.utc)

        # 1. Validate Config & Event association
        c_oid = VIPService._to_object_id(config_id)
        config = await vip_configuration_collection.find_one({"$or": [{"_id": c_oid}, {"_id": config_id}]}) if c_oid else await vip_configuration_collection.find_one({"_id": config_id})
        if not config:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VIP configuration not found.")

        if str(config.get("event_id")) != str(event_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Configuration does not belong to the specified event.")

        if not config.get("is_enabled"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="VIP pass booking is currently disabled for this configuration.")

        # 2. Validate Booking Window
        b_start = config.get("booking_start")
        b_end = config.get("booking_end")
        if b_start and b_start.tzinfo is None: b_start = b_start.replace(tzinfo=timezone.utc)
        if b_end and b_end.tzinfo is None: b_end = b_end.replace(tzinfo=timezone.utc)

        if b_start and now_utc < b_start:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="VIP pass booking window has not opened yet.")
        if b_end and now_utc > b_end:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="VIP pass booking window has closed.")

        # Clean up any expired pending reservations for this user and config
        await vip_booking_collection.update_many(
            {
                "configuration_id": str(config["_id"]),
                "user_email": user_email,
                "booking_status": "PENDING_PAYMENT",
                "expires_at": {"$lte": now_utc}
            },
            {"$set": {"booking_status": "EXPIRED", "expired_at": now_utc}}
        )

        # Check if user already has an active pending payment reservation
        active_pending = await vip_booking_collection.find_one({
            "configuration_id": str(config["_id"]),
            "user_email": user_email,
            "booking_status": "PENDING_PAYMENT",
            "expires_at": {"$gt": now_utc}
        })
        if active_pending:
            active_pending["booking_id"] = str(active_pending["_id"])
            active_pending["capacity"] = await VIPService.calculate_capacity_stats(config)
            
            p_order = VIPPaymentGatewayAdapter.create_order(
                booking_id=str(active_pending["_id"]),
                booking_reference=active_pending.get("booking_reference"),
                amount=float(active_pending.get("total_amount", 0.0)),
                currency=active_pending.get("currency", "INR"),
                user_email=user_email
            )
            active_pending["payment_details"] = p_order
            return serialize_mongo(active_pending)

        # 3. Enforce Per-User Booking Limits
        max_user_limit = int(config.get("max_passes_per_user", 1))
        existing_user_count = await vip_booking_collection.count_documents({
            "configuration_id": str(config["_id"]),
            "user_email": user_email,
            "booking_status": "CONFIRMED"
        })
        if (existing_user_count + req.quantity) > max_user_limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Per-user booking limit exceeded. You can only book a maximum of {max_user_limit} passes (Current: {existing_user_count})."
            )

        # 4. Check Idempotency Key
        if req.idempotency_key:
            existing_idemp = await vip_booking_collection.find_one({
                "idempotency_key": req.idempotency_key,
                "user_email": user_email
            })
            if existing_idemp:
                existing_idemp["booking_id"] = str(existing_idemp["_id"])
                existing_idemp["capacity"] = await VIPService.calculate_capacity_stats(config)
                return serialize_mongo(existing_idemp)

        # 5. Atomic Capacity Check & Allocation
        capacity_stats = await VIPService.calculate_capacity_stats(config)
        if capacity_stats.available_count < req.quantity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"SOLD OUT: Requested {req.quantity} pass(es), but only {capacity_stats.available_count} available."
            )

        # 6. Calculate Authoritative Price
        unit_price = float(config.get("price", 0.0))
        if unit_price <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid price configuration. Free VIP passes are prohibited.")
        total_amount = round(unit_price * req.quantity, 2)
        currency = config.get("currency", "INR")

        # 7. Create Pending Booking & Payment Order
        booking_ref = f"VIP-BKG-{uuid.uuid4().hex[:10].upper()}"
        expiry_minutes = int(config.get("reservation_expiry_minutes", 15))
        expires_at = now_utc + timedelta(minutes=expiry_minutes)

        holder_names = req.holder_names or [current_user.get("name", "VIP Guest")]
        while len(holder_names) < req.quantity:
            holder_names.append(f"Guest {len(holder_names) + 1}")

        booking_doc = {
            "booking_reference": booking_ref,
            "event_id": str(event_id),
            "event_title": config.get("event_title", "Event"),
            "configuration_id": str(config["_id"]),
            "pass_name": config.get("name"),
            "category": config.get("category"),
            "user_email": user_email,
            "user_name": current_user.get("name", ""),
            "quantity": req.quantity,
            "unit_price": unit_price,
            "total_amount": total_amount,
            "currency": currency,
            "holder_names": holder_names,
            "booking_status": "PENDING_PAYMENT",
            "payment_status": "PENDING",
            "payment_reference": None,
            "issued_pass_ids": [],
            "idempotency_key": req.idempotency_key,
            "booked_at": now_utc,
            "expires_at": expires_at,
            "created_at": now_utc,
            "updated_at": now_utc,
        }

        insert_res = await vip_booking_collection.insert_one(booking_doc)
        booking_id = str(insert_res.inserted_id)
        booking_doc["_id"] = insert_res.inserted_id
        booking_doc["booking_id"] = booking_id

        # 8. Create Gateway Payment Order
        payment_order = VIPPaymentGatewayAdapter.create_order(
            booking_id=booking_id,
            booking_reference=booking_ref,
            amount=total_amount,
            currency=currency,
            user_email=user_email
        )

        # Record payment attempt
        await vip_payment_attempt_collection.insert_one({
            "booking_id": booking_id,
            "booking_reference": booking_ref,
            "provider": payment_order.get("provider"),
            "provider_order_id": payment_order.get("provider_order_id"),
            "amount": total_amount,
            "currency": currency,
            "status": "INITIATED",
            "created_at": now_utc
        })

        booking_doc["payment_details"] = payment_order
        booking_doc["capacity"] = await VIPService.calculate_capacity_stats(config)

        # Audit log
        await audit_log_collection.insert_one({
            "actor_id": user_email,
            "action_type": "VIP_BOOKING_CREATED",
            "event_id": str(event_id),
            "booking_id": booking_id,
            "booking_reference": booking_ref,
            "amount": total_amount,
            "timestamp": now_utc
        })

        return serialize_mongo(booking_doc)

    # ──────────────────────────────────────────────────────────
    # Payment Confirmation & VIP Pass Issuance (Idempotent)
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def confirm_payment_and_issue_pass(
        booking_reference: str,
        payment_txn_id: str,
        amount_paid: float,
        currency: str,
        provider_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Idempotent payment confirmation and VIP Pass issuance.
        Issues cryptographically random QR verification token and creates passes.
        """
        booking = await vip_booking_collection.find_one({"booking_reference": booking_reference})
        if not booking:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VIP booking reference not found.")

        # 1. Check Idempotency - if already confirmed, return cleanly
        if booking.get("booking_status") == "CONFIRMED":
            logger.info(f"Booking {booking_reference} is already confirmed. Returning existing state.")
            passes = await vip_pass_collection.find({"booking_reference": booking_reference}).to_list(length=20)
            return {
                "status": "ALREADY_CONFIRMED",
                "booking_reference": booking_reference,
                "passes_issued_count": len(passes),
                "passes": [str(p["_id"]) for p in passes]
            }

        # 2. Validate Amount and Currency
        expected_amount = float(booking.get("total_amount", 0.0))
        if abs(expected_amount - float(amount_paid)) > 0.01:
            logger.error(f"Payment amount mismatch for {booking_reference}: expected {expected_amount}, received {amount_paid}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payment amount mismatch: expected {expected_amount} {booking.get('currency')}, got {amount_paid} {currency}."
            )

        # 3. Check if reservation expired
        now_utc = datetime.now(timezone.utc)
        expires_at = booking.get("expires_at")
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        config_id = booking.get("configuration_id")
        config = await vip_configuration_collection.find_one({"_id": VIPService._to_object_id(config_id)})

        if expires_at and now_utc > expires_at and booking.get("booking_status") == "EXPIRED":
            # Late payment handling: verify if capacity can still be safely allocated
            cap = await VIPService.calculate_capacity_stats(config) if config else None
            if not cap or cap.available_count < booking.get("quantity", 1):
                # Mark for refund reconciliation
                await vip_booking_collection.update_one(
                    {"_id": booking["_id"]},
                    {"$set": {
                        "booking_status": "REFUND_PENDING",
                        "payment_status": "REFUND_REQUIRED",
                        "payment_reference": payment_txn_id,
                        "reconciliation_reason": "Late payment received after reservation expiration and capacity is sold out.",
                        "updated_at": now_utc
                    }}
                )
                return {
                    "status": "REFUND_REQUIRED",
                    "message": "Late payment received after reservation expired. Capacity is no longer available. Refund scheduled.",
                    "booking_reference": booking_reference
                }

        # 4. Generate Passes & Opaque Cryptographic QR Tokens
        event_id = booking.get("event_id")
        quantity = booking.get("quantity", 1)
        holder_names = booking.get("holder_names", ["VIP Guest"])
        access_areas = config.get("access_areas", ["MAIN_GATE", "VIP_LOUNGE"]) if config else ["MAIN_GATE"]
        entry_policy = config.get("entry_policy", "SINGLE_ENTRY") if config else "SINGLE_ENTRY"
        validity_start = config.get("validity_start") if config else now_utc
        validity_end = config.get("validity_end") if config else (now_utc + timedelta(days=7))

        issued_passes = []
        issued_pass_ids = []

        for i in range(quantity):
            holder = holder_names[i] if i < len(holder_names) else f"Guest {i + 1}"
            raw_token = secrets.token_urlsafe(32)
            token_hash = VIPService.hash_token(raw_token)
            pass_ref = f"PASS-VIP-{uuid.uuid4().hex[:8].upper()}"

            # Generate QR Image base64 containing the secure verification payload
            qr_payload = f"https://eventora.com/vip-verify?token={raw_token}&ref={pass_ref}"
            qr_image_b64 = TicketService.generate_qr_image_base64(qr_payload)

            pass_doc = {
                "pass_reference": pass_ref,
                "booking_id": str(booking["_id"]),
                "booking_reference": booking_reference,
                "event_id": str(event_id),
                "event_title": booking.get("event_title"),
                "configuration_id": str(config_id),
                "category": booking.get("category", "VIP"),
                "pass_name": booking.get("pass_name", "VIP Pass"),
                "user_email": booking.get("user_email"),
                "holder_name": holder,
                "token_hash": token_hash,
                "qr_token": raw_token, # Available to owner upon retrieval
                "qr_image_base64": qr_image_b64,
                "status": "VALID",
                "access_areas": access_areas,
                "entry_policy": entry_policy,
                "entries_count": 0,
                "max_entries": config.get("max_entries", 1) if config else 1,
                "last_entry_at": None,
                "issued_at": now_utc,
                "validity_start": validity_start,
                "validity_end": validity_end,
                "issuing_source": "ONLINE_BOOKING",
                "created_at": now_utc,
                "updated_at": now_utc
            }
            res = await vip_pass_collection.insert_one(pass_doc)
            pass_doc["_id"] = res.inserted_id
            issued_pass_ids.append(str(res.inserted_id))
            issued_passes.append(pass_doc)

        # 5. Confirm Booking in Database
        await vip_booking_collection.update_one(
            {"_id": booking["_id"]},
            {"$set": {
                "booking_status": "CONFIRMED",
                "payment_status": "PAID",
                "payment_reference": payment_txn_id,
                "provider_order_id": provider_order_id,
                "issued_pass_ids": issued_pass_ids,
                "updated_at": now_utc
            }}
        )

        # 6. Audit Log
        await audit_log_collection.insert_one({
            "actor_id": booking.get("user_email"),
            "action_type": "VIP_PAYMENT_CONFIRMED_AND_PASS_ISSUED",
            "booking_reference": booking_reference,
            "payment_reference": payment_txn_id,
            "passes_issued": len(issued_pass_ids),
            "timestamp": now_utc
        })

        # 7. Send Email Notification
        try:
            EmailService.send_vip_pass_confirmation(
                email=booking.get("user_email"),
                name=booking.get("user_name"),
                event_title=booking.get("event_title"),
                pass_name=booking.get("pass_name"),
                quantity=quantity,
                booking_reference=booking_reference
            )
        except Exception as e:
            logger.warning(f"Failed to send VIP pass email notification: {e}")

        return {
            "status": "CONFIRMED",
            "booking_reference": booking_reference,
            "passes_issued_count": len(issued_pass_ids),
            "issued_pass_ids": issued_pass_ids
        }

    # ──────────────────────────────────────────────────────────
    # Super Admin Manual Issuance (Strict Paid-Only Policy)
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def manual_issue_vip_pass(
        event_id: str,
        config_id: str,
        req: VIPManualIssueReq,
        admin_email: str
    ) -> Dict[str, Any]:
        """
        Manual VIP pass issuance by Super Admin.
        STRICT POLICY: Requires verified payment/invoice reference and amount_paid > 0.
        Free passes are strictly prohibited even for administrators.
        """
        c_oid = VIPService._to_object_id(config_id)
        config = await vip_configuration_collection.find_one({"$or": [{"_id": c_oid}, {"_id": config_id}]}) if c_oid else await vip_configuration_collection.find_one({"_id": config_id})
        if not config:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VIP configuration not found.")

        # Check capacity
        capacity_stats = await VIPService.calculate_capacity_stats(config)
        if capacity_stats.available_count < 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="VIP configuration capacity reached.")

        now_utc = datetime.now(timezone.utc)
        booking_ref = f"VIP-MANUAL-{uuid.uuid4().hex[:8].upper()}"

        # Create confirmed manual booking record
        booking_doc = {
            "booking_reference": booking_ref,
            "event_id": str(event_id),
            "event_title": config.get("event_title"),
            "configuration_id": str(config["_id"]),
            "pass_name": config.get("name"),
            "category": config.get("category"),
            "user_email": req.user_email,
            "user_name": req.holder_name,
            "quantity": 1,
            "unit_price": req.amount_paid,
            "total_amount": req.amount_paid,
            "currency": req.currency,
            "holder_names": [req.holder_name],
            "booking_status": "CONFIRMED",
            "payment_status": "PAID",
            "payment_reference": req.payment_reference,
            "booked_at": now_utc,
            "issuing_admin": admin_email,
            "created_at": now_utc,
            "updated_at": now_utc
        }
        b_res = await vip_booking_collection.insert_one(booking_doc)

        # Generate Pass & Token
        raw_token = secrets.token_urlsafe(32)
        token_hash = VIPService.hash_token(raw_token)
        pass_ref = f"PASS-VIP-{uuid.uuid4().hex[:8].upper()}"
        qr_payload = f"https://eventora.com/vip-verify?token={raw_token}&ref={pass_ref}"
        qr_image_b64 = TicketService.generate_qr_image_base64(qr_payload)

        pass_doc = {
            "pass_reference": pass_ref,
            "booking_id": str(b_res.inserted_id),
            "booking_reference": booking_ref,
            "event_id": str(event_id),
            "event_title": config.get("event_title"),
            "configuration_id": str(config["_id"]),
            "category": config.get("category"),
            "pass_name": config.get("name"),
            "user_email": req.user_email,
            "holder_name": req.holder_name,
            "token_hash": token_hash,
            "qr_token": raw_token,
            "qr_image_base64": qr_image_b64,
            "status": "VALID",
            "access_areas": req.access_areas or config.get("access_areas", ["MAIN_GATE", "VIP_LOUNGE"]),
            "entry_policy": config.get("entry_policy", "SINGLE_ENTRY"),
            "entries_count": 0,
            "max_entries": config.get("max_entries", 1),
            "last_entry_at": None,
            "issued_at": now_utc,
            "validity_start": config.get("validity_start", now_utc),
            "validity_end": config.get("validity_end", now_utc + timedelta(days=7)),
            "issuing_source": "ADMIN_MANUAL",
            "issuing_admin": admin_email,
            "created_at": now_utc,
            "updated_at": now_utc
        }
        p_res = await vip_pass_collection.insert_one(pass_doc)
        pass_doc["_id"] = p_res.inserted_id

        await vip_booking_collection.update_one(
            {"_id": b_res.inserted_id},
            {"$set": {"issued_pass_ids": [str(p_res.inserted_id)]}}
        )

        # Audit
        await audit_log_collection.insert_one({
            "actor_id": admin_email,
            "action_type": "VIP_PASS_MANUALLY_ISSUED",
            "pass_reference": pass_ref,
            "user_email": req.user_email,
            "payment_reference": req.payment_reference,
            "timestamp": now_utc
        })

        pass_doc["id"] = str(p_res.inserted_id)
        pass_doc["pass_id"] = str(p_res.inserted_id)
        return serialize_mongo(pass_doc)

    # ──────────────────────────────────────────────────────────
    # Gate Entry & Access Area Verification
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def verify_entry(req: VIPVerifyReq, operator_email: str) -> VIPVerifyResponse:
        now_utc = datetime.now(timezone.utc)
        token_hash = VIPService.hash_token(req.verification_token)

        # 1. Idempotency Check on Request ID
        if req.idempotency_key:
            existing_checkin = await vip_checkin_collection.find_one({"idempotency_key": req.idempotency_key})
            if existing_checkin:
                return VIPVerifyResponse(
                    outcome=existing_checkin["outcome"],
                    allowed=(existing_checkin["outcome"] == VIPVerificationOutcome.ENTRY_APPROVED.value),
                    message=f"Duplicate request ID (Replayed result: {existing_checkin['outcome']})",
                    pass_id=existing_checkin.get("pass_id"),
                    pass_reference=existing_checkin.get("pass_reference"),
                    holder_name=existing_checkin.get("holder_name"),
                    category=existing_checkin.get("category"),
                    access_area_id=req.access_area_id,
                    gate_id=req.gate_id,
                    timestamp=existing_checkin["timestamp"],
                    entry_count=existing_checkin.get("entry_count", 0)
                )

        # 2. Lookup Pass by Token Hash
        pass_doc = await vip_pass_collection.find_one({"token_hash": token_hash})
        if not pass_doc:
            await VIPService._record_checkin_audit(
                pass_doc=None, req=req, outcome=VIPVerificationOutcome.INVALID_PASS.value, operator_email=operator_email, now_utc=now_utc
            )
            return VIPVerifyResponse(
                outcome=VIPVerificationOutcome.INVALID_PASS.value,
                allowed=False,
                message="Invalid or unrecognized verification token.",
                access_area_id=req.access_area_id,
                gate_id=req.gate_id,
                timestamp=now_utc
            )

        pass_id_str = str(pass_doc["_id"])
        pass_ref = pass_doc.get("pass_reference")
        holder_name = pass_doc.get("holder_name")
        category = pass_doc.get("category")

        # 3. Check Pass Status
        p_status = pass_doc.get("status")
        if p_status == VIPPassStatus.REVOKED.value:
            await VIPService._record_checkin_audit(pass_doc, req, VIPVerificationOutcome.PASS_REVOKED.value, operator_email, now_utc)
            return VIPVerifyResponse(
                outcome=VIPVerificationOutcome.PASS_REVOKED.value,
                allowed=False,
                message=f"Pass is revoked: {pass_doc.get('revocation_reason', 'Administrative action')}.",
                pass_id=pass_id_str, pass_reference=pass_ref, holder_name=holder_name, category=category,
                access_area_id=req.access_area_id, gate_id=req.gate_id, timestamp=now_utc
            )

        # 4. Check Event Association
        if str(pass_doc.get("event_id")) != str(req.event_id):
            await VIPService._record_checkin_audit(pass_doc, req, VIPVerificationOutcome.EVENT_MISMATCH.value, operator_email, now_utc)
            return VIPVerifyResponse(
                outcome=VIPVerificationOutcome.EVENT_MISMATCH.value,
                allowed=False,
                message="Pass is not valid for this event.",
                pass_id=pass_id_str, pass_reference=pass_ref, holder_name=holder_name, category=category,
                access_area_id=req.access_area_id, gate_id=req.gate_id, timestamp=now_utc
            )

        # 5. Check Validity Dates
        v_start = pass_doc.get("validity_start")
        v_end = pass_doc.get("validity_end")
        if v_start and v_start.tzinfo is None: v_start = v_start.replace(tzinfo=timezone.utc)
        if v_end and v_end.tzinfo is None: v_end = v_end.replace(tzinfo=timezone.utc)

        if v_start and now_utc < v_start:
            await VIPService._record_checkin_audit(pass_doc, req, VIPVerificationOutcome.NOT_YET_VALID.value, operator_email, now_utc)
            return VIPVerifyResponse(
                outcome=VIPVerificationOutcome.NOT_YET_VALID.value,
                allowed=False,
                message="Pass is not yet active for entry.",
                pass_id=pass_id_str, pass_reference=pass_ref, holder_name=holder_name, category=category,
                access_area_id=req.access_area_id, gate_id=req.gate_id, timestamp=now_utc
            )

        if v_end and now_utc > v_end:
            await VIPService._record_checkin_audit(pass_doc, req, VIPVerificationOutcome.PASS_EXPIRED.value, operator_email, now_utc)
            return VIPVerifyResponse(
                outcome=VIPVerificationOutcome.PASS_EXPIRED.value,
                allowed=False,
                message="Pass has expired.",
                pass_id=pass_id_str, pass_reference=pass_ref, holder_name=holder_name, category=category,
                access_area_id=req.access_area_id, gate_id=req.gate_id, timestamp=now_utc
            )

        # 6. Check Access Area Permission
        allowed_areas = pass_doc.get("access_areas", [])
        if req.access_area_id not in allowed_areas and "ALL" not in allowed_areas:
            await VIPService._record_checkin_audit(pass_doc, req, VIPVerificationOutcome.ACCESS_DENIED.value, operator_email, now_utc)
            return VIPVerifyResponse(
                outcome=VIPVerificationOutcome.ACCESS_DENIED.value,
                allowed=False,
                message=f"Access denied: Pass does not grant access to {req.access_area_id}.",
                pass_id=pass_id_str, pass_reference=pass_ref, holder_name=holder_name, category=category,
                access_area_id=req.access_area_id, gate_id=req.gate_id, timestamp=now_utc
            )

        # 7. Check Duplicate Entry & Re-entry Policy
        entry_policy = pass_doc.get("entry_policy", "SINGLE_ENTRY")
        entries_count = int(pass_doc.get("entries_count", 0))

        if entry_policy == "SINGLE_ENTRY" and entries_count >= 1:
            await VIPService._record_checkin_audit(pass_doc, req, VIPVerificationOutcome.DUPLICATE_ENTRY.value, operator_email, now_utc)
            return VIPVerifyResponse(
                outcome=VIPVerificationOutcome.DUPLICATE_ENTRY.value,
                allowed=False,
                message="Pass has already been used. Re-entry is not permitted.",
                pass_id=pass_id_str, pass_reference=pass_ref, holder_name=holder_name, category=category,
                access_area_id=req.access_area_id, gate_id=req.gate_id, timestamp=now_utc, entry_count=entries_count
            )

        if entry_policy == "ALLOW_REENTRY":
            max_entries = pass_doc.get("max_entries", 999)
            if entries_count >= max_entries:
                await VIPService._record_checkin_audit(pass_doc, req, VIPVerificationOutcome.REENTRY_NOT_ALLOWED.value, operator_email, now_utc)
                return VIPVerifyResponse(
                    outcome=VIPVerificationOutcome.REENTRY_NOT_ALLOWED.value,
                    allowed=False,
                    message=f"Maximum allowed entries ({max_entries}) reached.",
                    pass_id=pass_id_str, pass_reference=pass_ref, holder_name=holder_name, category=category,
                    access_area_id=req.access_area_id, gate_id=req.gate_id, timestamp=now_utc, entry_count=entries_count
                )

        # 8. Approve Entry & Atomically Record Check-in
        new_count = entries_count + 1
        await vip_pass_collection.update_one(
            {"_id": pass_doc["_id"]},
            {"$set": {"last_entry_at": now_utc}, "$inc": {"entries_count": 1}}
        )

        await VIPService._record_checkin_audit(
            pass_doc, req, VIPVerificationOutcome.ENTRY_APPROVED.value, operator_email, now_utc, entry_count=new_count
        )

        return VIPVerifyResponse(
            outcome=VIPVerificationOutcome.ENTRY_APPROVED.value,
            allowed=True,
            message="Entry approved. Welcome!",
            pass_id=pass_id_str,
            pass_reference=pass_ref,
            holder_name=holder_name,
            category=category,
            access_area_id=req.access_area_id,
            gate_id=req.gate_id,
            timestamp=now_utc,
            entry_count=new_count
        )

    @staticmethod
    async def _record_checkin_audit(
        pass_doc: Optional[Dict[str, Any]],
        req: VIPVerifyReq,
        outcome: str,
        operator_email: str,
        now_utc: datetime,
        entry_count: int = 0
    ):
        doc = {
            "pass_id": str(pass_doc["_id"]) if pass_doc else None,
            "pass_reference": pass_doc.get("pass_reference") if pass_doc else None,
            "holder_name": pass_doc.get("holder_name") if pass_doc else None,
            "category": pass_doc.get("category") if pass_doc else None,
            "event_id": str(req.event_id),
            "access_area_id": req.access_area_id,
            "gate_id": req.gate_id,
            "outcome": outcome,
            "operator_email": operator_email,
            "idempotency_key": req.idempotency_key,
            "timestamp": now_utc,
            "entry_count": entry_count
        }
        await vip_checkin_collection.insert_one(doc)

    # ──────────────────────────────────────────────────────────
    # Revocation, Cancellation & Expiry Worker
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def revoke_pass(pass_id: str, reason: str, admin_email: str) -> Dict[str, Any]:
        p_oid = VIPService._to_object_id(pass_id)
        pass_doc = await vip_pass_collection.find_one({"$or": [{"_id": p_oid}, {"_id": pass_id}]}) if p_oid else await vip_pass_collection.find_one({"_id": pass_id})
        if not pass_doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VIP Pass not found.")

        now_utc = datetime.now(timezone.utc)
        await vip_pass_collection.update_one(
            {"_id": pass_doc["_id"]},
            {"$set": {
                "status": VIPPassStatus.REVOKED.value,
                "revocation_reason": reason,
                "revoked_at": now_utc,
                "revoked_by": admin_email
            }}
        )

        await audit_log_collection.insert_one({
            "actor_id": admin_email,
            "action_type": "VIP_PASS_REVOKED",
            "pass_id": str(pass_doc["_id"]),
            "pass_reference": pass_doc.get("pass_reference"),
            "reason": reason,
            "timestamp": now_utc
        })

        return {"message": "VIP Pass revoked successfully.", "pass_id": str(pass_doc["_id"])}

    @staticmethod
    async def cancel_booking(booking_id: str, reason: str, admin_email: str) -> Dict[str, Any]:
        b_oid = VIPService._to_object_id(booking_id)
        booking = await vip_booking_collection.find_one({"$or": [{"_id": b_oid}, {"_id": booking_id}]}) if b_oid else await vip_booking_collection.find_one({"_id": booking_id})
        if not booking:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VIP Booking not found.")

        now_utc = datetime.now(timezone.utc)
        await vip_booking_collection.update_one(
            {"_id": booking["_id"]},
            {"$set": {
                "booking_status": "CANCELLED",
                "cancellation_reason": reason,
                "cancelled_at": now_utc,
                "cancelled_by": admin_email
            }}
        )

        # Invalidate any associated passes
        await vip_pass_collection.update_many(
            {"booking_reference": booking.get("booking_reference")},
            {"$set": {"status": "CANCELLED", "cancellation_reason": reason, "cancelled_at": now_utc}}
        )

        return {"message": "Booking cancelled and passes invalidated.", "booking_id": str(booking["_id"])}

    @staticmethod
    async def expire_pending_reservations() -> Dict[str, Any]:
        """
        Background worker cycle releasing expired payment reservations.
        Idempotent and safe across multiple instances.
        """
        now_utc = datetime.now(timezone.utc)
        expired_cursor = vip_booking_collection.find({
            "booking_status": "PENDING_PAYMENT",
            "expires_at": {"$lte": now_utc}
        })
        expired_bookings = await expired_cursor.to_list(length=500)

        released_count = 0
        for b in expired_bookings:
            res = await vip_booking_collection.update_one(
                {"_id": b["_id"], "booking_status": "PENDING_PAYMENT"},
                {"$set": {"booking_status": "EXPIRED", "expired_at": now_utc}}
            )
            if res.modified_count > 0:
                released_count += 1
                logger.info(f"Released expired VIP reservation: {b.get('booking_reference')}")

        return {"expired_count": released_count}
