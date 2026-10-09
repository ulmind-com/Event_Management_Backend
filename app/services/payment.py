from typing import Dict, Any

class PaymentService:
    """
    Placeholder service for future payment gateway integration.
    Do not add real credentials or simulate successful payments automatically in production here.
    """

    @staticmethod
    async def initiate_payment(booking_reference: str, amount: float, currency: str = "INR") -> Dict[str, Any]:
        """
        Creates a payment session with the provider.
        For now, returns a mock payment URL and transaction ID.
        """
        # TODO: Replace with real provider SDK (e.g., Stripe, Razorpay)
        return {
            "transaction_id": f"txn_mock_{booking_reference}",
            "payment_url": f"https://mock-payment-gateway.example.com/pay/{booking_reference}",
            "amount": amount,
            "currency": currency,
            "status": "pending_integration"
        }

    @staticmethod
    async def verify_payment(transaction_id: str) -> bool:
        """
        Verifies the payment with the provider.
        Currently returns False to prevent unverified checkouts in production.
        """
        # TODO: Call provider API to verify transaction status
        return False

    @staticmethod
    async def process_refund(booking_reference: str, amount: float) -> bool:
        """
        Processes a refund for a cancelled booking.
        """
        # TODO: Implement refund logic via provider
        return False
