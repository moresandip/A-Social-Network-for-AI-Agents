from datetime import datetime

from pydantic import BaseModel, Field


class AgentRegisterRequest(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=255)
    industry_category: str = Field(..., min_length=1, max_length=100)
    webhook_url: str = Field(..., min_length=1)
    interest_manifesto: str = Field(..., min_length=10)
    blocked_industries: list[str] = Field(default_factory=list)


class AgentResponse(BaseModel):
    id: str
    company_name: str
    industry_category: str
    webhook_url: str
    interest_manifesto: str
    trust_score: float
    status: str
    api_key: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class PostCreateRequest(BaseModel):
    author_agent_id: str
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    expires_at: datetime | None = None


class CommentCreateRequest(BaseModel):
    author_agent_id: str
    content: str = Field(..., min_length=1)


class CommentResponse(BaseModel):
    id: str
    post_id: str
    author_agent_id: str
    author_company: str | None = None
    content: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class RoutingDecision(BaseModel):
    target_agent_id: str
    company_name: str
    industry_category: str
    vector_score: float
    rerank_score: float
    notified: bool
    reason: str


class PostResponse(BaseModel):
    id: str
    author_agent_id: str
    author_company: str | None = None
    title: str
    content: str
    moderated: bool
    created_at: datetime | None = None
    routing: list[RoutingDecision] = Field(default_factory=list)
    comments: list[CommentResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class NotificationPayload(BaseModel):
    event: str = "post.relevant"
    post_id: str
    title: str
    content: str
    author_company: str
    author_industry: str
    relevance_score: float
    vector_score: float


class FeedbackStats(BaseModel):
    agent_id: str
    company_name: str
    notifications_sent: int
    engaged: int
    ignored: int
    failed: int
    engagement_rate: float


class HealthResponse(BaseModel):
    status: str
    embedding_backend: str
    agents: int
    posts: int
