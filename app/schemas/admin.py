from pydantic import BaseModel, EmailStr

class AdminLoginReq(BaseModel):
    email: EmailStr
    password: str


class AdminChangePasswordReq(BaseModel):
    current_password: str
    new_password: str


class AdminChangeEmailReq(BaseModel):
    current_password: str
    new_email: EmailStr
