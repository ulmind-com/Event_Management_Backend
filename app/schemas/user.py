from pydantic import BaseModel, EmailStr
from typing import Optional

class EmailRequest(BaseModel):
    email: EmailStr
    referral_code: Optional[str] = None

class OTPVerifyRequest(BaseModel):
    email: EmailStr
    otp: str

class UserLoginReq(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str
