"""Seed demo agents: Swiggy, Flipkart, PhonePe, HDFC Bank, Bajaj Auto."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Agent, AgentStatus
from app.services.embeddings import get_embedder

DEMO_AGENTS = [
    {
        "company_name": "Swiggy",
        "industry_category": "Food Delivery",
        "slug": "swiggy",
        "interest_manifesto": (
            "Interested in payment partnerships, festive food delivery promotions, "
            "restaurant merchant offers, UPI cashback collabs, and logistics capacity."
        ),
    },
    {
        "company_name": "Flipkart",
        "industry_category": "E-commerce",
        "slug": "flipkart",
        "interest_manifesto": (
            "Interested in e-commerce festive sales, SuperCoins partnerships, "
            "card and UPI checkout offers, and cross-brand shopping promotions."
        ),
    },
    {
        "company_name": "PhonePe",
        "industry_category": "Payments & Fintech",
        "slug": "phonepe",
        "interest_manifesto": (
            "Interested in e-commerce promotions, food delivery festive discounts, "
            "card payment partnerships, merchant cashback opportunities, and UPI offers."
        ),
    },
    {
        "company_name": "HDFC Bank",
        "industry_category": "Banking & Cards",
        "slug": "hdfc-bank",
        "interest_manifesto": (
            "Interested in credit card cashback on food delivery and shopping, "
            "festive merchant partnerships, EMI offers, and payment co-branded campaigns."
        ),
    },
    {
        "company_name": "Bajaj Auto",
        "industry_category": "Automotive",
        "slug": "bajaj-auto",
        "interest_manifesto": (
            "Interested in two-wheeler launches, automotive financing, dealership "
            "promotions, EV incentives, spare-parts supply chain, and insurance partners."
        ),
        "blocked_industries": ["Food Delivery"],
    },
]


def seed_demo_agents(db: Session, reset: bool = False) -> list[Agent]:
    settings = get_settings()
    embedder = get_embedder()

    if reset:
        db.query(Agent).delete()
        db.commit()

    created: list[Agent] = []
    for spec in DEMO_AGENTS:
        existing = db.query(Agent).filter(Agent.company_name == spec["company_name"]).first()
        if existing:
            created.append(existing)
            continue

        manifesto = spec["interest_manifesto"]
        embedding = embedder.embed(f"{spec['company_name']}. {spec['industry_category']}. {manifesto}")
        agent = Agent(
            company_name=spec["company_name"],
            industry_category=spec["industry_category"],
            webhook_url=f"{settings.mock_agent_base_url}/{spec['slug']}/webhook",
            interest_manifesto=manifesto,
            interest_embedding=embedder.serialize(embedding),
            blocked_industries=",".join(spec.get("blocked_industries", [])),
            status=AgentStatus.ACTIVE,
            trust_score=1.0,
        )
        db.add(agent)
        created.append(agent)

    db.commit()
    for agent in created:
        db.refresh(agent)
    return created
