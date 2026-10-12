import logging
import uuid
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from fastapi import HTTPException, status

from app.core.database import (
    merchandise_product_collection,
    merchandise_order_collection,
    merchandise_inventory_movement_collection,
    merchandise_payment_attempt_collection,
    merchandise_refund_collection,
    event_collection,
    audit_log_collection
)
from app.schemas.merchandise import (
    ProductCreateReq,
    ProductUpdateReq,
    ProductVariant,
    ProductStatus,
    OrderStatus,
    PaymentStatus,
    InventoryMovementType,
    OrderCreateReq,
    OrderFulfillmentUpdateReq,
    StockAdjustmentReq,
    MerchandiseCategory
)
from app.services.merchandise_payment_gateway import MerchandisePaymentGatewayAdapter
from app.services.email import EmailService

logger = logging.getLogger("eventora.merchandise_service")

def serialize_mongo(data: Any) -> Any:
    """Recursively converts MongoDB ObjectIds and Pydantic models into clean JSON-serializable types."""
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

class MerchandiseService:

    @staticmethod
    def _to_object_id(oid_str: str) -> Optional[ObjectId]:
        try:
            return ObjectId(str(oid_str))
        except Exception:
            return None

    # ──────────────────────────────────────────────────────────
    # Super Admin Product Management
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def create_product(req: ProductCreateReq, admin_email: str) -> Dict[str, Any]:
        # 1. Enforce Strictly Paid-Only Policy
        if req.price <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Every merchandise product must have a strictly positive price (> 0). Free merchandise is strictly prohibited."
            )

        # 2. Validate Variant Prices & Stock
        for v in req.variants:
            if v.price_override is not None and v.price_override <= 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Variant price override for SKU '{v.sku}' must be strictly positive (> 0)."
                )

        # 3. Generate URL-friendly slug if not provided
        base_slug = req.slug or re.sub(r"[^a-z0-9]+", "-", req.name.lower()).strip("-")
        slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"

        now_utc = datetime.now(timezone.utc)
        doc = req.model_dump()

        # If variants exist, calculate aggregate stock
        if req.variants:
            variant_stock_sum = sum(v.stock_quantity for v in req.variants)
            doc["stock_quantity"] = variant_stock_sum
        doc["reserved_quantity"] = 0

        sku = req.sku or f"MCH-{uuid.uuid4().hex[:8].upper()}"
        doc.update({
            "slug": slug,
            "sku": sku,
            "created_by": admin_email,
            "created_at": now_utc,
            "updated_at": now_utc,
            "version": 1
        })

        res = await merchandise_product_collection.insert_one(doc)
        doc["id"] = str(res.inserted_id)

        # Record Initial Stock Movement
        await merchandise_inventory_movement_collection.insert_one({
            "product_id": str(res.inserted_id),
            "movement_type": InventoryMovementType.INITIAL_STOCK.value,
            "quantity_delta": doc["stock_quantity"],
            "previous_quantity": 0,
            "new_quantity": doc["stock_quantity"],
            "actor": admin_email,
            "reason": "Initial product creation",
            "timestamp": now_utc
        })

        # Audit log
        await audit_log_collection.insert_one({
            "actor_id": admin_email,
            "action_type": "MERCHANDISE_PRODUCT_CREATED",
            "product_id": str(res.inserted_id),
            "timestamp": now_utc,
            "details": {"name": req.name, "price": req.price, "stock": doc["stock_quantity"]}
        })

        return serialize_mongo(doc)

    @staticmethod
    async def update_product(product_id: str, req: ProductUpdateReq, admin_email: str) -> Dict[str, Any]:
        p_oid = MerchandiseService._to_object_id(product_id)
        product = await merchandise_product_collection.find_one({"$or": [{"_id": p_oid}, {"_id": product_id}]}) if p_oid else await merchandise_product_collection.find_one({"_id": product_id})
        if not product:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchandise product not found.")

        update_dict = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}

        # Strict validation
        if "price" in update_dict and update_dict["price"] <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Price must be strictly positive (> 0). Free merchandise is prohibited."
            )

        now_utc = datetime.now(timezone.utc)
        update_dict["updated_at"] = now_utc
        update_dict["updated_by"] = admin_email

        await merchandise_product_collection.update_one(
            {"_id": product["_id"]},
            {"$set": update_dict, "$inc": {"version": 1}}
        )

        updated = await merchandise_product_collection.find_one({"_id": product["_id"]})
        return serialize_mongo(updated)

    @staticmethod
    async def set_product_status(product_id: str, new_status: ProductStatus, admin_email: str) -> Dict[str, Any]:
        p_oid = MerchandiseService._to_object_id(product_id)
        product = await merchandise_product_collection.find_one({"$or": [{"_id": p_oid}, {"_id": product_id}]}) if p_oid else await merchandise_product_collection.find_one({"_id": product_id})
        if not product:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchandise product not found.")

        now_utc = datetime.now(timezone.utc)
        await merchandise_product_collection.update_one(
            {"_id": product["_id"]},
            {"$set": {"status": new_status.value, "updated_at": now_utc, "updated_by": admin_email}}
        )
        updated = await merchandise_product_collection.find_one({"_id": product["_id"]})
        return serialize_mongo(updated)

    @staticmethod
    async def get_product(product_id_or_slug: str, admin_view: bool = False) -> Dict[str, Any]:
        p_oid = MerchandiseService._to_object_id(product_id_or_slug)
        query = {"$or": [{"_id": p_oid}, {"_id": product_id_or_slug}, {"slug": product_id_or_slug}]} if p_oid else {"$or": [{"_id": product_id_or_slug}, {"slug": product_id_or_slug}]}
        product = await merchandise_product_collection.find_one(query)
        if not product:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

        if not admin_view and product.get("status") != ProductStatus.ACTIVE.value:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product is not currently available.")

        return serialize_mongo(product)

    @staticmethod
    async def list_products(
        admin_view: bool = False,
        category: Optional[str] = None,
        event_id: Optional[str] = None,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ) -> Dict[str, Any]:
        query: Dict[str, Any] = {}
        if not admin_view:
            query["status"] = ProductStatus.ACTIVE.value
        elif status_filter:
            query["status"] = status_filter

        if category:
            query["category"] = category
        if event_id:
            query["event_id"] = str(event_id)
        if search:
            regex = {"$regex": re.escape(search), "$options": "i"}
            query["$or"] = [{"name": regex}, {"description": regex}, {"sku": regex}]

        total = await merchandise_product_collection.count_documents(query)
        cursor = merchandise_product_collection.find(query).sort("created_at", -1).skip(skip).limit(limit)
        products = await cursor.to_list(length=limit)

        return serialize_mongo({
            "total": total,
            "skip": skip,
            "limit": limit,
            "products": products
        })

    # ──────────────────────────────────────────────────────────
    # User Order Creation & Atomic Stock Reservation
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def create_order(
        current_user: Dict[str, Any],
        req: OrderCreateReq
    ) -> Dict[str, Any]:
        user_email = current_user.get("email")
        user_name = current_user.get("name") or "Valued Customer"
        now_utc = datetime.now(timezone.utc)

        # 1. Idempotency Check
        if req.idempotency_key:
            existing_order = await merchandise_order_collection.find_one({
                "idempotency_key": req.idempotency_key,
                "user_email": user_email
            })
            if existing_order:
                return serialize_mongo(existing_order)

        # 2. Authoritative Price & Product Lookup
        item_snapshots = []
        subtotal = 0.0
        max_shipping_fee = 0.0
        currency = "INR"

        # Validate every item against DB
        for item_req in req.items:
            p_oid = MerchandiseService._to_object_id(item_req.product_id)
            product = await merchandise_product_collection.find_one({"$or": [{"_id": p_oid}, {"_id": item_req.product_id}]}) if p_oid else await merchandise_product_collection.find_one({"_id": item_req.product_id})
            if not product:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Product '{item_req.product_id}' not found.")

            if product.get("status") != ProductStatus.ACTIVE.value:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Product '{product.get('name')}' is currently not available for purchase.")

            currency = product.get("currency", "INR")
            item_price = float(product.get("price", 0.0))
            item_sku = product.get("sku", "SKU")
            variant_snapshot = None

            # Handle Variants if specified
            if item_req.variant_id:
                matching_variant = next((v for v in product.get("variants", []) if v.get("variant_id") == item_req.variant_id), None)
                if not matching_variant:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Variant '{item_req.variant_id}' not found for product '{product.get('name')}'.")
                if not matching_variant.get("is_active", True):
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Selected variant is inactive.")

                if matching_variant.get("price_override") is not None:
                    item_price = float(matching_variant.get("price_override"))
                item_sku = matching_variant.get("sku", item_sku)
                variant_snapshot = {
                    "variant_id": matching_variant.get("variant_id"),
                    "size": matching_variant.get("size"),
                    "color": matching_variant.get("color"),
                    "sku": item_sku
                }

                # Check variant availability
                avail_variant_stock = matching_variant.get("stock_quantity", 0) - matching_variant.get("reserved_quantity", 0)
                if avail_variant_stock < item_req.quantity:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"SOLD OUT / Insufficient stock for variant '{item_sku}'. Available: {max(0, avail_variant_stock)}, Requested: {item_req.quantity}."
                    )
            else:
                # Check simple product availability
                avail_prod_stock = product.get("stock_quantity", 0) - product.get("reserved_quantity", 0)
                if avail_prod_stock < item_req.quantity:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"SOLD OUT / Insufficient stock for '{product.get('name')}'. Available: {max(0, avail_prod_stock)}, Requested: {item_req.quantity}."
                    )

            if item_price <= 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Authoritative price for '{product.get('name')}' is zero or invalid.")

            line_subtotal = round(item_price * item_req.quantity, 2)
            subtotal += line_subtotal
            if product.get("shipping_fee", 0.0) > max_shipping_fee:
                max_shipping_fee = float(product.get("shipping_fee", 0.0))

            item_snapshots.append({
                "product_id": str(product["_id"]),
                "product_name": product.get("name"),
                "sku": item_sku,
                "variant": variant_snapshot,
                "unit_price": item_price,
                "quantity": item_req.quantity,
                "subtotal": line_subtotal,
                "images": product.get("images", [])[:1]
            })

        total_amount = round(subtotal + max_shipping_fee, 2)

        # Strict Business Rule: Total must be strictly positive
        if total_amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Every merchandise order must have a strictly positive total (> 0). Free merchandise purchases are strictly prohibited."
            )

        # 3. Concurrency-Safe Atomic Stock Reservation
        reserved_items = []
        try:
            for item in req.items:
                p_oid = MerchandiseService._to_object_id(item.product_id)
                filter_q: Dict[str, Any] = {"_id": p_oid} if p_oid else {"_id": item.product_id}

                if item.variant_id:
                    filter_q["variants.variant_id"] = item.variant_id
                    res = await merchandise_product_collection.update_one(
                        filter_q,
                        {"$inc": {
                            "variants.$.reserved_quantity": item.quantity,
                            "reserved_quantity": item.quantity
                        }}
                    )
                else:
                    res = await merchandise_product_collection.update_one(
                        filter_q,
                        {"$inc": {"reserved_quantity": item.quantity}}
                    )

                if res.modified_count == 0:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Could not reserve stock atomically due to concurrent order.")
                reserved_items.append(item)
        except Exception as e:
            # Rollback any reserved items in this order
            for rev_item in reserved_items:
                r_oid = MerchandiseService._to_object_id(rev_item.product_id)
                r_filter: Dict[str, Any] = {"_id": r_oid} if r_oid else {"_id": rev_item.product_id}
                if rev_item.variant_id:
                    r_filter["variants.variant_id"] = rev_item.variant_id
                    await merchandise_product_collection.update_one(
                        r_filter,
                        {"$inc": {
                            "variants.$.reserved_quantity": -rev_item.quantity,
                            "reserved_quantity": -rev_item.quantity
                        }}
                    )
                else:
                    await merchandise_product_collection.update_one(
                        r_filter,
                        {"$inc": {"reserved_quantity": -rev_item.quantity}}
                    )
            raise e

        # 4. Create Pending Order Document
        order_number = f"MCH-ORD-{uuid.uuid4().hex[:10].upper()}"
        expires_at = now_utc + timedelta(minutes=15)

        order_doc = {
            "order_number": order_number,
            "user_email": user_email,
            "user_name": user_name,
            "items": item_snapshots,
            "subtotal": subtotal,
            "shipping_fee": max_shipping_fee,
            "discount_amount": 0.0,
            "total_amount": total_amount,
            "currency": currency,
            "shipping_address": req.shipping_address.model_dump(),
            "status": OrderStatus.PENDING_PAYMENT.value,
            "payment_status": PaymentStatus.PENDING.value,
            "payment_reference": None,
            "idempotency_key": req.idempotency_key,
            "status_history": [
                {
                    "status": OrderStatus.PENDING_PAYMENT.value,
                    "timestamp": now_utc,
                    "actor": user_email,
                    "notes": "Order placed, awaiting payment confirmation."
                }
            ],
            "expires_at": expires_at,
            "created_at": now_utc,
            "updated_at": now_utc
        }

        insert_res = await merchandise_order_collection.insert_one(order_doc)
        order_doc["id"] = str(insert_res.inserted_id)

        # Record Inventory Reservation Movements
        for it in item_snapshots:
            await merchandise_inventory_movement_collection.insert_one({
                "product_id": it["product_id"],
                "variant_id": it["variant"]["variant_id"] if it.get("variant") else None,
                "movement_type": InventoryMovementType.STOCK_RESERVED.value,
                "quantity_delta": it["quantity"],
                "related_order_number": order_number,
                "actor": user_email,
                "reason": "Stock reserved for pending checkout",
                "timestamp": now_utc
            })

        # 5. Create Payment Gateway Order
        payment_order = MerchandisePaymentGatewayAdapter.create_order(
            order_id=str(insert_res.inserted_id),
            order_number=order_number,
            amount=total_amount,
            currency=currency,
            user_email=user_email
        )

        await merchandise_payment_attempt_collection.insert_one({
            "order_id": str(insert_res.inserted_id),
            "order_number": order_number,
            "provider": payment_order.get("provider"),
            "provider_order_id": payment_order.get("provider_order_id"),
            "amount": total_amount,
            "currency": currency,
            "status": "INITIATED",
            "created_at": now_utc
        })

        order_doc["payment_details"] = payment_order
        return serialize_mongo(order_doc)

    # ──────────────────────────────────────────────────────────
    # Authoritative Payment Confirmation & Stock Commitment
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def confirm_payment_and_complete_order(
        order_number: str,
        payment_txn_id: str,
        amount_paid: float,
        currency: str,
        provider_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        order = await merchandise_order_collection.find_one({"order_number": order_number})
        if not order:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchandise order not found.")

        # 1. Idempotency Check
        if order.get("status") == OrderStatus.CONFIRMED.value and order.get("payment_status") == PaymentStatus.PAID.value:
            logger.info(f"Order {order_number} is already confirmed. Returning idempotently.")
            return serialize_mongo({
                "status": "ALREADY_CONFIRMED",
                "order_number": order_number,
                "payment_status": "PAID"
            })

        # 2. Strict Amount & Currency Verification
        expected_amount = float(order.get("total_amount", 0.0))
        if abs(expected_amount - float(amount_paid)) > 0.01:
            logger.error(f"Payment amount mismatch for {order_number}: expected {expected_amount}, got {amount_paid}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Payment amount mismatch: expected {expected_amount} {order.get('currency')}, got {amount_paid} {currency}."
            )

        now_utc = datetime.now(timezone.utc)

        # 3. Commit Reserved Stock to Sold Physical Stock
        for it in order.get("items", []):
            p_oid = MerchandiseService._to_object_id(it["product_id"])
            p_filter: Dict[str, Any] = {"_id": p_oid} if p_oid else {"_id": it["product_id"]}
            qty = it.get("quantity", 1)

            if it.get("variant") and it["variant"].get("variant_id"):
                p_filter["variants.variant_id"] = it["variant"]["variant_id"]
                await merchandise_product_collection.update_one(
                    p_filter,
                    {"$inc": {
                        "variants.$.stock_quantity": -qty,
                        "variants.$.reserved_quantity": -qty,
                        "stock_quantity": -qty,
                        "reserved_quantity": -qty
                    }}
                )
            else:
                await merchandise_product_collection.update_one(
                    p_filter,
                    {"$inc": {
                        "stock_quantity": -qty,
                        "reserved_quantity": -qty
                    }}
                )

            # Record Sale Committed Movement
            await merchandise_inventory_movement_collection.insert_one({
                "product_id": it["product_id"],
                "variant_id": it["variant"]["variant_id"] if it.get("variant") else None,
                "movement_type": InventoryMovementType.SALE_COMMITTED.value,
                "quantity_delta": -qty,
                "related_order_number": order_number,
                "actor": "PAYMENT_GATEWAY",
                "reason": "Payment verified. Stock committed to completed sale.",
                "timestamp": now_utc
            })

        # 4. Mark Order Confirmed & Paid
        new_history_entry = {
            "status": OrderStatus.CONFIRMED.value,
            "timestamp": now_utc,
            "actor": "PAYMENT_GATEWAY",
            "notes": f"Payment verified successfully with reference {payment_txn_id}."
        }

        await merchandise_order_collection.update_one(
            {"_id": order["_id"]},
            {
                "$set": {
                    "status": OrderStatus.CONFIRMED.value,
                    "payment_status": PaymentStatus.PAID.value,
                    "payment_reference": payment_txn_id,
                    "provider_order_id": provider_order_id,
                    "paid_at": now_utc,
                    "updated_at": now_utc
                },
                "$push": {"status_history": new_history_entry}
            }
        )

        # Authoritative trigger: ORDER_UPDATE (Confirmed)
        try:
            from app.services.notification_service import NotificationService
            from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
            from app.schemas.notification import NotificationCreate

            await NotificationService.create_notification(NotificationCreate(
                recipient_user_id=order.get("user_email"),
                notification_type=NotificationType.ORDER_UPDATE,
                title="Order Confirmed",
                message=f"Your merchandise order #{order_number} has been confirmed.",
                resource_type="merchandise_order",
                resource_id=order_number,
                action_url=f"/merchandise/orders/{order_number}",
                priority=NotificationPriority.NORMAL,
                sound_category=SoundCategory.SUCCESS,
                deduplication_key=f"order_conf_{order_number}",
                source_module="merchandise",
                source_event_id=order_number,
            ))
        except Exception:
            pass


        # 5. Send Order Confirmation Email
        try:
            EmailService.send_merchandise_order_confirmation(
                email=order.get("user_email"),
                name=order.get("user_name"),
                order_number=order_number,
                total_amount=expected_amount,
                currency=order.get("currency", "INR"),
                items_count=sum(i.get("quantity", 1) for i in order.get("items", []))
            )
        except Exception as e:
            logger.warning(f"Failed to send merchandise order email: {e}")

        return serialize_mongo({
            "status": "SUCCESS",
            "order_number": order_number,
            "payment_reference": payment_txn_id,
            "total_amount": expected_amount,
            "currency": currency
        })

    # ──────────────────────────────────────────────────────────
    # Super Admin Order Fulfillment & Status Management
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def update_fulfillment_status(
        order_id: str,
        req: OrderFulfillmentUpdateReq,
        admin_email: str
    ) -> Dict[str, Any]:
        o_oid = MerchandiseService._to_object_id(order_id)
        order = await merchandise_order_collection.find_one({"$or": [{"_id": o_oid}, {"_id": order_id}, {"order_number": order_id}]}) if o_oid else await merchandise_order_collection.find_one({"$or": [{"_id": order_id}, {"order_number": order_id}]})
        if not order:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merchandise order not found.")

        # Payment bypass prevention: Cannot advance an unpaid order to fulfillment statuses
        if order.get("payment_status") != PaymentStatus.PAID.value and req.status in [OrderStatus.PACKED, OrderStatus.SHIPPED, OrderStatus.DELIVERED]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot advance unverified or unpaid order to fulfillment statuses."
            )

        now_utc = datetime.now(timezone.utc)
        update_fields: Dict[str, Any] = {
            "status": req.status.value,
            "updated_at": now_utc,
            "updated_by": admin_email
        }
        if req.tracking_number:
            update_fields["tracking_number"] = req.tracking_number
        if req.courier_name:
            update_fields["courier_name"] = req.courier_name

        history_entry = {
            "status": req.status.value,
            "timestamp": now_utc,
            "actor": admin_email,
            "notes": req.notes or f"Status updated to {req.status.value}."
        }

        await merchandise_order_collection.update_one(
            {"_id": order["_id"]},
            {"$set": update_fields, "$push": {"status_history": history_entry}}
        )

        # Notify user on shipping update
        if req.status in [OrderStatus.SHIPPED, OrderStatus.DELIVERED]:
            try:
                EmailService.send_merchandise_fulfillment_update(
                    email=order.get("user_email"),
                    name=order.get("user_name"),
                    order_number=order.get("order_number"),
                    status=req.status.value,
                    courier_name=req.courier_name or order.get("courier_name"),
                    tracking_number=req.tracking_number or order.get("tracking_number")
                )
            except Exception as e:
                logger.warning(f"Failed to send fulfillment email: {e}")

        # Authoritative trigger: ORDER_UPDATE (Status transition)
        try:
            from app.services.notification_service import NotificationService
            from app.core.notification_types import NotificationType, NotificationPriority, SoundCategory
            from app.schemas.notification import NotificationCreate

            order_num = order.get("order_number")
            tracking_info = f" (Tracking: {req.tracking_number})" if req.tracking_number else ""
            await NotificationService.create_notification(NotificationCreate(
                recipient_user_id=order.get("user_email"),
                notification_type=NotificationType.ORDER_UPDATE,
                title=f"Order Status: {req.status.value}",
                message=f"Your order #{order_num} has been updated to {req.status.value}{tracking_info}.",
                resource_type="merchandise_order",
                resource_id=order_num,
                action_url=f"/merchandise/orders/{order_num}",
                priority=NotificationPriority.NORMAL,
                sound_category=SoundCategory.DEFAULT,
                deduplication_key=f"order_{order_num}_{req.status.value}",
                source_module="merchandise",
                source_event_id=order_num,
            ))
        except Exception:
            pass

        updated = await merchandise_order_collection.find_one({"_id": order["_id"]})
        return serialize_mongo(updated)


    # ──────────────────────────────────────────────────────────
    # Order Cancellation & Expiry Handlers
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def cancel_order(
        order_id: str,
        user_email: str,
        reason: str,
        admin_view: bool = False
    ) -> Dict[str, Any]:
        o_oid = MerchandiseService._to_object_id(order_id)
        query = {"$or": [{"_id": o_oid}, {"_id": order_id}, {"order_number": order_id}]} if o_oid else {"$or": [{"_id": order_id}, {"order_number": order_id}]}
        if not admin_view:
            query["user_email"] = user_email

        order = await merchandise_order_collection.find_one(query)
        if not order:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found or unauthorized.")

        current_status = order.get("status")
        if current_status in [OrderStatus.SHIPPED.value, OrderStatus.DELIVERED.value, OrderStatus.CANCELLED.value]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Order cannot be cancelled in status '{current_status}'."
            )

        now_utc = datetime.now(timezone.utc)

        # If pending payment, release reserved stock
        if current_status == OrderStatus.PENDING_PAYMENT.value:
            for it in order.get("items", []):
                p_oid = MerchandiseService._to_object_id(it["product_id"])
                p_filter: Dict[str, Any] = {"_id": p_oid} if p_oid else {"_id": it["product_id"]}
                qty = it.get("quantity", 1)

                if it.get("variant") and it["variant"].get("variant_id"):
                    p_filter["variants.variant_id"] = it["variant"]["variant_id"]
                    await merchandise_product_collection.update_one(
                        p_filter,
                        {"$inc": {
                            "variants.$.reserved_quantity": -qty,
                            "reserved_quantity": -qty
                        }}
                    )
                else:
                    await merchandise_product_collection.update_one(
                        p_filter,
                        {"$inc": {"reserved_quantity": -qty}}
                    )

                await merchandise_inventory_movement_collection.insert_one({
                    "product_id": it["product_id"],
                    "variant_id": it["variant"]["variant_id"] if it.get("variant") else None,
                    "movement_type": InventoryMovementType.RESERVATION_RELEASED.value,
                    "quantity_delta": -qty,
                    "related_order_number": order.get("order_number"),
                    "actor": user_email,
                    "reason": f"Pending order cancelled: {reason}",
                    "timestamp": now_utc
                })

            await merchandise_order_collection.update_one(
                {"_id": order["_id"]},
                {
                    "$set": {"status": OrderStatus.CANCELLED.value, "updated_at": now_utc},
                    "$push": {"status_history": {"status": OrderStatus.CANCELLED.value, "timestamp": now_utc, "actor": user_email, "notes": reason}}
                }
            )
            return serialize_mongo({"message": "Order cancelled and reserved stock released.", "order_number": order.get("order_number")})

        # If paid and confirmed (before shipment), mark REFUND_PENDING and restock
        elif current_status in [OrderStatus.CONFIRMED.value, OrderStatus.PROCESSING.value]:
            for it in order.get("items", []):
                p_oid = MerchandiseService._to_object_id(it["product_id"])
                p_filter: Dict[str, Any] = {"_id": p_oid} if p_oid else {"_id": it["product_id"]}
                qty = it.get("quantity", 1)

                if it.get("variant") and it["variant"].get("variant_id"):
                    p_filter["variants.variant_id"] = it["variant"]["variant_id"]
                    await merchandise_product_collection.update_one(
                        p_filter,
                        {"$inc": {"variants.$.stock_quantity": qty, "stock_quantity": qty}}
                    )
                else:
                    await merchandise_product_collection.update_one(
                        p_filter,
                        {"$inc": {"stock_quantity": qty}}
                    )

                await merchandise_inventory_movement_collection.insert_one({
                    "product_id": it["product_id"],
                    "variant_id": it["variant"]["variant_id"] if it.get("variant") else None,
                    "movement_type": InventoryMovementType.RETURN_RESTOCK.value,
                    "quantity_delta": qty,
                    "related_order_number": order.get("order_number"),
                    "actor": user_email,
                    "reason": f"Order cancelled before shipment. Stock restored. Reason: {reason}",
                    "timestamp": now_utc
                })

            await merchandise_order_collection.update_one(
                {"_id": order["_id"]},
                {
                    "$set": {"status": OrderStatus.REFUND_PENDING.value, "payment_status": PaymentStatus.REFUNDED.value, "updated_at": now_utc},
                    "$push": {"status_history": {"status": OrderStatus.REFUND_PENDING.value, "timestamp": now_utc, "actor": user_email, "notes": reason}}
                }
            )
            return serialize_mongo({"message": "Order cancelled. Physical stock restored and refund pending.", "order_number": order.get("order_number")})

        return serialize_mongo({"message": "Cancellation request processed.", "order_number": order.get("order_number")})

    @staticmethod
    async def expire_pending_orders() -> Dict[str, Any]:
        """
        Background maintenance job: expires unpaid orders older than TTL (15 mins)
        and safely releases reserved stock.
        """
        now_utc = datetime.now(timezone.utc)
        expired_cursor = merchandise_order_collection.find({
            "status": OrderStatus.PENDING_PAYMENT.value,
            "expires_at": {"$lt": now_utc}
        })
        expired_orders = await expired_cursor.to_list(length=100)

        released_count = 0
        for ord_doc in expired_orders:
            for it in ord_doc.get("items", []):
                p_oid = MerchandiseService._to_object_id(it["product_id"])
                p_filter: Dict[str, Any] = {"_id": p_oid} if p_oid else {"_id": it["product_id"]}
                qty = it.get("quantity", 1)

                if it.get("variant") and it["variant"].get("variant_id"):
                    p_filter["variants.variant_id"] = it["variant"]["variant_id"]
                    await merchandise_product_collection.update_one(
                        p_filter,
                        {"$inc": {
                            "variants.$.reserved_quantity": -qty,
                            "reserved_quantity": -qty
                        }}
                    )
                else:
                    await merchandise_product_collection.update_one(
                        p_filter,
                        {"$inc": {"reserved_quantity": -qty}}
                    )

            await merchandise_order_collection.update_one(
                {"_id": ord_doc["_id"]},
                {
                    "$set": {"status": OrderStatus.CANCELLED.value, "payment_status": PaymentStatus.EXPIRED.value, "updated_at": now_utc},
                    "$push": {"status_history": {"status": OrderStatus.CANCELLED.value, "timestamp": now_utc, "actor": "SYSTEM_EXPIRY_WORKER", "notes": "Reservation expired after TTL."}}
                }
            )
            released_count += 1

        return {"expired_orders_count": released_count}

    # ──────────────────────────────────────────────────────────
    # Super Admin Inventory Adjustments & Analytics
    # ──────────────────────────────────────────────────────────

    @staticmethod
    async def adjust_stock(req: StockAdjustmentReq, admin_email: str) -> Dict[str, Any]:
        p_oid = MerchandiseService._to_object_id(req.product_id)
        product = await merchandise_product_collection.find_one({"$or": [{"_id": p_oid}, {"_id": req.product_id}]}) if p_oid else await merchandise_product_collection.find_one({"_id": req.product_id})
        if not product:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

        now_utc = datetime.now(timezone.utc)
        if req.variant_id:
            matching_var = next((v for v in product.get("variants", []) if v.get("variant_id") == req.variant_id), None)
            if not matching_var:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variant not found.")

            prev_qty = matching_var.get("stock_quantity", 0)
            new_qty = prev_qty + req.quantity_delta
            if new_qty < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Stock cannot be negative (Current: {prev_qty}, Delta: {req.quantity_delta}).")

            await merchandise_product_collection.update_one(
                {"_id": product["_id"], "variants.variant_id": req.variant_id},
                {"$inc": {"variants.$.stock_quantity": req.quantity_delta, "stock_quantity": req.quantity_delta}, "$set": {"updated_at": now_utc}}
            )
        else:
            prev_qty = product.get("stock_quantity", 0)
            new_qty = prev_qty + req.quantity_delta
            if new_qty < 0:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Stock cannot be negative (Current: {prev_qty}, Delta: {req.quantity_delta}).")

            await merchandise_product_collection.update_one(
                {"_id": product["_id"]},
                {"$inc": {"stock_quantity": req.quantity_delta}, "$set": {"updated_at": now_utc}}
            )

        # Audit movement
        await merchandise_inventory_movement_collection.insert_one({
            "product_id": str(product["_id"]),
            "variant_id": req.variant_id,
            "movement_type": InventoryMovementType.STOCK_ADJUSTMENT.value,
            "quantity_delta": req.quantity_delta,
            "previous_quantity": prev_qty,
            "new_quantity": new_qty,
            "actor": admin_email,
            "reason": req.reason,
            "timestamp": now_utc
        })

        updated = await merchandise_product_collection.find_one({"_id": product["_id"]})
        return serialize_mongo(updated)

    @staticmethod
    async def get_analytics() -> Dict[str, Any]:
        """
        Super Admin merchandise sales and inventory analytics aggregation.
        """
        total_products = await merchandise_product_collection.count_documents({})
        active_products = await merchandise_product_collection.count_documents({"status": ProductStatus.ACTIVE.value})
        archived_products = await merchandise_product_collection.count_documents({"status": ProductStatus.ARCHIVED.value})

        # Orders summary
        orders = await merchandise_order_collection.find({}).to_list(length=1000)
        total_orders = len(orders)
        confirmed_orders = sum(1 for o in orders if o.get("payment_status") == PaymentStatus.PAID.value)
        pending_orders = sum(1 for o in orders if o.get("status") == OrderStatus.PENDING_PAYMENT.value)
        cancelled_orders = sum(1 for o in orders if o.get("status") in [OrderStatus.CANCELLED.value, OrderStatus.REFUNDED.value])

        gross_revenue = sum(float(o.get("total_amount", 0.0)) for o in orders if o.get("payment_status") == PaymentStatus.PAID.value)
        refunded_amount = sum(float(o.get("total_amount", 0.0)) for o in orders if o.get("status") == OrderStatus.REFUNDED.value)
        net_revenue = round(gross_revenue - refunded_amount, 2)

        # Units sold
        units_sold = 0
        for o in orders:
            if o.get("payment_status") == PaymentStatus.PAID.value:
                units_sold += sum(int(i.get("quantity", 1)) for i in o.get("items", []))

        # Low stock warnings
        low_stock_products = await merchandise_product_collection.find({
            "status": ProductStatus.ACTIVE.value,
            "$expr": {"$lte": [{"$subtract": ["$stock_quantity", "$reserved_quantity"]}, "$low_stock_threshold"]}
        }).to_list(length=20)

        return serialize_mongo({
            "total_products": total_products,
            "active_products": active_products,
            "archived_products": archived_products,
            "total_orders": total_orders,
            "confirmed_orders": confirmed_orders,
            "pending_orders": pending_orders,
            "cancelled_orders": cancelled_orders,
            "units_sold": units_sold,
            "gross_revenue": round(gross_revenue, 2),
            "refunded_amount": round(refunded_amount, 2),
            "net_revenue": net_revenue,
            "currency": "INR",
            "low_stock_items": [
                {
                    "product_id": str(p["_id"]),
                    "name": p.get("name"),
                    "sku": p.get("sku"),
                    "available_stock": p.get("stock_quantity", 0) - p.get("reserved_quantity", 0),
                    "threshold": p.get("low_stock_threshold", 5)
                }
                for p in low_stock_products
            ]
        })
