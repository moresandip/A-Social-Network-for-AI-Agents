"""Lightweight in-process async task queue for webhook delivery.

Architecture
------------
Instead of sending webhooks inline inside the HTTP request cycle, we push
notification jobs onto an asyncio.Queue.  A single background worker coroutine
(started at app startup) drains the queue with configurable concurrency.

Production upgrade path
-----------------------
Swap `_queue` for a Redis Streams / RabbitMQ consumer by replacing
`enqueue_notification` and `_worker` — the caller (notifications.py) stays
unchanged.

Queue item format
-----------------
{
    "post_id": str,
    "agent_id": str,
    "webhook_url": str,
    "payload_body": bytes,
    "signature": str,
    "attempt": int,
}
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# In-process queue — replace with Redis client for production scale
_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10_000)

MAX_RETRIES = 3
RETRY_DELAYS = [1, 3, 10]  # seconds between retries (exponential-ish backoff)


def build_signed_payload(envelope: dict, secret: str) -> tuple[bytes, str]:
    """Return (body_bytes, hmac_hex)."""
    body = json.dumps({**envelope, "delivered_at": datetime.now(timezone.utc).isoformat()},
                      ensure_ascii=False).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, sig


async def enqueue_notification(
    post_id: str,
    agent_id: str,
    webhook_url: str,
    envelope: dict,
) -> None:
    """Push a webhook delivery job onto the queue (non-blocking)."""
    settings = get_settings()
    body, sig = build_signed_payload(envelope, settings.webhook_secret)
    item = {
        "post_id": post_id,
        "agent_id": agent_id,
        "webhook_url": webhook_url,
        "payload_body": body,
        "signature": sig,
        "attempt": 0,
    }
    try:
        _queue.put_nowait(item)
        logger.debug("Enqueued webhook for agent=%s post=%s", agent_id[:8], post_id[:8])
    except asyncio.QueueFull:
        logger.warning("Task queue full — dropping webhook for agent=%s", agent_id[:8])


async def _deliver_one(item: dict, client: httpx.AsyncClient) -> bool:
    """Try a single delivery. Returns True on success."""
    try:
        response = await client.post(
            item["webhook_url"],
            content=item["payload_body"],
            headers={
                "Content-Type": "application/json",
                "X-Agent-Network-Signature": item["signature"],
                "X-Agent-Network-Event": "post.relevant",
            },
        )
        if response.status_code < 400:
            logger.info(
                "Webhook delivered agent=%s post=%s attempt=%d",
                item["agent_id"][:8], item["post_id"][:8], item["attempt"] + 1,
            )
            return True
        logger.warning(
            "Webhook HTTP %d for agent=%s — will retry",
            response.status_code, item["agent_id"][:8],
        )
        return False
    except Exception as exc:
        logger.warning("Webhook error agent=%s: %s", item["agent_id"][:8], exc)
        return False


async def _worker() -> None:
    """Background coroutine: drain the queue with retry + backoff."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            item = await _queue.get()
            success = await _deliver_one(item, client)
            if not success and item["attempt"] < MAX_RETRIES - 1:
                # Re-enqueue with incremented attempt after backoff
                delay = RETRY_DELAYS[min(item["attempt"], len(RETRY_DELAYS) - 1)]
                await asyncio.sleep(delay)
                item["attempt"] += 1
                await _queue.put(item)
            elif not success:
                logger.error(
                    "Webhook permanently failed after %d attempts for agent=%s post=%s",
                    MAX_RETRIES, item["agent_id"][:8], item["post_id"][:8],
                )
            _queue.task_done()


def start_worker(n_workers: int = 3) -> list[asyncio.Task]:
    """Spawn N concurrent worker coroutines. Call once at app startup."""
    tasks = [asyncio.create_task(_worker(), name=f"webhook-worker-{i}") for i in range(n_workers)]
    logger.info("Started %d async webhook worker(s)", n_workers)
    return tasks
