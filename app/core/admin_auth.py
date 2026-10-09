from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.security import decode_token
from app.core.database import admin_collection

admin_security = HTTPBearer(description="Admin Bearer Token")

async def get_current_admin(credentials: HTTPAuthorizationCredentials = Depends(admin_security)):
    """Dependency: validates admin JWT. Rejects regular user tokens."""
    token = credentials.credentials
    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Must be an admin-type token
    if payload.get("type") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not an admin token")

    email = payload.get("sub")
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    admin = await admin_collection.find_one({"email": email})
    if not admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin account not found")

    return admin
