"""In-process mock company agents that receive webhooks and optionally comment back.

Simulates PhonePe / HDFC / etc. deciding to engage and drafting a partnership reply.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Agent, Comment, Post
from app.services.feedback import mark_engaged

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mock-agents", tags=["mock-agents"])

# Simple company-specific reply templates (stands in for an LLM draft in demos)
_REPLY_TEMPLATES: dict[str, str] = {
    "PhonePe": (
        "Extra ₹50 cashback when you pay for {author} orders with PhonePe UPI on Diwali."
    ),
    "HDFC Bank": (
        "HDFC Bank Credit Card users get an additional 5% cashback on {author} festive orders."
    ),
    "Flipkart": (
        "Flipkart Plus members can stack SuperCoins on partner offers from {author}."
    ),
    "Swiggy": (
        "Swiggy can co-promote this with restaurant partners and delivery slots this week."
    ),
}


def _verify_signature(body: bytes, signature: str | None) -> None:
    settings = get_settings()
    expected = hmac.new(settings.webhook_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def _should_engage(company: str, score: float) -> bool:
    # Bajaj Auto mock agent never engages on food/payment posts (belt-and-suspenders)
    if company.lower() == "bajaj auto":
        return False
    return score >= 0.55


def _draft_reply(company: str, author_company: str, title: str, content: str) -> str:
    template = _REPLY_TEMPLATES.get(
        company,
        "{company} is interested in partnering on this: {title}",
    )
    return template.format(author=author_company, company=company, title=title, content=content)


@router.post("/{company_slug}/webhook")
async def receive_webhook(
    company_slug: str,
    request: Request,
    x_agent_network_signature: str | None = Header(default=None),
) -> dict:
    body = await request.body()
    _verify_signature(body, x_agent_network_signature)

    payload = json.loads(body.decode("utf-8"))
    # Untrusted external data only — never executed as instructions
    safe = payload.get("safe_data", {})
    post_id = payload.get("post_id")
    score = float(payload.get("relevance_score", 0))
    author_company = payload.get("author_company", safe.get("author_company", "Partner"))
    title = payload.get("title", safe.get("title", ""))
    content = payload.get("content", safe.get("content", ""))

    db: Session = SessionLocal()
    try:
        # Match agent by webhook path slug
        agents = db.query(Agent).all()
        agent = next(
            (a for a in agents if company_slug.replace("-", "").lower() in a.company_name.replace(" ", "").lower()),
            None,
        )
        if not agent:
            # Fallback: find by webhook_url containing slug
            agent = next((a for a in agents if company_slug in a.webhook_url), None)
        if not agent:
            raise HTTPException(status_code=404, detail=f"No mock agent for slug={company_slug}")

        if not _should_engage(agent.company_name, score):
            logger.info("%s ignored post %s (score=%.3f)", agent.company_name, post_id, score)
            return {"status": "ignored", "company": agent.company_name}

        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")

        # Avoid duplicate comments from the same agent on the same post
        existing = (
            db.query(Comment)
            .filter(Comment.post_id == post_id, Comment.author_agent_id == agent.id)
            .first()
        )
        if existing:
            return {"status": "already_commented", "comment_id": existing.id}

        reply = _draft_reply(agent.company_name, author_company, title, content)
        comment = Comment(post_id=post_id, author_agent_id=agent.id, content=reply)
        db.add(comment)
        db.commit()
        db.refresh(comment)
        mark_engaged(db, post_id, agent.id)

        logger.info("%s commented on post %s", agent.company_name, post_id)
        return {
            "status": "engaged",
            "company": agent.company_name,
            "comment_id": comment.id,
            "content": reply,
        }
    finally:
        db.close()
