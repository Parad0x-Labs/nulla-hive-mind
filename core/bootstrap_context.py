from __future__ import annotations

from typing import Any

from core import policy_engine
from core.persistent_memory import describe_session_memory_policy, load_memory_excerpt
from core.prompt_assembly_report import ContextItem
from core.runtime_paths import project_path
from core.user_preferences import describe_preferences_for_context
from storage.dialogue_memory import get_dialogue_session, recent_dialogue_turns, session_lexicon


def _compact_join(items: list[str], *, limit: int) -> str:
    picked = [item.strip() for item in items if item and item.strip()][:limit]
    return ", ".join(picked)


def _read_markdown_context(*parts: str, max_chars: int = 2200) -> str:
    path = project_path(*parts)
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars] + "\n[...]"
    return text


def build_bootstrap_context(
    *,
    persona: Any,
    task: Any,
    classification: dict[str, Any],
    interpretation: Any,
    session_id: str,
    max_lexicon_items: int = 4,
) -> list[ContextItem]:
    session_state = get_dialogue_session(session_id)
    recent_turns = recent_dialogue_turns(session_id, limit=2)
    lexicon = session_lexicon(session_id)
    quality_flags = list(getattr(interpretation, "quality_flags", []) or [])
    topic_hints = list(getattr(interpretation, "topic_hints", []) or [])
    references = list(getattr(interpretation, "reference_targets", []) or [])

    items: list[ContextItem] = [
        ContextItem(
            item_id="bootstrap-persona",
            layer="bootstrap",
            source_type="persona",
            title="Agent identity",
            content=(
                f"Persona: {persona.display_name}. Tone: {persona.tone}. "
                f"Execution style: {persona.execution_style}. Spirit anchor: {persona.spirit_anchor}."
            ),
            priority=1.0,
            confidence=0.95,
            must_keep=True,
            include_reason="stable_identity",
        ),
        ContextItem(
            item_id="bootstrap-session",
            layer="bootstrap",
            source_type="session_state",
            title="Session topic hints",
            content=(
                f"Current topics: {_compact_join(topic_hints, limit=4) or 'none'}. "
                f"References: {_compact_join(references, limit=3) or 'none'}. "
                f"Last subject: {session_state.get('last_subject') or 'none'}."
            ),
            priority=0.95,
            confidence=float(getattr(interpretation, "understanding_confidence", 0.0) or 0.0),
            must_keep=True,
            include_reason="session_grounding",
        ),
        ContextItem(
            item_id="bootstrap-task",
            layer="bootstrap",
            source_type="task_constraints",
            title="Active task constraints",
            content=(
                f"Task class: {classification.get('task_class', 'unknown')}. "
                f"Summary: {getattr(task, 'task_summary', '')}. "
                f"Risk flags: {_compact_join(list(classification.get('risk_flags') or []), limit=4) or 'none'}."
            ),
            priority=0.92,
            confidence=float(classification.get("confidence_hint", 0.0) or 0.0),
            must_keep=True,
            include_reason="task_constraints",
        ),
        ContextItem(
            item_id="bootstrap-safety",
            layer="bootstrap",
            source_type="policy",
            title="Safety mode",
            content=(
                f"Execution default: {policy_engine.get('execution.default_mode', 'advice_only')}. "
                f"Persona core locked: {bool(policy_engine.get('personality.persona_core_locked', True))}. "
                f"Understanding confidence: {float(getattr(interpretation, 'understanding_confidence', 0.0) or 0.0):.2f}."
            ),
            priority=0.88,
            confidence=0.9,
            must_keep=True,
            include_reason="safety_policy",
        ),
    ]

    # Self-knowledge: load NULLA's self-awareness document
    sk_text = _read_markdown_context("docs", "NULLA_SELF_KNOWLEDGE.md", max_chars=2200)
    if sk_text:
        items.append(
            ContextItem(
                item_id="bootstrap-self-knowledge",
                layer="bootstrap",
                source_type="self_knowledge",
                title="Self-knowledge",
                content=sk_text,
                priority=0.97,
                confidence=1.0,
                must_keep=True,
                include_reason="agent_self_awareness",
            )
        )

    # Operational doctrine: OpenClaw integrations + live internet behavior.
    doctrine_text = _read_markdown_context("docs", "NULLA_OPENCLAW_TOOL_DOCTRINE.md", max_chars=2000)
    if doctrine_text:
        items.append(
            ContextItem(
                item_id="bootstrap-openclaw-doctrine",
                layer="bootstrap",
                source_type="operating_doctrine",
                title="OpenClaw tool doctrine",
                content=doctrine_text,
                priority=0.965,
                confidence=1.0,
                include_reason="tooling_behavior_contract",
            )
        )

    # Owner identity: display name, privacy pact, and owner authority.
    try:
        from core.onboarding import load_identity
        identity = load_identity()
        agent_name = identity.get("agent_name", "")
        privacy_pact = identity.get("privacy_pact", "")
        if agent_name:
            content = (
                f"My current display name is {agent_name}. "
                "The operator can rename me or give me a nickname at any time. "
                "Internal runtime identity and display naming are separate."
            )
            if privacy_pact:
                content += f" Privacy pact: {privacy_pact}"
            items.append(
                ContextItem(
                    item_id="bootstrap-owner-identity",
                    layer="bootstrap",
                    source_type="owner_identity",
                    title="Owner identity",
                    content=content,
                    priority=0.99,
                    confidence=1.0,
                    must_keep=True,
                    include_reason="owner_identity_contract",
                )
            )
    except Exception:
        pass

    # Runtime memory: persists under NULLA_HOME/data/MEMORY.md.
    try:
        memory_excerpt = load_memory_excerpt(max_chars=2000).strip()
        if memory_excerpt:
            items.append(
                ContextItem(
                    item_id="bootstrap-runtime-memory",
                    layer="bootstrap",
                    source_type="runtime_memory",
                    title="Persistent memory",
                    content=memory_excerpt,
                    priority=0.94,
                    confidence=0.9,
                    include_reason="persistent_runtime_memory",
                )
            )
    except Exception:
        pass

    try:
        policy_text = describe_session_memory_policy(session_id)
        if policy_text:
            items.append(
                ContextItem(
                    item_id="bootstrap-session-memory-policy",
                    layer="bootstrap",
                    source_type="session_policy",
                    title="Session memory policy",
                    content=policy_text,
                    priority=0.985,
                    confidence=1.0,
                    must_keep=True,
                    include_reason="memory_sharing_scope",
                )
            )
    except Exception:
        pass

    # User personalization controls (humor, character mode, boundaries).
    try:
        pref_text = describe_preferences_for_context()
        if pref_text:
            items.append(
                ContextItem(
                    item_id="bootstrap-user-preferences",
                    layer="bootstrap",
                    source_type="user_preferences",
                    title="User preferences",
                    content=pref_text,
                    priority=0.91,
                    confidence=1.0,
                    include_reason="persistent_user_preferences",
                )
            )
    except Exception:
        pass

    if quality_flags:
        items.append(
            ContextItem(
                item_id="bootstrap-quality",
                layer="bootstrap",
                source_type="input_quality",
                title="Input quality",
                content=f"Quality flags: {_compact_join(quality_flags, limit=5)}.",
                priority=0.72,
                confidence=0.7,
                include_reason="input_quality",
            )
        )

    if recent_turns:
        recent_summary = " | ".join(
            f"{str(turn.get('reconstructed_input') or '')[:80]}"
            for turn in recent_turns[:2]
        )
        items.append(
            ContextItem(
                item_id="bootstrap-dialogue",
                layer="bootstrap",
                source_type="recent_dialogue",
                title="Recent dialogue state",
                content=f"Recent turns: {recent_summary}",
                priority=0.82,
                confidence=0.72,
                include_reason="recent_dialogue",
            )
        )

    if lexicon:
        selected: list[str] = []
        input_text = (
            f"{getattr(interpretation, 'normalized_text', '')} "
            f"{getattr(interpretation, 'reconstructed_text', '')}"
        ).lower()
        for term, canonical in lexicon.items():
            if term in input_text or canonical in input_text or canonical in topic_hints:
                selected.append(f"{term}->{canonical}")
            if len(selected) >= max_lexicon_items:
                break
        if selected:
            items.append(
                ContextItem(
                    item_id="bootstrap-lexicon",
                    layer="bootstrap",
                    source_type="shorthand",
                    title="Active shorthand mappings",
                    content=f"Shorthand: {', '.join(selected[:max_lexicon_items])}.",
                    priority=0.7,
                    confidence=0.75,
                    include_reason="active_shorthand",
                )
            )

    return items
