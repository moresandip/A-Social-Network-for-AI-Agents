"""Multi-stage relevance routing engine.

Stage 1 — SQL/metadata hard filters
Stage 2 — Dense vector + lexical hybrid retrieval (top K)
Stage 3 — Cross-encoder-style / heuristic LLM judge rerank + threshold
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Agent, AgentStatus, Post
from app.services.embeddings import get_embedder, lexical_overlap


@dataclass
class Candidate:
    agent: Agent
    vector_score: float
    rerank_score: float = 0.0
    notified: bool = False
    reason: str = ""


# Lightweight industry adjacency used by Stage-3 when OpenAI is unavailable.
_INDUSTRY_AFFINITY: dict[str, set[str]] = {
    "food delivery": {"payments & fintech", "banking & cards", "e-commerce", "advertising"},
    "e-commerce": {"payments & fintech", "banking & cards", "food delivery", "logistics"},
    "payments & fintech": {"food delivery", "e-commerce", "banking & cards", "retail"},
    "banking & cards": {"payments & fintech", "e-commerce", "food delivery", "travel"},
    "automotive": {"insurance", "financing", "manufacturing"},
    "insurance": {"banking & cards", "automotive", "health"},
}


def _norm_industry(value: str) -> str:
    return value.strip().lower()


class RelevanceRouter:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.embedder = get_embedder()

    def route(self, db: Session, post: Post, author: Agent) -> list[Candidate]:
        eligible = self.stage1_filter(db, author)
        candidates = self.stage2_vector_search(post, eligible)
        final = self.stage3_rerank(post, author, candidates)
        return final

    def stage1_filter(self, db: Session, author: Agent) -> list[Agent]:
        """Hard guardrails: active only, not self, respect blocklists."""
        agents = (
            db.query(Agent)
            .filter(Agent.status == AgentStatus.ACTIVE, Agent.id != author.id)
            .all()
        )
        author_industry = _norm_industry(author.industry_category)
        eligible: list[Agent] = []
        for agent in agents:
            blocked = {_norm_industry(x) for x in (agent.blocked_industries or "").split(",") if x.strip()}
            if author_industry in blocked:
                continue
            if agent.trust_score < 0.2:
                continue
            eligible.append(agent)
        return eligible

    def stage2_vector_search(self, post: Post, eligible: list[Agent]) -> list[Candidate]:
        """Hybrid dense + lexical retrieval, keep top K."""
        post_vec = self.embedder.deserialize(post.post_embedding)
        post_text = f"{post.title}. {post.content}"
        scored: list[Candidate] = []

        for agent in eligible:
            interest_vec = self.embedder.deserialize(agent.interest_embedding)
            dense = self.embedder.cosine(post_vec, interest_vec)
            lex = lexical_overlap(post_text, agent.interest_manifesto)
            # Hybrid: dense dominates, lexical rescues synonym-poor hash collisions
            hybrid = 0.75 * max(dense, 0.0) + 0.25 * lex
            scored.append(
                Candidate(
                    agent=agent,
                    vector_score=round(hybrid, 4),
                    reason="stage2_candidate",
                )
            )

        scored.sort(key=lambda c: c.vector_score, reverse=True)
        return scored[: self.settings.routing_top_k]

    def stage3_rerank(self, post: Post, author: Agent, candidates: list[Candidate]) -> list[Candidate]:
        """High-precision rerank; notify only those above threshold (cap final_limit)."""
        if self.settings.use_openai:
            return self._rerank_openai(post, author, candidates)
        return self._rerank_heuristic(post, author, candidates)

    def _rerank_heuristic(self, post: Post, author: Agent, candidates: list[Candidate]) -> list[Candidate]:
        post_text = f"{post.title}. {post.content}".lower()
        author_ind = _norm_industry(author.industry_category)
        affinity = _INDUSTRY_AFFINITY.get(author_ind, set())

        for cand in candidates:
            manifesto = cand.agent.interest_manifesto.lower()
            target_ind = _norm_industry(cand.agent.industry_category)

            # Domain affinity boost
            affinity_score = 1.0 if target_ind in affinity else (0.4 if target_ind == author_ind else 0.1)

            # Keyword / partnership intent signals in manifesto
            intent_hits = 0
            intent_terms = [
                "payment",
                "upi",
                "cashback",
                "card",
                "offer",
                "discount",
                "merchant",
                "partnership",
                "promo",
                "festive",
                "diwali",
                "delivery",
                "e-commerce",
                "ecommerce",
                "food",
            ]
            for term in intent_terms:
                if term in manifesto and (term in post_text or term in author_ind or term in target_ind):
                    intent_hits += 1
            intent_score = min(intent_hits / 4.0, 1.0)

            # Penalize clearly unrelated verticals (e.g. automotive vs food offers)
            unrelated_penalty = 0.0
            if target_ind not in affinity and target_ind != author_ind:
                if not any(t in manifesto for t in ["payment", "cashback", "card", "merchant", "promo", "partner"]):
                    unrelated_penalty = 0.35

            final = (
                0.45 * cand.vector_score
                + 0.35 * affinity_score
                + 0.25 * intent_score
                - unrelated_penalty
            )
            cand.rerank_score = round(max(0.0, min(final, 1.0)), 4)

        candidates.sort(key=lambda c: c.rerank_score, reverse=True)
        threshold = self.settings.routing_rerank_threshold
        limit = self.settings.routing_final_limit

        notified = 0
        for cand in candidates:
            if cand.rerank_score >= threshold and notified < limit:
                cand.notified = True
                cand.reason = f"rerank_score {cand.rerank_score} >= {threshold}"
                notified += 1
            else:
                cand.notified = False
                if cand.rerank_score < threshold:
                    cand.reason = f"below threshold ({cand.rerank_score} < {threshold})"
                else:
                    cand.reason = f"truncated by final_limit={limit}"
        return candidates

    def _rerank_openai(self, post: Post, author: Agent, candidates: list[Candidate]) -> list[Candidate]:
        """Optional LLM judge for final relevance scores."""
        from openai import OpenAI

        client = OpenAI(api_key=self.settings.openai_api_key)
        # Score in small batches to keep latency bounded
        for cand in candidates:
            prompt = (
                "Score how relevant this post is for the target agent on a 0-1 scale. "
                "Reply with ONLY a float.\n\n"
                f"Author industry: {author.industry_category}\n"
                f"Post: {post.title} — {post.content}\n"
                f"Target company: {cand.agent.company_name}\n"
                f"Target industry: {cand.agent.industry_category}\n"
                f"Target interests: {cand.agent.interest_manifesto}\n"
            )
            try:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=8,
                )
                raw = (resp.choices[0].message.content or "0").strip()
                score = float("".join(ch for ch in raw if ch.isdigit() or ch == "."))
                score = max(0.0, min(score, 1.0))
            except Exception:
                score = cand.vector_score
            cand.rerank_score = round(score, 4)

        return self._apply_threshold(candidates)

    def _apply_threshold(self, candidates: list[Candidate]) -> list[Candidate]:
        candidates.sort(key=lambda c: c.rerank_score, reverse=True)
        threshold = self.settings.routing_rerank_threshold
        limit = self.settings.routing_final_limit
        notified = 0
        for cand in candidates:
            if cand.rerank_score >= threshold and notified < limit:
                cand.notified = True
                cand.reason = f"rerank_score {cand.rerank_score} >= {threshold}"
                notified += 1
            else:
                cand.notified = False
                cand.reason = (
                    f"below threshold ({cand.rerank_score} < {threshold})"
                    if cand.rerank_score < threshold
                    else f"truncated by final_limit={limit}"
                )
        return candidates
