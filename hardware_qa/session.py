"""Conversation session state.

Keeps the message history plus a cache of previously gathered tool results
so follow-up questions ("and what about the GPU?") reuse verified facts
instead of re-running commands or guessing.
"""

from __future__ import annotations

from typing import Any


class SessionState:
    def __init__(self) -> None:
        self.history: list[dict[str, str]] = []
        self._tool_cache: dict[str, dict[str, Any]] = {}

    def add_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        self.history.append({"role": "assistant", "content": text})

    def remember_tool(self, name: str, data: dict[str, Any]) -> None:
        self._tool_cache[name] = data

    def cached(self, name: str) -> dict[str, Any] | None:
        return self._tool_cache.get(name)
