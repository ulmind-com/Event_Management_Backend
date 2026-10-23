from pydantic import BaseModel, EmailStr, Field
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

# Convenient alias for role-based password login
PasswordLoginReq = UserLoginReq

class SetPasswordReq(BaseModel):
    password: str = Field(..., min_length=8)
    confirm_password: Optional[str] = None

class ChangePasswordReq(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)
    confirm_password: Optional[str] = None

class Token(BaseModel):
    access_token: str
    token_type: str

class GoogleLoginReq(BaseModel):
    id_token: str
    referral_code: Optional[str] = None
