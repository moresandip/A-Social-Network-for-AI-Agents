"""Embedding backends: local hashing (default) or OpenAI when API key + openai pkg set."""

from __future__ import annotations

import hashlib
import json
import math
import re
from functools import lru_cache

from app.config import get_settings

_TOKEN_RE = re.compile(r"[a-z0-9₹%]+", re.IGNORECASE)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(a: list[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


class LocalHashEmbedder:
    """Deterministic hashing embedder — no model download, no numpy."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _tokenize(text)
        if not tokens:
            return vec

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            weight = 1.0 + min(len(token), 12) / 12.0
            vec[idx] += sign * weight

        n = _norm(vec)
        if n > 0:
            vec = [x / n for x in vec]
        return vec


class OpenAIEmbedder:
    def __init__(self, api_key: str) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = "text-embedding-3-small"

    def embed(self, text: str) -> list[float]:
        response = self.client.embeddings.create(model=self.model, input=text)
        return list(response.data[0].embedding)


class Embedder:
    def __init__(self) -> None:
        settings = get_settings()
        if settings.use_openai:
            try:
                self._backend = OpenAIEmbedder(settings.openai_api_key)
                self.backend_name = "openai:text-embedding-3-small"
            except ImportError:
                self._backend = LocalHashEmbedder(settings.embedding_dim)
                self.backend_name = "local:hashing (openai pkg missing)"
        else:
            self._backend = LocalHashEmbedder(settings.embedding_dim)
            self.backend_name = "local:hashing"

    def embed(self, text: str) -> list[float]:
        return self._backend.embed(text)

    @staticmethod
    def serialize(vector: list[float]) -> str:
        return json.dumps(vector)

    @staticmethod
    def deserialize(payload: str) -> list[float]:
        return json.loads(payload or "[]")

    @staticmethod
    def cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        denom = _norm(a) * _norm(b)
        if denom == 0:
            return 0.0
        return _dot(a, b) / denom

    def shift_away(self, interest: list[float], post: list[float], alpha: float = 0.05) -> list[float]:
        """Negative feedback: nudge agent interest away from ignored post topics."""
        if not interest or not post or len(interest) != len(post):
            return interest
        updated = [i - alpha * p for i, p in zip(interest, post)]
        n = _norm(updated)
        if n > 0:
            updated = [x / n for x in updated]
        return updated


@lru_cache
def get_embedder() -> Embedder:
    return Embedder()


def lexical_overlap(post_text: str, manifesto: str) -> float:
    """Jaccard-ish lexical signal for hybrid Stage-2 scoring."""
    a = set(_tokenize(post_text))
    b = set(_tokenize(manifesto))
    if not a or not b:
        return 0.0
    return len(a & b) / math.sqrt(len(a) * len(b))
