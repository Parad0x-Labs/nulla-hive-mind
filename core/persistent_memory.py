from __future__ import annotations

import json
import re
from typing import Any

from core.memory import entries as memory_entries
from core.memory import learning as memory_learning
from core.memory.files import (
    MAX_CONVERSATION_LOG_BYTES as _MAX_CONVERSATION_LOG_BYTES,
)
from core.memory.files import (
    conversation_log_path,
    memory_entries_path,
    memory_path,
    operator_dense_profile_path,
    session_summaries_path,
    user_heuristics_path,
)
from core.memory.files import (
    ensure_memory_files as _ensure_memory_files,
)
from core.memory.files import (
    trim_jsonl_file as _trim_jsonl_file,
)
from core.memory.files import (
    utcnow as _utcnow,
)
from core.memory.policies import (
    describe_session_memory_policy,
    session_memory_policy,
    set_session_memory_policy,
)
from core.memory.policies import (
    ensure_session_policy_table as _ensure_session_policy_table,
)
from core.memory.policies import (
    parse_session_scope_command as _parse_session_scope_command,
)
from core.privacy_guard import share_scope_label
from storage.dialogue_memory import (
    archive_dialogue_topic,
    get_dialogue_session,
    update_dialogue_session,
)

add_memory_fact = memory_entries.add_memory_fact
forget_memory = memory_entries.forget_memory
load_memory_excerpt = memory_entries.load_memory_excerpt
recent_conversation_events = memory_entries.recent_conversation_events
search_relevant_memory = memory_entries.search_relevant_memory
search_session_summaries = memory_entries.search_session_summaries
search_user_heuristics = memory_entries.search_user_heuristics
summarize_memory = memory_entries.summarize_memory
load_operator_dense_profile = memory_learning.load_operator_dense_profile
refresh_operator_dense_profile = memory_learning.refresh_operator_dense_profile

_keyword_tokens = memory_entries.keyword_tokens_filtered
_sanitize_fact = memory_entries.sanitize_fact
_trim_text = memory_entries.trim_text
_normalized_history = memory_learning.normalized_history
_record_assistant_dialogue_turn = memory_learning.record_assistant_dialogue_turn
_auto_capture_memory = memory_learning.auto_capture_memory
_update_user_heuristics = memory_learning.update_user_heuristics
_detect_implicit_feedback = memory_learning.detect_implicit_feedback
_update_session_summary = memory_learning.update_session_summary

__all__ = [
    "add_memory_fact",
    "append_conversation_event",
    "augment_history_from_session_log",
    "conversation_log_path",
    "describe_session_memory_policy",
    "ensure_memory_files",
    "forget_memory",
    "load_memory_excerpt",
    "load_operator_dense_profile",
    "maybe_handle_memory_command",
    "memory_entries_path",
    "memory_lifecycle_snapshot",
    "memory_path",
    "operator_dense_profile_path",
    "recent_conversation_events",
    "refresh_operator_dense_profile",
    "search_relevant_memory",
    "search_session_summaries",
    "search_user_heuristics",
    "session_memory_policy",
    "session_summaries_path",
    "set_session_memory_policy",
    "summarize_memory",
    "user_heuristics_path",
]

_REMEMBER_RE = re.compile(r"^(?:remember(?: that)?|note(?: that)?|store(?: this)?)\s+(.+)$", re.IGNORECASE)
_FORGET_RE = re.compile(r"^(?:forget|erase)\s+(.+)$", re.IGNORECASE)
_SNAPSHOT_GENERIC_OPERATION_TOKENS = {
    "append",
    "back",
    "create",
    "desktop",
    "directory",
    "download",
    "exact",
    "exactly",
    "file",
    "files",
    "folder",
    "inside",
    "make",
    "path",
    "paths",
    "read",
    "readback",
    "save",
    "text",
    "whole",
    "workspace",
    "write",
}
_SNAPSHOT_UTILITY_MARKERS = (
    "what time",
    "date and time",
    "what date",
    "what day",
    "weather",
)


def ensure_memory_files() -> None:
    _ensure_memory_files(ensure_policy_table=_ensure_session_policy_table)


