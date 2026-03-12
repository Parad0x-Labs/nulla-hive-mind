from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.input_normalizer import NormalizationResult, normalize_user_text
from storage.dialogue_memory import (
    get_dialogue_session,
    record_dialogue_turn,
    recent_dialogue_turns,
    session_lexicon,
    update_dialogue_session,
    upsert_lexicon_term,
)


_AMBIGUOUS_REFERENCE_RE = re.compile(r"\b(it|they|them|this|that|this one|that one|other one)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9_'\-]+")

_PHRASE_HINTS = [
    ("knowledge shard", ("knowledge", "shard")),
    ("swarm memory", ("swarm", "memory")),
    ("meet and greet", ("meet", "greet")),
    ("presence lease", ("presence", "lease")),
    ("fetch route", ("fetch", "route")),
    ("replica count", ("replica", "count")),
    ("security hardening", ("security", "harden")),
    ("password leak", ("password", "leak")),
    ("telegram bot", ("telegram", "bot")),
    ("calendar workflow", ("calendar",)),
    ("email workflow", ("email",)),
    ("discord integration", ("discord",)),
    ("openclaw integration", ("openclaw",)),
]

_TOPIC_TERMS = {
    "agent",
    "bot",
    "credit",
    "credits",
    "calendar",
    "daemon",
    "discord",
    "email",
    "fetch",
    "freshness",
    "helper",
    "identity",
    "inbox",
    "knowledge",
    "lease",
    "liquefy",
    "lore",
    "memory",
    "meeting",
    "mesh",
    "node",
    "openclaw",
    "onboarding",
    "password",
    "persona",
    "presence",
    "protect",
    "replica",
    "replication",
    "route",
    "schedule",
    "security",
    "server",
    "setup",
    "shard",
    "standalone",
    "swarm",
    "telegram",
    "timeout",
    "transport",
}


@dataclass
class HumanInputInterpretation:
    raw_text: str
    normalized_text: str
    reconstructed_text: str
    intent_mode: str
    topic_hints: list[str]
    reference_targets: list[str]
    understanding_confidence: float
    quality_flags: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    turn_id: str | None = None

    def as_context(self) -> dict[str, object]:
        return {
            "topic_hints": list(self.topic_hints),
            "reference_targets": list(self.reference_targets),
            "understanding_confidence": float(self.understanding_confidence),
            "quality_flags": list(self.quality_flags),
            "intent_mode": self.intent_mode,
            "normalized_text": self.normalized_text,
            "reconstructed_text": self.reconstructed_text,
            "needs_clarification": bool(self.needs_clarification),
            "turn_id": self.turn_id,
            "interpretation_summary": self.interpretation_summary,
            "model_prompt_hints": {
                "ambiguity": "high" if self.understanding_confidence < 0.45 else "medium" if self.understanding_confidence < 0.7 else "low",
                "topic_count": len(self.topic_hints),
                "reference_count": len(self.reference_targets),
            },
        }

    @property
    def interpretation_summary(self) -> str:
        topic = ", ".join(self.reference_targets or self.topic_hints[:2]) or "the current request"
        return f"{self.intent_mode} about {topic}"


def runtime_session_id(*, device: str, persona_id: str) -> str:
    safe_device = re.sub(r"[^a-zA-Z0-9_\-]+", "-", device).strip("-").lower() or "local"
    safe_persona = re.sub(r"[^a-zA-Z0-9_\-]+", "-", persona_id).strip("-").lower() or "default"
    return f"{safe_device}:{safe_persona}"


def learn_user_shorthand(term: str, canonical: str, *, session_id: str | None = None) -> None:
    upsert_lexicon_term(term, canonical, scope=session_id or "global", source="manual")


def _extract_topic_hints(text: str) -> list[str]:
    lower = text.lower()
    hints: list[str] = []
    for phrase, required in _PHRASE_HINTS:
        if all(token in lower for token in required):
            hints.append(phrase)
    for token in _WORD_RE.findall(lower):
        if token in _TOPIC_TERMS and token not in hints:
            hints.append(token)
        if len(hints) >= 8:
            break
    return hints[:8]


