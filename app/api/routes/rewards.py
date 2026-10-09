from fastapi import APIRouter, Depends
from app.api.deps import get_current_user
from app.core.database import certificate_collection, reward_collection

router = APIRouter()

@router.get("/certificates")
async def get_my_certificates(current_user: dict = Depends(get_current_user)):
    certs = await certificate_collection.find({"email": current_user["email"]}).to_list(length=100)
    for c in certs:
        c["_id"] = str(c["_id"])
    return certs

@router.get("/coins")
async def get_my_coins(current_user: dict = Depends(get_current_user)):
    rewards = await reward_collection.find({"email": current_user["email"]}).to_list(length=100)
    total_coins = sum([r.get("coins", 0) for r in rewards])
    for r in rewards:
        r["_id"] = str(r["_id"])
    return {"total_coins": total_coins, "history": rewards}
