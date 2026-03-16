from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.context_understanding import WorkingInterpretation, expand_unfinished_for_self
from core.input_normalizer import NormalizationResult, normalize_user_text
from storage.dialogue_memory import (
    get_dialogue_session,
    recent_dialogue_turns,
    record_dialogue_turn,
    session_lexicon,
    update_dialogue_session,
    upsert_lexicon_term,
)

_AMBIGUOUS_REFERENCE_RE = re.compile(r"\b(it|they|them|this|that|this one|that one|other one)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9_'\-]+")
_FOLLOWUP_RE = re.compile(
    r"\b(what do you mean|how so|why that|go on|continue|expand|tell me more|ok do that|okay do that|yes do that|do that|"
    r"can you sharpen|can you continue|can you unpack|explain that|and then|what about that)\b",
    re.IGNORECASE,
)
_COMMITMENT_RE = re.compile(
    r"^(?:i(?:['’]ll| will| can| am going to|['’]m going to)\b|let me\b|next i can\b)",
    re.IGNORECASE,
)

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
_CONTINUITY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "by",
    "do",
    "for",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "we",
    "what",
    "why",
    "you",
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
    working_interpretation: WorkingInterpretation | None = None

    def as_context(self) -> dict[str, object]:
        ctx = {
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
        if self.working_interpretation and self.working_interpretation.grounding_note:
            ctx["short_input_grounding_note"] = self.working_interpretation.grounding_note
        return ctx

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


def _continuity_tokens(text: str) -> set[str]:
    return {
        token
        for token in _WORD_RE.findall(str(text or "").lower())
        if len(token) > 2 and token not in _CONTINUITY_STOPWORDS
    }


def _has_continuity_overlap(left: str, right: str) -> bool:
    left_tokens = _continuity_tokens(left)
    right_tokens = _continuity_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    return bool(left_tokens & right_tokens)


def _unique_strings(values: list[str], *, limit: int = 4, max_chars: int = 180) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        clean = " ".join(str(item or "").split()).strip().rstrip(".!?")
        if not clean:
            continue
        lowered = clean.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(clean[:max_chars])
        if len(normalized) >= limit:
            break
    return normalized


def _sentence_fragments(text: str) -> list[str]:
    return [fragment.strip() for fragment in re.split(r"(?:\n+|(?<=[.!?])\s+)", str(text or "")) if fragment.strip()]


def _extract_assistant_commitments(text: str) -> list[str]:
    commitments: list[str] = []
    for fragment in _sentence_fragments(text):
        if not _COMMITMENT_RE.search(fragment):
            continue
        commitment = _COMMITMENT_RE.sub("", fragment, count=1).strip(" :-")
        if len(commitment) < 6:
            continue
        commitments.append(commitment)
    return _unique_strings(commitments, limit=4)


def _latest_assistant_commitments(recent_turns: list[dict[str, object]]) -> list[str]:
    for turn in recent_turns:
        if str(turn.get("speaker_role") or "").strip().lower() != "assistant":
            continue
        commitments = _extract_assistant_commitments(str(turn.get("reconstructed_input") or turn.get("raw_input") or ""))
        if commitments:
            return commitments
    return []


def _is_followup_continuation(text: str, *, reference_targets: list[str]) -> bool:
    lower = str(text or "").strip().lower()
    if reference_targets:
        return True
    if _FOLLOWUP_RE.search(lower):
        return True
    return lower in {"yes", "yeah", "yep", "ok", "okay", "do that", "go on", "continue"}


def _infer_stance_and_emotional_tone(text: str, *, intent_mode: str) -> tuple[str | None, str | None]:
    lower = str(text or "").strip().lower()
    stance: str | None = None
    emotional_tone: str | None = None

    if any(marker in lower for marker in ["fucking", "stuck", "annoyed", "frustrated", "wtf", "hate", "broken", "tired of"]):
        emotional_tone = "frustrated"
    elif any(marker in lower for marker in ["urgent", "asap", "right now", "now", "immediately"]):
        emotional_tone = "urgent"
    elif intent_mode == "question":
        emotional_tone = "curious"

    if any(marker in lower for marker in ["not convinced", "skeptical", "doubt", "are you sure", "really", "actually"]):
        stance = "skeptical"
    elif any(marker in lower for marker in ["need", "must", "want", "help me", "decide", "deciding", "fix", "make", "build"]):
        stance = "goal_driven"
    elif intent_mode == "question":
        stance = "exploratory"

    return stance, emotional_tone


def _derive_continuity_state(
    *,
    normalized_text: str,
    current_topics: list[str],
    reference_targets: list[str],
    intent_mode: str,
    session_state: dict[str, object],
    recent_turns: list[dict[str, object]],
) -> dict[str, object]:
    session_topics = [str(item) for item in list(session_state.get("topic_hints") or []) if str(item or "").strip()]
    last_subject = str(session_state.get("last_subject") or "").strip()
    existing_goal = str(session_state.get("current_user_goal") or "").strip()
    existing_commitments = [str(item) for item in list(session_state.get("assistant_commitments") or []) if str(item or "").strip()]
    existing_unresolved = [str(item) for item in list(session_state.get("unresolved_followups") or []) if str(item or "").strip()]
    existing_stance = str(session_state.get("user_stance") or "").strip() or None
    existing_emotional_tone = str(session_state.get("emotional_tone") or "").strip() or None

    followup_like = _is_followup_continuation(normalized_text, reference_targets=reference_targets)
    topical_overlap = bool({item.lower() for item in current_topics + reference_targets} & {item.lower() for item in session_topics})
    lexical_overlap = any(
        _has_continuity_overlap(normalized_text, candidate)
        for candidate in [existing_goal, last_subject, *existing_commitments, *existing_unresolved]
        if candidate
    )
    same_thread = followup_like or topical_overlap or lexical_overlap

    next_subject = (current_topics or reference_targets or ([last_subject] if same_thread and last_subject else []) or [None])[0]
    if same_thread:
        merged_topics = list(dict.fromkeys(current_topics + reference_targets + session_topics))[:8]
    else:
        merged_topics = list(dict.fromkeys(current_topics + reference_targets))[:8]

    if same_thread and (followup_like or not _continuity_tokens(normalized_text)) and existing_goal:
        current_user_goal = existing_goal
    else:
        current_user_goal = " ".join(str(normalized_text or "").split()).strip()[:240] or existing_goal or None

    latest_commitments = _latest_assistant_commitments(recent_turns)
    if same_thread:
        assistant_commitments = _unique_strings(latest_commitments or existing_commitments)
        unresolved_followups = _unique_strings(assistant_commitments or existing_unresolved)
    else:
        assistant_commitments = []
        unresolved_followups = []

    current_stance, current_emotional_tone = _infer_stance_and_emotional_tone(
        normalized_text,
        intent_mode=intent_mode,
    )
    if same_thread:
        user_stance = current_stance or existing_stance
        emotional_tone = current_emotional_tone or existing_emotional_tone
    else:
        user_stance = current_stance
        emotional_tone = current_emotional_tone

    return {
        "same_thread": same_thread,
        "next_subject": str(next_subject) if next_subject else None,
        "merged_topics": merged_topics,
        "current_user_goal": current_user_goal,
        "assistant_commitments": assistant_commitments,
        "unresolved_followups": unresolved_followups,
        "user_stance": user_stance,
        "emotional_tone": emotional_tone,
    }


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
    continuity_turns = recent_dialogue_turns(session_id, limit=8, speaker_roles=("user", "assistant"))
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

    working = expand_unfinished_for_self(
        user_input,
        normalized_text=normalized.normalized_text,
        topic_hints=current_topics,
        reference_targets=reference_targets,
        recent_turns=turns,
        quality_flags=quality_flags,
    )
    reconstructed = working.expanded if working.expanded else normalized.normalized_text
    if reference_targets and "Context subject:" not in reconstructed:
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
    continuity = _derive_continuity_state(
        normalized_text=normalized.normalized_text,
        current_topics=current_topics,
        reference_targets=reference_targets,
        intent_mode=intent_mode,
        session_state=session,
        recent_turns=continuity_turns,
    )
    update_dialogue_session(
        session_id,
        last_subject=str(continuity.get("next_subject") or "") or None,
        topic_hints=list(continuity.get("merged_topics") or []),
        last_intent_mode=intent_mode,
        current_user_goal=str(continuity.get("current_user_goal") or "") or None,
        assistant_commitments=list(continuity.get("assistant_commitments") or []),
        unresolved_followups=list(continuity.get("unresolved_followups") or []),
        user_stance=str(continuity.get("user_stance") or "") or None,
        emotional_tone=str(continuity.get("emotional_tone") or "") or None,
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
        working_interpretation=working,
    )
