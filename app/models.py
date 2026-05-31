"""
Pydantic models for request/response validation
"""
from pydantic import BaseModel, Field, field_validator
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


class Agent2BuildQueriesRequest(BaseModel):
    """Request payload for Agent 2 prompt builder"""
    recipient_profile: Dict[str, Any]
    force_expand_budget: bool = False


class Agent2BuildQueriesResponse(BaseModel):
    """Response payload for Agent 2 prompt builder"""
    queries: List[str]
    budget_stated: int
    budget_searched: int
    budget_strategy: Literal["strict", "expanded_30_percent"]
    category_specificity: Literal["specific", "broad", "unknown"] = "unknown"


class Agent3ScrapeRequest(BaseModel):
    """Request payload for Agent 3 scraper"""
    queries: List[str] = Field(min_length=1, max_length=5)
    budget_searched: int = Field(ge=100, le=1_000_000)


class ScrapedProduct(BaseModel):
    """Raw scraped product from Amazon"""
    asin: str
    name: str
    price: Optional[float] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    image_url: Optional[str] = None
    affiliate_url: str
    source_query: str


class Agent3ScrapeResponse(BaseModel):
    """Response payload for Agent 3 scraper"""
    products: List[ScrapedProduct]
    total_products: int
    warning_message: Optional[str] = None


class Agent4RankRequest(BaseModel):
    """Request payload for Agent 4 ranker"""
    products: List[ScrapedProduct] = Field(min_length=1)
    budget_searched: int = Field(ge=100, le=1_000_000)


class RankedProduct(ScrapedProduct):
    """Ranked product with score breakdown"""
    final_score: float
    rating_score: float
    review_score: float
    price_fit_score: float


class Agent4RankResponse(BaseModel):
    """Response payload for Agent 4 ranker"""
    ranked_products: List[RankedProduct]
    total_ranked: int


class Agent5PersonalizeRequest(BaseModel):
    """Request payload for Agent 5 personalizer"""
    ranked_products: List[RankedProduct] = Field(min_length=1)
    recipient_profile: Dict[str, Any]


class PersonalizedProduct(RankedProduct):
    """Ranked product with personalized recommendation reason"""
    personalized_reason: str


class Agent5PersonalizeResponse(BaseModel):
    """Response payload for Agent 5 personalizer"""
    products: List[PersonalizedProduct]
    total_personalized: int


class ClickLogRequest(BaseModel):
    """Request payload to log product click"""
    session_id: Optional[str] = Field(default=None, max_length=100)
    product_asin: str = Field(min_length=1, max_length=20)

    @field_validator("session_id")
    @classmethod
    def normalize_session_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("product_asin")
    @classmethod
    def normalize_product_asin(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("product_asin must not be empty")
        return normalized


class ClickLogResponse(BaseModel):
    """Response payload after logging click"""
    success: bool
    click_id: str


class SessionCreateRequest(BaseModel):
    """Request payload to save a completed search session"""
    recipient_profile: Dict[str, Any]
    search_queries: List[str] = Field(min_length=1, max_length=20)
    products_returned: List[Dict[str, Any]] = Field(default_factory=list, max_length=200)
    budget_stated: int = Field(ge=100, le=1_000_000)
    budget_searched: int = Field(ge=100, le=1_000_000)

    @field_validator("search_queries")
    @classmethod
    def normalize_search_queries(cls, values: List[str]) -> List[str]:
        cleaned: List[str] = []
        for value in values:
            normalized = value.strip()
            if not normalized:
                continue
            cleaned.append(normalized)
        if not cleaned:
            raise ValueError("search_queries must include at least one non-empty query")
        return cleaned


class SessionResponse(BaseModel):
    """Session payload returned by sessions endpoints"""
    id: str
    user_id: str
    recipient_profile: Dict[str, Any]
    search_queries: List[str]
    products_returned: List[Dict[str, Any]]
    budget_stated: int
    budget_searched: int
    created_at: datetime


class SessionListResponse(BaseModel):
    """Response payload for listing sessions"""
    sessions: List[SessionResponse]
    total: int
