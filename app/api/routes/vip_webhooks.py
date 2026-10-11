from fastapi import APIRouter, Request, Header, HTTPException, status
from typing import Optional, Dict, Any
import json
import logging

from app.services.vip_payment_gateway import VIPPaymentGatewayAdapter
from app.services.vip_service import VIPService
from app.core.database import vip_booking_collection

logger = logging.getLogger("eventora.vip_webhook")

router = APIRouter()

@router.post("/vip/payments/webhook", status_code=status.HTTP_200_OK)
async def process_vip_payment_webhook(
    request: Request,
    x_razorpay_signature: Optional[str] = Header(None),
    x_webhook_signature: Optional[str] = Header(None)
):
    """
    Authoritative Payment Gateway Webhook Receiver for VIP Pass Bookings.
    Validates HMAC SHA256 signature, enforces amount/currency/order reconciliation,
    and idempotently confirms bookings and issues passes.
    """
    raw_body = await request.body()
    sig_header = x_razorpay_signature or x_webhook_signature or request.headers.get("X-Signature")

    # In production/live environments, reject if signature cannot be verified
    is_valid_sig = VIPPaymentGatewayAdapter.verify_webhook_signature(raw_body, sig_header)
    if not is_valid_sig:
        logger.warning("Payment webhook received with invalid or missing signature.")
        # Only accept unsigned payloads if in test/development mode with explicit flag
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or missing webhook signature."
        )

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    parsed = VIPPaymentGatewayAdapter.parse_webhook_event(payload)
    booking_ref = parsed.get("booking_reference")
    if not booking_ref:
        raise HTTPException(status_code=400, detail="Missing booking reference in webhook payload.")

    if parsed.get("status") == "PAID":
        result = await VIPService.confirm_payment_and_issue_pass(
            booking_reference=booking_ref,
            payment_txn_id=parsed.get("transaction_id"),
            amount_paid=parsed.get("amount", 0.0),
            currency=parsed.get("currency", "INR"),
            provider_order_id=parsed.get("provider_order_id")
        )
        return {"status": "SUCCESS", "result": result}
    else:
        # Mark payment failed
        await vip_booking_collection.update_one(
            {"booking_reference": booking_ref, "booking_status": "PENDING_PAYMENT"},
            {"$set": {"booking_status": "PAYMENT_FAILED", "payment_status": "FAILED"}}
        )
        return {"status": "PAYMENT_FAILED", "booking_reference": booking_ref}
