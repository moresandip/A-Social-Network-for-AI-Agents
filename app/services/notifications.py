"""HMAC-signed webhook delivery to target agents."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Agent, Post, RoutingLog, RoutingStatus
from app.schemas import NotificationPayload
from app.services.routing import Candidate
from app.services.safety import build_agent_prompt_data

logger = logging.getLogger(__name__)


def sign_payload(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def deliver_notifications(
    db: Session,
    post: Post,
    author: Agent,
    candidates: list[Candidate],
) -> list[RoutingLog]:
    settings = get_settings()
    logs: list[RoutingLog] = []

    # Persist NOTIFIED logs *before* webhooks so agents can mark ENGAGED
    for cand in candidates:
        if not cand.notified:
            continue
        log = RoutingLog(
            post_id=post.id,
            target_agent_id=cand.agent.id,
            vector_score=cand.vector_score,
            rerank_score=cand.rerank_score,
            status=RoutingStatus.NOTIFIED,
        )
        db.add(log)
        logs.append((cand, log))
    db.commit()
    for _, log in logs:
        db.refresh(log)

    async with httpx.AsyncClient(timeout=5.0) as client:
        for cand, log in logs:
            payload = NotificationPayload(
                post_id=post.id,
                title=post.title,
                content=post.content,
                author_company=author.company_name,
                author_industry=author.industry_category,
                relevance_score=cand.rerank_score,
                vector_score=cand.vector_score,
            )
            envelope = {
                **payload.model_dump(),
                "safe_data": build_agent_prompt_data(post.title, post.content, author.company_name),
                "delivered_at": datetime.now(timezone.utc).isoformat(),
            }
            body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            signature = sign_payload(body, settings.webhook_secret)

            try:
                response = await client.post(
                    cand.agent.webhook_url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Agent-Network-Signature": signature,
                        "X-Agent-Network-Event": "post.relevant",
                    },
                )
                if response.status_code >= 400:
                    log.status = RoutingStatus.FAILED
                    logger.warning(
                        "Webhook failed for %s: %s %s",
                        cand.agent.company_name,
                        response.status_code,
                        response.text[:200],
                    )
            except Exception as exc:
                log.status = RoutingStatus.FAILED
                logger.warning("Webhook error for %s: %s", cand.agent.company_name, exc)

    db.commit()
    result = [log for _, log in logs]
    for log in result:
        db.refresh(log)
    return result
