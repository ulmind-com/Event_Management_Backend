from fastapi import APIRouter, Request, HTTPException, Header, status
from typing import Optional, Dict, Any
import json
import logging

from app.services.merchandise_payment_gateway import MerchandisePaymentGatewayAdapter
from app.services.merchandise_service import MerchandiseService, serialize_mongo

logger = logging.getLogger("eventora.merchandise_webhooks")

router = APIRouter()

@router.post("/payments/webhook", response_model=Dict[str, Any])
async def handle_merchandise_payment_webhook(
    request: Request,
    x_razorpay_signature: Optional[str] = Header(None),
    stripe_signature: Optional[str] = Header(None),
    x_webhook_signature: Optional[str] = Header(None)
):
    """
    Authoritative Payment Webhook Handler for Event Merchandise.
    Verifies HMAC digital signature, prevents payment forgery, and idempotently confirms orders.
    """
    raw_body = await request.body()
    signature = x_razorpay_signature or stripe_signature or x_webhook_signature

    if not signature:
        logger.error("Missing webhook digital signature header.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing webhook signature header."
        )

    # 1. Authoritative Signature Verification
    is_valid = MerchandisePaymentGatewayAdapter.verify_webhook_signature(raw_body, signature)
    if not is_valid:
        logger.error("Invalid merchandise webhook signature verification failed.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook digital signature."
        )

    # 2. Parse & Normalize Payload
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    parsed = MerchandisePaymentGatewayAdapter.parse_webhook_event(payload)
    order_number = parsed.get("order_number")
    payment_txn_id = parsed.get("payment_txn_id")
    amount = parsed.get("amount")
    currency = parsed.get("currency")
    provider_order_id = parsed.get("provider_order_id")

    if not order_number:
        raise HTTPException(status_code=400, detail="Missing order_number reference in webhook event.")

    if parsed.get("status") != "SUCCESS":
        logger.warning(f"Payment failed or declined for order {order_number}.")
        return {"status": "PAYMENT_FAILED_RECORDED", "order_number": order_number}

    # 3. Idempotently Confirm Order & Commit Stock
    res = await MerchandiseService.confirm_payment_and_complete_order(
        order_number=order_number,
        payment_txn_id=payment_txn_id,
        amount_paid=amount,
        currency=currency,
        provider_order_id=provider_order_id
    )

    return serialize_mongo(res)