def append_conversation_event(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
    source_context: dict[str, Any] | None = None,
    response_class: str | None = None,
) -> None:
    ensure_memory_files()
    history = _normalized_history((source_context or {}).get("conversation_history"))
    policy = session_memory_policy(session_id)
    assistant_text = str(assistant_output or "").strip()
    payload = {
        "ts": _utcnow(),
        "session_id": session_id,
        "surface": str((source_context or {}).get("surface", "")),
        "platform": str((source_context or {}).get("platform", "")),
        "user": str(user_input or "")[:4000],
        "assistant": assistant_text[:8000],
        "history_message_count": len(history),
        "share_scope": policy["share_scope"],
        "realm_label": policy.get("realm_label") or share_scope_label(policy["share_scope"]),
        "restricted_terms": list(policy.get("restricted_terms") or []),
    }
    path = conversation_log_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _trim_jsonl_file(path, max_bytes=_MAX_CONVERSATION_LOG_BYTES)
    _record_assistant_dialogue_turn(
        session_id=session_id,
        assistant_output=assistant_text,
    )
    _close_failed_dialogue_topic_if_needed(
        session_id=session_id,
        user_input=user_input,
        assistant_output=assistant_text,
        response_class=response_class,
    )
    _auto_capture_memory(session_id=session_id, user_input=user_input)
    _update_user_heuristics(session_id=session_id, user_input=user_input)
    _detect_implicit_feedback(
        session_id=session_id,
        user_input=user_input,
        assistant_output=assistant_output,
    )
    _update_session_summary(
        session_id=session_id,
        user_input=user_input,
        assistant_output=assistant_output,
    )
    refresh_operator_dense_profile(session_id=session_id)


def _close_failed_dialogue_topic_if_needed(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
    response_class: str | None,
) -> None:
    normalized_session = str(session_id or "").strip()
    normalized_output = " ".join(str(assistant_output or "").split()).strip()
    normalized_class = str(response_class or "").strip().lower()
    if not normalized_session or not normalized_output:
        return
    if not _assistant_failure_closes_topic(normalized_output, response_class=normalized_class):
        return
    state = get_dialogue_session(normalized_session)
    last_subject = str(state.get("last_subject") or "").strip() or None
    topic_hints = [str(item).strip() for item in list(state.get("topic_hints") or []) if str(item).strip()]
    current_user_goal = str(state.get("current_user_goal") or "").strip() or None
    assistant_commitments = [str(item).strip() for item in list(state.get("assistant_commitments") or []) if str(item).strip()]
    unresolved_followups = [str(item).strip() for item in list(state.get("unresolved_followups") or []) if str(item).strip()]
    if not any([last_subject, topic_hints, current_user_goal, assistant_commitments, unresolved_followups]):
        return
    archive_dialogue_topic(
        normalized_session,
        last_subject=last_subject,
        topic_hints=topic_hints,
        current_user_goal=current_user_goal,
        assistant_commitments=assistant_commitments,
        unresolved_followups=unresolved_followups,
        closure_status="unresolved",
        closure_reason="assistant_failure",
        closing_user_input=user_input,
        closing_assistant_output=normalized_output,
    )
    update_dialogue_session(
        normalized_session,
        last_subject=last_subject,
        topic_hints=topic_hints,
        last_intent_mode=str(state.get("last_intent_mode") or "").strip() or None,
        current_user_goal="",
        assistant_commitments=[],
        unresolved_followups=[],
        user_stance=str(state.get("user_stance") or "").strip() or None,
        emotional_tone=str(state.get("emotional_tone") or "").strip() or None,
    )


def _assistant_failure_closes_topic(text: str, *, response_class: str) -> bool:
    if response_class in {"task_failed_user_safe", "system_error_user_safe"}:
        return True
    lowered = str(text or "").strip().lower()
    failure_prefixes = (
        "i couldn't ",
        "i can't ",
        "i cant ",
        "sorry, i couldn't ",
        "sorry, i can't ",
        "sorry, i cant ",
        "i checked, but i couldn't ",
    )
    return any(lowered.startswith(prefix) for prefix in failure_prefixes)


def augment_history_from_session_log(
    history: list[dict[str, str]] | None,
    *,
    session_id: str,
    user_text: str,
    limit: int = 6,
) -> list[dict[str, str]]:
    normalized_history = [dict(item) for item in list(history or []) if isinstance(item, dict)]
    if len(normalized_history) > 1:
        return normalized_history
    normalized_session = str(session_id or "").strip()
    normalized_user = str(user_text or "").strip()
    if not normalized_session or not normalized_user:
        return normalized_history

    hydrated_history: list[dict[str, str]] = []
    for event in recent_conversation_events(normalized_session, limit=max(1, int(limit))):
        if not isinstance(event, dict):
            continue
        event_user = str(event.get("user") or "").strip()
        event_assistant = str(event.get("assistant") or "").strip()
        if event_user:
            hydrated_history.append({"role": "user", "content": event_user})
        if event_assistant:
            hydrated_history.append({"role": "assistant", "content": event_assistant})

    if not hydrated_history:
        return normalized_history

    if normalized_history:
        last_message = normalized_history[-1]
        if (
            str(last_message.get("role") or "").strip().lower() == "user"
            and str(last_message.get("content") or "").strip() == normalized_user
        ):
            return [*hydrated_history, *normalized_history]
    return [*hydrated_history, {"role": "user", "content": normalized_user}]


