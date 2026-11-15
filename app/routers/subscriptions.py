"""Subscriptions: agents explicitly declare topics / industries they care about.

These topics are used by Stage-1 of the routing engine to give a hard-boost
to agents that have subscribed to keywords matching the post's content.

Endpoints
---------
POST   /api/v1/subscriptions           — subscribe agent to a topic
GET    /api/v1/subscriptions/{agent_id} — list all subscriptions for an agent
DELETE /api/v1/subscriptions/{sub_id}  — unsubscribe
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Agent, Subscription
from app.schemas import SubscriptionCreateRequest, SubscriptionResponse

router = APIRouter(prefix="/api/v1/subscriptions", tags=["subscriptions"])


@router.post("", response_model=SubscriptionResponse, status_code=201)
def subscribe(payload: SubscriptionCreateRequest, db: Session = Depends(get_db)) -> Subscription:
    """Register an agent's interest in a topic keyword or industry."""
    agent = db.query(Agent).filter(Agent.id == payload.agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    sub = Subscription(agent_id=payload.agent_id, topic=payload.topic.strip().lower())
    db.add(sub)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Agent '{agent.company_name}' is already subscribed to topic '{payload.topic}'",
        )
    db.refresh(sub)
    return sub


@router.get("/{agent_id}", response_model=list[SubscriptionResponse])
def list_subscriptions(agent_id: str, db: Session = Depends(get_db)) -> list[Subscription]:
    """Return all subscriptions for a given agent."""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return db.query(Subscription).filter(Subscription.agent_id == agent_id).all()


@router.delete("/{subscription_id}", status_code=204)
def unsubscribe(subscription_id: str, db: Session = Depends(get_db)) -> None:
    """Remove a subscription."""
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    db.delete(sub)
    db.commit()
