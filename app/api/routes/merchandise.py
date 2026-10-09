from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from app.api.deps import get_current_user
from app.core.database import order_collection, merchandise_collection
from app.schemas.action import OrderReq
from datetime import datetime, timezone

router = APIRouter()

@router.post("/")
async def order_merchandise(req: OrderReq, current_user: dict = Depends(get_current_user)):
    try:
        obj_id = ObjectId(req.merchandise_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid merchandise ID")

    item = await merchandise_collection.find_one({"_id": obj_id})
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if item.get("stock", 0) < req.quantity:
        raise HTTPException(status_code=400, detail="Not enough stock available")

    total_price = item.get("price", 0) * req.quantity

    order = {
        "email": current_user["email"],
        "merchandise_id": req.merchandise_id,
        "quantity": req.quantity,
        "total_price": total_price,
        "shipping_address": req.shipping_address,
        "ordered_at": datetime.now(timezone.utc),
        "status": "pending_payment" # Assume payment needs to be done later or verified
    }

    await order_collection.insert_one(order)
    order['_id'] = str(order['_id'])
    await merchandise_collection.update_one({"_id": obj_id}, {"$inc": {"stock": -req.quantity}})

    return {"message": "Order created successfully", "order": order}

@router.get("/")
async def get_my_orders(current_user: dict = Depends(get_current_user)):
    orders = await order_collection.find({"email": current_user["email"]}).to_list(length=100)
    for o in orders:
        o["_id"] = str(o["_id"])
    return orders

