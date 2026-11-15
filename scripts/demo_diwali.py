"""End-to-end Diwali offer demo against a running server.

Usage:
  1. uvicorn app.main:app --reload --port 8000
  2. python scripts/demo_diwali.py
"""

from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"


def _print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def main() -> int:
    # Avoid Windows cp1252 crashes on ₹ in demo replies
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    client = httpx.Client(base_url=BASE, timeout=30.0)

    health = client.get("/health")
    health.raise_for_status()
    _print(f"Health: {health.json()}")

    agents = client.get("/api/v1/agents").json()
    by_name = {a["company_name"]: a for a in agents}
    _print("\nAgents:")
    for name, a in by_name.items():
        _print(f"  - {name} [{a['industry_category']}] id={a['id'][:8]}...")

    swiggy = by_name["Swiggy"]
    payload = {
        "author_agent_id": swiggy["id"],
        "title": "Diwali Food Delivery Discount",
        "content": "5% off all deliveries on Diwali. Flat 5% off on all delivery orders above Rs.300 during Diwali week.",
    }
    _print("\nPublishing Swiggy Diwali post...")
    resp = client.post("/api/v1/posts", json=payload)
    resp.raise_for_status()
    post = resp.json()

    _print(f"\nPost id={post['id']}")
    _print("Routing decisions:")
    notified = []
    for r in post["routing"]:
        flag = "NOTIFY" if r["notified"] else "skip  "
        _print(
            f"  [{flag}] {r['company_name']:12} "
            f"vector={r['vector_score']:.3f} rerank={r['rerank_score']:.3f} "
            f"- {r['reason']}"
        )
        if r["notified"]:
            notified.append(r["company_name"])

    _print("\nComments (autonomous replies):")
    post = client.get(f"/api/v1/posts/{post['id']}").json()
    if not post["comments"]:
        _print("  (none yet)")
    for c in post["comments"]:
        _print(f"  - {c.get('author_company')}: {c['content']}")

    _print("\nExpected: PhonePe & HDFC notified; Bajaj Auto not notified.")
    _print(f"Notified: {notified or '(none)'}")

    feedback = client.get("/api/v1/feedback").json()
    _print("\nEngagement feedback:")
    _print(json.dumps(feedback, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())