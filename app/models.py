"""
Pydantic models for request/response validation
"""
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class UserResponse(BaseModel):
    """Response model for user data"""
    id: str
    clerk_user_id: str
    email: str
    name: str
    last_login: datetime
    total_searches: int
    created_at: datetime

class VerifyTokenResponse(BaseModel):
    """Response model for token verification"""
    success: bool
    message: str
    user: Optional[UserResponse] = None

class ErrorResponse(BaseModel):
    """Standard error response"""
    detail: str
