from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def estimate_tokens(text: str) -> int:
    text = (text or "").strip()
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


@dataclass
class ContextItem:
    item_id: str
    layer: str
    source_type: str
    title: str
    content: str
    priority: float = 0.0
    confidence: float = 0.0
    must_keep: bool = False
    include_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.content or "")

    @property
    def token_count(self) -> int:
        return estimate_tokens(self.content)

    def to_record(self, *, included: bool, reason: str) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "layer": self.layer,
            "source_type": self.source_type,
            "title": self.title,
            "chars": self.char_count,
            "tokens": self.token_count,
            "priority": round(float(self.priority), 4),
            "confidence": round(float(self.confidence), 4),
            "must_keep": bool(self.must_keep),
            "included": bool(included),
            "reason": reason,
            "metadata": dict(self.metadata),
            "provenance": dict(self.provenance),
        }


@dataclass
class PromptAssemblyReport:
    task_id: str
    trace_id: str
    total_context_budget: int
    bootstrap_budget: int
    relevant_budget: int
    cold_budget: int
    bootstrap_tokens_used: int = 0
    relevant_tokens_used: int = 0
    cold_tokens_used: int = 0
    bootstrap_chars_used: int = 0
    relevant_chars_used: int = 0
    cold_chars_used: int = 0
    items_included: list[dict[str, Any]] = field(default_factory=list)
    items_excluded: list[dict[str, Any]] = field(default_factory=list)
    trimming_decisions: list[str] = field(default_factory=list)
    swarm_metadata_consulted: bool = False
    cold_archive_opened: bool = False
    stayed_under_budget: bool = True
    retrieval_confidence: str = "low"

    def total_tokens_used(self) -> int:
        return self.bootstrap_tokens_used + self.relevant_tokens_used + self.cold_tokens_used

    def total_chars_used(self) -> int:
        return self.bootstrap_chars_used + self.relevant_chars_used + self.cold_chars_used

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "total_context_budget": self.total_context_budget,
            "bootstrap_budget": self.bootstrap_budget,
            "relevant_budget": self.relevant_budget,
            "cold_budget": self.cold_budget,
            "bootstrap_tokens_used": self.bootstrap_tokens_used,
            "relevant_tokens_used": self.relevant_tokens_used,
            "cold_tokens_used": self.cold_tokens_used,
            "bootstrap_chars_used": self.bootstrap_chars_used,
            "relevant_chars_used": self.relevant_chars_used,
            "cold_chars_used": self.cold_chars_used,
            "total_tokens_used": self.total_tokens_used(),
            "total_chars_used": self.total_chars_used(),
            "items_included": list(self.items_included),
            "items_excluded": list(self.items_excluded),
            "trimming_decisions": list(self.trimming_decisions),
            "swarm_metadata_consulted": self.swarm_metadata_consulted,
            "cold_archive_opened": self.cold_archive_opened,
            "stayed_under_budget": self.stayed_under_budget,
            "retrieval_confidence": self.retrieval_confidence,
        }

    def render_human(self) -> str:
        lines = [
            f"Task: {self.task_id}",
            f"Trace: {self.trace_id}",
            f"Budgets: total={self.total_context_budget} bootstrap={self.bootstrap_budget} relevant={self.relevant_budget} cold={self.cold_budget}",
            f"Used: total={self.total_tokens_used()} bootstrap={self.bootstrap_tokens_used} relevant={self.relevant_tokens_used} cold={self.cold_tokens_used}",
            f"Retrieval confidence: {self.retrieval_confidence}",
            f"Swarm metadata consulted: {'yes' if self.swarm_metadata_consulted else 'no'}",
            f"Cold archive opened: {'yes' if self.cold_archive_opened else 'no'}",
            f"Under budget: {'yes' if self.stayed_under_budget else 'no'}",
        ]
        if self.trimming_decisions:
            lines.extend(["", "Trimming decisions:"])
            lines.extend(f"- {entry}" for entry in self.trimming_decisions)
        if self.items_included:
            lines.extend(["", "Included items:"])
            lines.extend(
                f"- [{item['layer']}] {item['source_type']}: {item['title']} ({item['tokens']} tokens)"
                for item in self.items_included
            )
        if self.items_excluded:
            lines.extend(["", "Excluded items:"])
            lines.extend(
                f"- [{item['layer']}] {item['source_type']}: {item['title']} ({item['reason']})"
                for item in self.items_excluded
            )
        return "\n".join(lines)
