from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import booking_collection
from app.schemas.booking import PaymentVerifyReq, BookingStatus, PaymentStatus
from app.api.routes.bookings import generate_tickets

router = APIRouter()

@router.post("/verify-mock/{booking_id}")
async def verify_payment_mock(booking_id: str, req: PaymentVerifyReq, bg_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    """
    Mock endpoint to simulate a payment gateway webhook or client-side verify.
    In a real app, this would verify the transaction signature.
    """
    try:
        b_id = ObjectId(booking_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid booking ID")
        
    booking = await booking_collection.find_one({"_id": b_id})
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
        
    if booking.get("payment_status") != PaymentStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Booking is not pending payment")
        
    if req.status == "success":
        await booking_collection.update_one(
            {"_id": b_id},
            {"$set": {
                "payment_status": PaymentStatus.PAID.value,
                "booking_status": BookingStatus.CONFIRMED.value,
                "payment_reference": req.transaction_id
            }}
        )
        # Generate tickets now that it's paid
        bg_tasks.add_task(
            generate_tickets, str(b_id), booking["booking_reference"], booking["event_id"], 
            "PaidTicket", booking["user_email"], booking["quantity"], []
        )
        return {"message": "Payment verified and booking confirmed"}
    else:
        await booking_collection.update_one(
            {"_id": b_id},
            {"$set": {
                "payment_status": PaymentStatus.FAILED.value,
                "booking_status": BookingStatus.CANCELLED.value
            }}
        )
        return {"message": "Payment failed, booking cancelled"}
