"""Multi-turn conversation mode for interactive agent sessions.

Maintains conversation state and allows back-and-forth dialogue
with the NULLA swarm through any channel.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ConversationTurn:
    role: str  # "user" or "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Conversation:
    conversation_id: str
    channel_type: str
    user_id: str
    turns: list[ConversationTurn] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    max_turns: int = 50

    def add_user_message(self, text: str) -> None:
        self.turns.append(ConversationTurn(role="user", content=text))
        self.last_active = time.time()
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

    def add_assistant_response(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        self.turns.append(ConversationTurn(role="assistant", content=text, metadata=metadata or {}))
        self.last_active = time.time()

    def get_context_window(self, max_tokens_estimate: int = 4000) -> list[dict[str, str]]:
        """Return recent turns that fit within approximate token budget."""
        messages = []
        char_budget = max_tokens_estimate * 4  # Rough chars-per-token estimate
        used = 0
        for turn in reversed(self.turns):
            if used + len(turn.content) > char_budget:
                break
            messages.insert(0, {"role": turn.role, "content": turn.content})
            used += len(turn.content)
        return messages

    @property
    def is_stale(self) -> bool:
        return time.time() - self.last_active > 3600  # 1 hour


class ConversationManager:
    """Manages active conversations across channels."""

    def __init__(self, max_conversations: int = 1000) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._max_conversations = max_conversations

    def get_or_create(self, channel_type: str, user_id: str) -> Conversation:
        key = f"{channel_type}:{user_id}"
        if key not in self._conversations:
            # Evict stale conversations if at capacity
            if len(self._conversations) >= self._max_conversations:
                self._evict_stale()
            self._conversations[key] = Conversation(
                conversation_id=str(uuid.uuid4()),
                channel_type=channel_type,
                user_id=user_id,
            )
        return self._conversations[key]

    def _evict_stale(self) -> None:
        stale_keys = [k for k, v in self._conversations.items() if v.is_stale]
        for key in stale_keys:
            del self._conversations[key]
        # If still over capacity, remove oldest
        if len(self._conversations) >= self._max_conversations:
            oldest_key = min(self._conversations, key=lambda k: self._conversations[k].last_active)
            del self._conversations[oldest_key]

    def end_conversation(self, channel_type: str, user_id: str) -> None:
        key = f"{channel_type}:{user_id}"
        self._conversations.pop(key, None)

    def active_count(self) -> int:
        return len(self._conversations)

    def status(self) -> dict[str, Any]:
        return {
            "active_conversations": self.active_count(),
            "stale_conversations": sum(1 for c in self._conversations.values() if c.is_stale),
        }


_MANAGER = ConversationManager()


def get_conversation_manager() -> ConversationManager:
    return _MANAGER