def memory_lifecycle_snapshot(
    *,
    session_id: str,
    query_text: str = "",
    topic_hints: list[str] | None = None,
    recent_limit: int = 6,
    memory_limit: int = 4,
    heuristic_limit: int = 4,
    summary_limit: int = 3,
) -> dict[str, Any]:
    ensure_memory_files()
    normalized_session = str(session_id or "").strip()
    recent = recent_conversation_events(normalized_session, limit=max(1, int(recent_limit))) if normalized_session else []
    recent_user_turns = [
        str(item.get("user") or "").strip()
        for item in recent
        if str(item.get("user") or "").strip()
    ]
    inferred_query = str(query_text or "").strip()
    if not inferred_query and recent_user_turns:
        inferred_query = " ".join(recent_user_turns[-2:])
    normalized_topic_hints = [
        str(item).strip()
        for item in list(topic_hints or [])
        if str(item).strip()
    ]
    if not normalized_topic_hints and inferred_query:
        normalized_topic_hints = _keyword_tokens(inferred_query, limit=6)

    skip_durable_selection = _should_skip_durable_snapshot_selection(
        query_text=inferred_query,
        recent_user_turns=recent_user_turns,
    )
    relevant_memory: list[dict[str, Any]] = []
    session_summaries: list[dict[str, Any]] = []
    heuristics: list[dict[str, Any]] = []
    if not skip_durable_selection and inferred_query:
        relevant_memory = [
            row
            for row in search_relevant_memory(
                inferred_query,
                topic_hints=normalized_topic_hints,
                limit=max(1, int(memory_limit)),
            )
            if float(row.get("score") or 0.0) >= 0.35
        ]
        session_summaries = [
            row
            for row in search_session_summaries(
                inferred_query,
                topic_hints=normalized_topic_hints,
                limit=max(1, int(summary_limit)),
                exclude_session_id=normalized_session or None,
            )
            if float(row.get("score") or 0.0) >= 0.45
        ]
    if not skip_durable_selection and (inferred_query or normalized_topic_hints):
        heuristics = [
            row
            for row in search_user_heuristics(
                inferred_query,
                topic_hints=normalized_topic_hints,
                limit=max(1, int(heuristic_limit)),
            )
            if float(row.get("score") or 0.0) >= 0.40
        ]
    dense_profile = dict(load_operator_dense_profile() or {})
    policy = session_memory_policy(normalized_session)

    recent_turns = [
        {
            "ts": str(item.get("ts") or "").strip(),
            "user": _trim_text(str(item.get("user") or ""), 180),
            "assistant": _trim_text(str(item.get("assistant") or ""), 220),
        }
        for item in recent
    ]
    selection_summary = (
        f"query `{_trim_text(inferred_query or 'recent session context', 90)}` selected "
        f"{len(relevant_memory)} durable memory entries, "
        f"{len(session_summaries)} prior session summaries, and "
        f"{len(heuristics)} heuristic signals."
    )
    return {
        "session_id": normalized_session,
        "selection_query": inferred_query,
        "topic_hints": normalized_topic_hints,
        "share_scope": str(policy.get("share_scope") or "local_only"),
        "realm_label": str(policy.get("realm_label") or share_scope_label(policy.get("share_scope"))),
        "restricted_terms": list(policy.get("restricted_terms") or []),
        "recent_conversation_event_count": len(recent),
        "recent_turns": recent_turns,
        "recent_user_turns": recent_user_turns[-3:],
        "relevant_memory_count": len(relevant_memory),
        "relevant_memory": [
            {
                "text": _trim_text(str(item.get("text") or ""), 180),
                "category": str(item.get("category") or "").strip(),
                "score": float(item.get("score") or 0.0),
                "share_scope": str(item.get("share_scope") or "").strip(),
            }
            for item in relevant_memory
        ],
        "session_summary_count": len(session_summaries),
        "session_summaries": [
            {
                "session_id": str(item.get("session_id") or "").strip(),
                "summary": _trim_text(str(item.get("summary") or ""), 220),
                "score": float(item.get("score") or 0.0),
            }
            for item in session_summaries
        ],
        "heuristic_count": len(heuristics),
        "user_heuristics": [
            {
                "category": str(item.get("category") or "").strip(),
                "signal": str(item.get("signal") or "").strip(),
                "text": _trim_text(str(item.get("text") or ""), 140),
                "score": float(item.get("score") or 0.0),
                "mentions": int(item.get("mentions") or 0),
            }
            for item in heuristics
        ],
        "dense_profile": {
            "dense_summary": _trim_text(str(dense_profile.get("dense_summary") or ""), 280),
            "response_style": list(dense_profile.get("response_style") or []),
            "source_preferences": list(dense_profile.get("source_preferences") or []),
            "preferred_stacks": list(dense_profile.get("preferred_stacks") or []),
            "active_projects": list(dense_profile.get("active_projects") or []),
            "last_session_id": str(dense_profile.get("last_session_id") or "").strip(),
        },
        "selection_summary": selection_summary,
    }


