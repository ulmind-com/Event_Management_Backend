from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime

class JudgeCreateReq(BaseModel):
    full_name: str
    email: EmailStr
    phone_number: Optional[str] = None
    designation: Optional[str] = None
    biography: Optional[str] = None
    status: str = "PENDING_SETUP"

class JudgeUpdateReq(BaseModel):
    full_name: Optional[str] = None
    phone_number: Optional[str] = None
    designation: Optional[str] = None
    biography: Optional[str] = None
    status: Optional[str] = None

class PasswordSetupReq(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)

class JudgeAssignmentReq(BaseModel):
    judge_email: str
    competition_id: str
