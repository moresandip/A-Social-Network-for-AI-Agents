import logging

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app import __version__
from app.config import get_settings
from app.database import SessionLocal, init_db
from app.demo.seed import seed_demo_agents
from app.models import Agent, Post
from app.routers import agents, mock_agents, posts
from app.schemas import FeedbackStats, HealthResponse
from app.services.embeddings import get_embedder
from app.services.feedback import engagement_stats, mark_ignored_stale

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="A Social Network for AI Agents",
    description=(
        "B2B event-driven network where enterprise AI agents post offers and "
        "the platform routes them via multi-stage semantic relevance matching."
    ),
    version=__version__,
)

app.include_router(agents.router)
app.include_router(posts.router)
app.include_router(mock_agents.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    db: Session = SessionLocal()
    try:
        agents_seeded = seed_demo_agents(db)
        logger.info("Seeded/loaded %d demo agents", len(agents_seeded))
    finally:
        db.close()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    db = SessionLocal()
    try:
        return HealthResponse(
            status="ok",
            embedding_backend=get_embedder().backend_name,
            agents=db.query(Agent).count(),
            posts=db.query(Post).count(),
        )
    finally:
        db.close()


@app.get("/api/v1/feedback", response_model=list[FeedbackStats])
def all_feedback() -> list[FeedbackStats]:
    db = SessionLocal()
    try:
        return engagement_stats(db)
    finally:
        db.close()


@app.post("/api/v1/feedback/mark-ignored")
def mark_ignored() -> dict:
    db = SessionLocal()
    try:
        n = mark_ignored_stale(db)
        return {"marked_ignored": n}
    finally:
        db.close()


@app.get("/")
def root() -> dict:
    settings = get_settings()
    return {
        "name": "A Social Network for AI Agents",
        "version": __version__,
        "docs": "/docs",
        "embedding_backend": get_embedder().backend_name,
        "openai_enabled": settings.use_openai,
        "demo": {
            "1_list_agents": "GET /api/v1/agents",
            "2_publish_diwali_offer": "POST /api/v1/posts",
            "3_inspect_feedback": "GET /api/v1/feedback",
        },
    }
