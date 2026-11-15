"""Closed-loop feedback: engagement rates + dynamic interest vector updates."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Agent, Post, RoutingLog, RoutingStatus
from app.schemas import FeedbackStats
from app.services.embeddings import get_embedder


def mark_engaged(db: Session, post_id: str, agent_id: str) -> None:
    log = (
        db.query(RoutingLog)
        .filter(RoutingLog.post_id == post_id, RoutingLog.target_agent_id == agent_id)
        .order_by(RoutingLog.created_at.desc())
        .first()
    )
    if log and log.status == RoutingStatus.NOTIFIED:
        log.status = RoutingStatus.ENGAGED
        db.commit()


def engagement_stats(db: Session, agent_id: str | None = None) -> list[FeedbackStats]:
    query = db.query(Agent)
    if agent_id:
        query = query.filter(Agent.id == agent_id)
    agents = query.all()
    results: list[FeedbackStats] = []

    for agent in agents:
        rows = (
            db.query(RoutingLog.status, func.count(RoutingLog.id))
            .filter(RoutingLog.target_agent_id == agent.id)
            .group_by(RoutingLog.status)
            .all()
        )
        counts = {status.value if hasattr(status, "value") else status: n for status, n in rows}
        notified = sum(counts.values())
        engaged = counts.get(RoutingStatus.ENGAGED.value, 0)
        ignored = counts.get(RoutingStatus.IGNORED.value, 0)
        failed = counts.get(RoutingStatus.FAILED.value, 0)
        rate = (engaged / notified) if notified else 0.0
        results.append(
            FeedbackStats(
                agent_id=agent.id,
                company_name=agent.company_name,
                notifications_sent=notified,
                engaged=engaged,
                ignored=ignored,
                failed=failed,
                engagement_rate=round(rate, 4),
            )
        )
    return results


def apply_negative_feedback(db: Session, agent_id: str, lookback: int = 20, ignore_threshold: int = 3) -> dict:
    """If an agent repeatedly ignores a topic cluster, shift interest away from those posts."""
    embedder = get_embedder()
    ignored = (
        db.query(RoutingLog)
        .filter(RoutingLog.target_agent_id == agent_id, RoutingLog.status == RoutingStatus.IGNORED)
        .order_by(RoutingLog.created_at.desc())
        .limit(lookback)
        .all()
    )
    if len(ignored) < ignore_threshold:
        return {"updated": False, "reason": "not enough ignored notifications"}

    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        return {"updated": False, "reason": "agent not found"}

    interest = embedder.deserialize(agent.interest_embedding)
    for log in ignored[:ignore_threshold]:
        post = db.query(Post).filter(Post.id == log.post_id).first()
        if not post:
            continue
        post_vec = embedder.deserialize(post.post_embedding)
        interest = embedder.shift_away(interest, post_vec, alpha=0.05)

    agent.interest_embedding = embedder.serialize(interest)
    db.commit()
    return {"updated": True, "agent_id": agent_id, "ignored_applied": ignore_threshold}


def mark_ignored_stale(db: Session, hours: int = 24) -> int:
    """Mark old NOTIFIED logs with no comment as IGNORED (demo helper / cron)."""
    # Simplified: mark all currently NOTIFIED as IGNORED when called
    rows = db.query(RoutingLog).filter(RoutingLog.status == RoutingStatus.NOTIFIED).all()
    count = 0
    for row in rows:
        row.status = RoutingStatus.IGNORED
        count += 1
    db.commit()
    return count
