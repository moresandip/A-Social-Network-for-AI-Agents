"""In-process mock company agents that receive webhooks and optionally comment back.

Reply drafting strategy (in priority order):
  1. OpenAI GPT  — when OPENAI_API_KEY is set, generates a contextual LLM reply.
  2. Template     — fast deterministic fallback for demos without an API key.
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

# Fallback templates when no OPENAI_API_KEY is configured
_REPLY_TEMPLATES: dict[str, str] = {
    "PhonePe": (
        "Extra \u20b950 cashback when you pay for {author} orders with PhonePe UPI on Diwali."
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

_LLM_SYSTEM_PROMPT = (
    "You are an AI agent representing {company} in the {industry} industry. "
    "You have received a business post from {author} and decided to engage. "
    "Write a SHORT (1-2 sentence) professional partnership comment that proposes "
    "a concrete collaboration or offer relevant to your company. "
    "Be specific, business-like, and do NOT use generic phrases."
)


def _draft_reply_llm(
    company: str, industry: str, author_company: str, title: str, content: str
) -> str | None:
    """Call OpenAI to generate a contextual reply. Returns None if unavailable."""
    settings = get_settings()
    if not settings.use_openai:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        system = _LLM_SYSTEM_PROMPT.format(
            company=company, industry=industry, author=author_company
        )
        user_msg = f"Post title: {title}\n\nPost content: {content}"
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=120,
            temperature=0.7,
        )
        reply = (response.choices[0].message.content or "").strip()
        return reply if reply else None
    except Exception as exc:
        logger.warning("LLM reply failed for %s, falling back to template: %s", company, exc)
        return None


def _draft_reply_template(company: str, author_company: str, title: str, content: str) -> str:
    template = _REPLY_TEMPLATES.get(
        company,
        "{company} is interested in partnering on this: {title}",
    )
    return template.format(author=author_company, company=company, title=title, content=content)


def _draft_reply(
    company: str, industry: str, author_company: str, title: str, content: str
) -> str:
    """Try LLM first; fall back to template."""
    llm_reply = _draft_reply_llm(company, industry, author_company, title, content)
    if llm_reply:
        logger.info("LLM drafted reply for %s", company)
        return llm_reply
    return _draft_reply_template(company, author_company, title, content)


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
    author_industry = payload.get("author_industry", "")
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
            agent = next((a for a in agents if company_slug in a.webhook_url), None)
        if not agent:
            raise HTTPException(status_code=404, detail=f"No mock agent for slug={company_slug}")

        if not _should_engage(agent.company_name, score):
            logger.info("%s ignored post %s (score=%.3f)", agent.company_name, post_id, score)
            return {"status": "ignored", "company": agent.company_name}

        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")

        # Avoid duplicate comments
        existing = (
            db.query(Comment)
            .filter(Comment.post_id == post_id, Comment.author_agent_id == agent.id)
            .first()
        )
        if existing:
            return {"status": "already_commented", "comment_id": existing.id}

        # Draft reply: LLM (when API key set) or template fallback
        reply = _draft_reply(
            company=agent.company_name,
            industry=agent.industry_category,
            author_company=author_company,
            title=title,
            content=content,
        )
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
            "reply_source": "llm" if getattr(get_settings(), "use_openai", False) else "template",
        }
    finally:
        db.close()
