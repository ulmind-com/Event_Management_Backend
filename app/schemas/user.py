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

class ForgotPasswordReq(BaseModel):
    email: EmailStr = Field(..., description="Registered user email address")

class ForgotPasswordVerifyReq(BaseModel):
    email: EmailStr = Field(..., description="Registered user email address")
    otp: str = Field(..., min_length=6, max_length=6, description="6-digit verification code sent to email")

class ResetPasswordWithTokenReq(BaseModel):
    reset_token: str = Field(..., description="Temporary verification token received from verify-otp")
    new_password: str = Field(..., min_length=8, description="New password (minimum 8 characters)")
    confirm_password: str = Field(..., min_length=8, description="Confirmation of new password")

class DirectResetPasswordReq(BaseModel):
    email: EmailStr = Field(..., description="Registered user email address")
    otp: str = Field(..., min_length=6, max_length=6, description="6-digit verification code sent to email")
    new_password: str = Field(..., min_length=8, description="New password (minimum 8 characters)")
    confirm_password: str = Field(..., min_length=8, description="Confirmation of new password")

class Token(BaseModel):
    access_token: str
    token_type: str

class GoogleLoginReq(BaseModel):
    id_token: str
    referral_code: Optional[str] = None

class CompleteProfileReq(BaseModel):
    name: Optional[str] = None
    phone_number: Optional[str] = None
    date_of_birth: Optional[str] = None
    address: Optional[str] = None
    parent_phone_number: Optional[str] = None
    profile_photo: Optional[str] = None
    password: Optional[str] = None

