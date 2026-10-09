from fastapi import APIRouter, HTTPException, Depends
from app.schemas.judge import PasswordSetupReq
from app.core.database import user_collection
from app.core.security import get_password_hash
from app.core.config import settings
import jwt
from datetime import datetime, timezone

router = APIRouter()

@router.post("/setup")
async def setup_password(req: PasswordSetupReq):
    """Securely sets a password for a newly invited judge."""
    try:
        payload = jwt.decode(req.token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != "judge_setup":
            raise HTTPException(status_code=400, detail="Invalid token type")
        email = payload.get("sub")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=400, detail="Invalid token")
        
    judge = await user_collection.find_one({"email": email, "role": "JUDGE"})
    if not judge:
        raise HTTPException(status_code=404, detail="Judge not found")
        
    await user_collection.update_one(
        {"email": email},
        {"$set": {
            "password_hash": get_password_hash(req.new_password),
            "status": "ACTIVE",
            "is_verified": True,
            "registration_complete": True
        }}
    )
    
    return {"message": "Password setup successfully. You can now login."}
