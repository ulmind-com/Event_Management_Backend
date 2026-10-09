from pydantic import BaseModel, EmailStr

class AdminLoginReq(BaseModel):
    email: EmailStr
    password: str
