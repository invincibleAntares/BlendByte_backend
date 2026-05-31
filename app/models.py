"""
Pydantic models for request/response validation
"""
from pydantic import BaseModel, Field
from typing import Optional, Literal, List, Dict, Any
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


class IntakeMessage(BaseModel):
    """Single chat message in the intake conversation"""
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class Agent1ChatRequest(BaseModel):
    """Request payload for Agent 1 conversational intake"""
    messages: List[IntakeMessage] = Field(default_factory=list, max_length=30)


class Agent1ChatResponse(BaseModel):
    """Response payload for Agent 1 conversational intake"""
    is_complete: bool
    next_question: Optional[str] = None
    recipient_profile: Optional[Dict[str, Any]] = None
    messages: List[IntakeMessage]