def _infer_intent_mode(text: str) -> str:
    lower = text.lower().strip()
    if lower.endswith("?") or re.match(r"^(what|why|how|can|should|will|would|is|are)\b", lower):
        return "question"
    if any(marker in lower for marker in ["please", "need", "want", "make", "create", "fix", "harden", "check"]):
        return "request"
    return "statement"


def _resolve_reference_targets(
    normalized_text: str,
    *,
    current_topics: list[str],
    session_state: dict[str, object],
    recent_turns: list[dict[str, object]],
) -> tuple[list[str], list[str]]:
    quality_flags: list[str] = []
    if not _AMBIGUOUS_REFERENCE_RE.search(normalized_text):
        return [], quality_flags

    targets: list[str] = []
    last_subject = str(session_state.get("last_subject") or "").strip()
    if last_subject:
        targets.append(last_subject)
    for turn in recent_turns:
        for hint in turn.get("topic_hints") or []:
            if hint not in targets:
                targets.append(str(hint))
            if len(targets) >= 3:
                break
        if len(targets) >= 3:
            break
    if current_topics:
        for hint in current_topics:
            if hint not in targets:
                targets.append(hint)
            if len(targets) >= 3:
                break

    if not targets:
        quality_flags.append("ambiguous_reference")
    return targets[:3], quality_flags


def _score_understanding(
    normalized: NormalizationResult,
    *,
    current_topics: list[str],
    reference_targets: list[str],
    reference_flags: list[str],
) -> float:
    score = 0.58
    if current_topics:
        score += 0.14
    if reference_targets:
        score += 0.10
    if normalized.replacements:
        score += 0.04
    if "fragmented" in normalized.quality_flags:
        score -= 0.08
    if "short_input" in normalized.quality_flags:
        score -= 0.06
    if "typo_heavy" in normalized.quality_flags:
        score -= 0.05
    if "shorthand_heavy" in normalized.quality_flags:
        score -= 0.03
    if "ambiguous_reference" in reference_flags:
        score -= 0.18
    return max(0.20, min(0.95, score))


def adapt_user_input(user_input: str, *, session_id: str) -> HumanInputInterpretation:
    session = get_dialogue_session(session_id)
    lexicon = session_lexicon(session_id)
    normalized = normalize_user_text(user_input, session_lexicon=lexicon)
    turns = recent_dialogue_turns(session_id, limit=3)
    current_topics = _extract_topic_hints(normalized.normalized_text)
    reference_targets, reference_flags = _resolve_reference_targets(
        normalized.normalized_text,
        current_topics=current_topics,
        session_state=session,
        recent_turns=turns,
    )
    intent_mode = _infer_intent_mode(normalized.normalized_text)
    quality_flags = list(dict.fromkeys(list(normalized.quality_flags) + reference_flags))
    confidence = _score_understanding(
        normalized,
        current_topics=current_topics,
        reference_targets=reference_targets,
        reference_flags=reference_flags,
    )
    needs_clarification = confidence < 0.45 or "ambiguous_reference" in reference_flags

    reconstructed = normalized.normalized_text
    if reference_targets:
        reconstructed = f"{reconstructed} Context subject: {', '.join(reference_targets[:2])}."

    turn_id = record_dialogue_turn(
        session_id,
        raw_input=user_input,
        normalized_input=normalized.normalized_text,
        reconstructed_input=reconstructed,
        topic_hints=current_topics,
        reference_targets=reference_targets,
        understanding_confidence=confidence,
        quality_flags=quality_flags,
    )
    next_subject = (current_topics or reference_targets or session.get("topic_hints") or [None])[0]
    merged_topics = list(dict.fromkeys(current_topics + reference_targets + list(session.get("topic_hints") or [])))[:8]
    update_dialogue_session(
        session_id,
        last_subject=str(next_subject) if next_subject else None,
        topic_hints=merged_topics,
        last_intent_mode=intent_mode,
    )

    return HumanInputInterpretation(
        raw_text=user_input,
        normalized_text=normalized.normalized_text,
        reconstructed_text=reconstructed,
        intent_mode=intent_mode,
        topic_hints=current_topics,
        reference_targets=reference_targets,
        understanding_confidence=confidence,
        quality_flags=quality_flags,
        needs_clarification=needs_clarification,
        turn_id=turn_id,
    )
