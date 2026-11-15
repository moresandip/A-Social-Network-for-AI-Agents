from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Agent, AgentStatus
from app.schemas import AgentRegisterRequest, AgentResponse, FeedbackStats
from app.services.embeddings import get_embedder
from app.services.feedback import apply_negative_feedback, engagement_stats

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.post("/register", response_model=AgentResponse, status_code=201)
def register_agent(payload: AgentRegisterRequest, db: Session = Depends(get_db)) -> Agent:
    embedder = get_embedder()
    embedding = embedder.embed(
        f"{payload.company_name}. {payload.industry_category}. {payload.interest_manifesto}"
    )
    agent = Agent(
        company_name=payload.company_name,
        industry_category=payload.industry_category,
        webhook_url=payload.webhook_url,
        interest_manifesto=payload.interest_manifesto,
        interest_embedding=embedder.serialize(embedding),
        blocked_industries=",".join(payload.blocked_industries),
        status=AgentStatus.ACTIVE,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("", response_model=list[AgentResponse])
def list_agents(db: Session = Depends(get_db)) -> list[Agent]:
    return db.query(Agent).order_by(Agent.created_at.asc()).all()


@router.get("/{agent_id}", response_model=AgentResponse)
def get_agent(agent_id: str, db: Session = Depends(get_db)) -> Agent:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.get("/{agent_id}/feedback", response_model=list[FeedbackStats])
def agent_feedback(agent_id: str, db: Session = Depends(get_db)) -> list[FeedbackStats]:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return engagement_stats(db, agent_id)


@router.post("/{agent_id}/feedback/apply-negative")
def agent_negative_feedback(agent_id: str, db: Session = Depends(get_db)) -> dict:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return apply_negative_feedback(db, agent_id)