def _should_skip_durable_snapshot_selection(
    *,
    query_text: str,
    recent_user_turns: list[str],
) -> bool:
    normalized_query = " ".join(str(query_text or "").lower().split())
    if any(marker in normalized_query for marker in _SNAPSHOT_UTILITY_MARKERS):
        return True
    query_tokens = set(_keyword_tokens(normalized_query, limit=8))
    if query_tokens and query_tokens.issubset(_SNAPSHOT_GENERIC_OPERATION_TOKENS):
        return True
    recent_tokens = set(_keyword_tokens(" ".join(recent_user_turns[-3:]), limit=24))
    return bool(
        query_tokens
        and query_tokens.issubset(_SNAPSHOT_GENERIC_OPERATION_TOKENS)
        and recent_tokens & _SNAPSHOT_GENERIC_OPERATION_TOKENS
    )


def maybe_handle_memory_command(user_text: str, *, session_id: str | None = None) -> tuple[bool, str]:
    text = str(user_text or "").strip()
    if not text:
        return False, ""
    lowered = text.lower()
    scope_command = _parse_session_scope_command(text)
    if scope_command is not None:
        action = str(scope_command.get("action") or "")
        if action == "show":
            return True, describe_session_memory_policy(session_id)
        if not session_id:
            return True, "I need an active session before I can change the share scope."
        result = set_session_memory_policy(
            str(session_id),
            share_scope=str(scope_command.get("share_scope") or "local_only"),
            restricted_terms=list(scope_command.get("restricted_terms") or []),
        )
        scope = result["share_scope"]
        if scope == "hive_mind":
            label = "SHARED PACK"
            scope_note = "Generalized learnings from this session can sync to the mesh after secret screening. Raw chat remains in the PRIVATE VAULT."
        elif scope == "public_knowledge":
            label = "HIVE/PUBLIC COMMONS"
            scope_note = "Generalized learnings from this session can be published as public claims after secret screening. Raw chat remains in the PRIVATE VAULT."
        else:
            label = "PRIVATE VAULT"
            scope_note = "Everything from this session stays on this node unless you reclassify it."
        protected = ""
        if result.get("restricted_terms"):
            protected = " Protected exceptions: " + ", ".join(list(result["restricted_terms"])[:6]) + "."
        stats = (
            f" Existing session shards updated: {int(result.get('updated_shards') or 0)}"
            f", shared now: {int(result.get('registered_shards') or 0)}"
            f", forced local by privacy guard: {int(result.get('blocked_shards') or 0)}."
        )
        return True, f"Session scope set to {label}. {scope_note}{protected}{stats}"
    if lowered in {"/memory", "what do you remember", "show memory"}:
        summary = summarize_memory(limit=8)
        if not summary:
            return True, "Memory is currently empty. Tell me what to remember."
        return True, "What I remember:\n" + "\n".join(f"- {line}" for line in summary)

    remember = _REMEMBER_RE.match(text)
    if remember:
        fact = remember.group(1).strip()
        if len(fact) < 3:
            return True, "Memory update skipped: fact is too short."
        added = add_memory_fact(fact)
        if added:
            return True, "Locked in. I’ll remember that."
        return True, "I already had that in memory."

    forget = _FORGET_RE.match(text)
    if forget:
        token = forget.group(1).strip()
        if len(token) < 2:
            return True, "Forget command skipped: provide a clearer keyword."
        removed = forget_memory(token)
        return True, f"Forget applied. Removed {removed} memory entr{'y' if removed == 1 else 'ies'}."

    return False, ""
