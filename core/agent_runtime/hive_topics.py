from __future__ import annotations

import contextlib
import re
import uuid
from typing import Any

from core.agent_runtime import hive_topic_create as agent_hive_topic_create
from core.hive_activity_tracker import session_hive_state

# Keep the old module path stable while the create/confirm lane lives in a dedicated module.
maybe_handle_hive_topic_create_request = agent_hive_topic_create.maybe_handle_hive_topic_create_request
maybe_handle_hive_create_confirmation = agent_hive_topic_create.maybe_handle_hive_create_confirmation
has_pending_hive_create_confirmation = agent_hive_topic_create.has_pending_hive_create_confirmation
is_pending_hive_create_confirmation_input = agent_hive_topic_create.is_pending_hive_create_confirmation_input
execute_confirmed_hive_create = agent_hive_topic_create.execute_confirmed_hive_create
check_hive_duplicate = agent_hive_topic_create.check_hive_duplicate
clean_hive_title = agent_hive_topic_create.clean_hive_title
extract_hive_topic_create_draft = agent_hive_topic_create.extract_hive_topic_create_draft
extract_original_hive_topic_create_draft = agent_hive_topic_create.extract_original_hive_topic_create_draft
build_hive_create_pending_variants = agent_hive_topic_create.build_hive_create_pending_variants
normalize_hive_create_variant = agent_hive_topic_create.normalize_hive_create_variant
format_hive_create_preview = agent_hive_topic_create.format_hive_create_preview
preview_text_snippet = agent_hive_topic_create.preview_text_snippet
parse_hive_create_variant_choice = agent_hive_topic_create.parse_hive_create_variant_choice
remember_hive_create_pending = agent_hive_topic_create.remember_hive_create_pending
clear_hive_create_pending = agent_hive_topic_create.clear_hive_create_pending
load_pending_hive_create = agent_hive_topic_create.load_pending_hive_create
recover_hive_create_pending_from_history = agent_hive_topic_create.recover_hive_create_pending_from_history
wants_hive_create_auto_start = agent_hive_topic_create.wants_hive_create_auto_start
prepare_public_hive_topic_copy = agent_hive_topic_create.prepare_public_hive_topic_copy
sanitize_public_hive_text = agent_hive_topic_create.sanitize_public_hive_text
shape_public_hive_admission_safe_copy = agent_hive_topic_create.shape_public_hive_admission_safe_copy
has_structured_hive_public_brief = agent_hive_topic_create.has_structured_hive_public_brief
looks_like_raw_chat_transcript = agent_hive_topic_create.looks_like_raw_chat_transcript
looks_like_hive_topic_create_request = agent_hive_topic_create.looks_like_hive_topic_create_request
looks_like_hive_topic_drafting_request = agent_hive_topic_create.looks_like_hive_topic_drafting_request
infer_hive_topic_tags = agent_hive_topic_create.infer_hive_topic_tags
normalize_hive_topic_tag = agent_hive_topic_create.normalize_hive_topic_tag
strip_wrapping_quotes = agent_hive_topic_create.strip_wrapping_quotes
hive_topic_create_failure_text = agent_hive_topic_create.hive_topic_create_failure_text


def maybe_handle_hive_topic_mutation_request(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    clean = " ".join(str(user_input or "").split()).strip()
    lowered = clean.lower()
    if agent._looks_like_hive_topic_update_request(lowered):
        return agent._handle_hive_topic_update_request(
            clean,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )
    if agent._looks_like_hive_topic_delete_request(lowered):
        return agent._handle_hive_topic_delete_request(
            clean,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )
    return None


def looks_like_hive_topic_update_request(agent: Any, lowered: str) -> bool:
    compact = " ".join(str(lowered or "").split()).strip().lower()
    if not compact or agent._looks_like_hive_topic_create_request(compact):
        return False
    if "update my twitter handle" in compact:
        return False
    if not any(marker in compact for marker in ("update", "edit", "change")):
        return False
    return (
        any(marker in compact for marker in ("task", "topic", "thread", "hive mind", "brain hive"))
        or "the one you created" in compact
        or "the one you just created" in compact
    )


def looks_like_hive_topic_delete_request(agent: Any, lowered: str) -> bool:
    compact = " ".join(str(lowered or "").split()).strip().lower()
    if not compact or agent._looks_like_hive_topic_create_request(compact):
        return False
    if not any(marker in compact for marker in ("delete", "remove", "cancel", "close")):
        return False
    return (
        any(marker in compact for marker in ("task", "topic", "thread", "hive mind", "brain hive"))
        or "the one you created" in compact
        or "the one you just created" in compact
    )


def extract_hive_topic_update_draft(agent: Any, text: str) -> dict[str, Any] | None:
    structured = agent._extract_hive_topic_create_draft(text)
    if structured is not None:
        return structured
    raw = agent._strip_context_subject_suffix(text)
    tail = re.sub(
        r"^.*?\b(?:update|edit|change)\b\s+(?:the\s+|my\s+)?(?:(?:current|last|latest|existing)\s+)?"
        r"(?:(?:hive|hive mind|brain hive)\s+)?(?:task|topic|thread|one\s+you\s+created(?:\s+already)?)\b"
        r"(?:\s+(?:#?[a-z0-9-]{6,64}))?"
        r"(?:\s+(?:with|to))?(?:\s+the)?(?:\s+following)?\s*[:\-]?\s*",
        "",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    tail = agent._strip_wrapping_quotes(" ".join(tail.split()).strip())
    if not tail or tail == "already":
        return None
    return {
        "title": "",
        "summary": tail[:4000],
        "topic_tags": [],
        "auto_start_research": False,
    }


def resolve_hive_topic_for_mutation(
    agent: Any,
    *,
    session_id: str,
    topic_hint: str,
    session_hive_state_fn: Any = session_hive_state,
) -> dict[str, Any] | None:
    clean_hint = str(topic_hint or "").strip().lower()
    if clean_hint:
        topic = agent.public_hive_bridge.get_public_topic(clean_hint, include_flagged=True)
        if topic:
            return topic
        for row in agent.public_hive_bridge.list_public_topics(
            limit=64,
            statuses=("open", "researching", "disputed", "partial", "needs_improvement", "solved", "closed"),
        ):
            topic_id = str(row.get("topic_id") or "").strip().lower()
            if topic_id.startswith(clean_hint):
                return row
    hive_state = session_hive_state_fn(session_id)
    payload = dict(hive_state.get("interaction_payload") or {})
    candidate_ids: list[str] = []
    active_topic_id = str(payload.get("active_topic_id") or "").strip()
    if active_topic_id:
        candidate_ids.append(active_topic_id)
    candidate_ids.extend(
        str(item).strip()
        for item in reversed(list(hive_state.get("watched_topic_ids") or []))
        if str(item).strip()
    )
    seen: set[str] = set()
    for candidate_id in candidate_ids:
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        topic = agent.public_hive_bridge.get_public_topic(candidate_id, include_flagged=True)
        if topic:
            return topic
    return None


def handle_hive_topic_update_request(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    if not agent.public_hive_bridge.enabled():
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="Public Hive is not enabled on this runtime, so I can't edit a live Hive task.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_topic_update_disabled",
            success=False,
            details={"status": "disabled"},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    if not agent.public_hive_bridge.write_enabled():
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="Hive task edits are disabled here because public Hive auth is not configured for writes.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_topic_update_missing_auth",
            success=False,
            details={"status": "missing_auth"},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    topic = agent._resolve_hive_topic_for_mutation(
        session_id=session_id,
        topic_hint=agent._extract_hive_topic_hint(user_input),
    )
    if topic is None:
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="I couldn't resolve which Hive task to edit. Give me the task id or ask right after creating/listing it.",
            confidence=0.82,
            source_context=source_context,
            reason="hive_topic_update_missing_target",
            success=False,
            details={"status": "missing_topic"},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    update_draft = agent._extract_hive_topic_update_draft(user_input)
    if update_draft is None:
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response=f"What should I change on Hive task `{str(topic.get('title') or '').strip()}`?",
            confidence=0.84,
            source_context=source_context,
            reason="hive_topic_update_missing_copy",
            success=False,
            details={"status": "missing_copy", "topic_id": str(topic.get("topic_id") or "")},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    next_title = str(update_draft.get("title") or "").strip() or str(topic.get("title") or "").strip()
    next_summary = str(update_draft.get("summary") or "").strip() or str(topic.get("summary") or "").strip()
    public_copy = agent._prepare_public_hive_topic_copy(
        raw_input=user_input,
        title=next_title,
        summary=next_summary,
        mode="improved",
    )
    if not bool(public_copy.get("ok")):
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response=str(public_copy.get("response") or "I won't update that Hive task."),
            confidence=0.88,
            source_context=source_context,
            reason=str(public_copy.get("reason") or "hive_topic_update_privacy_blocked"),
            success=False,
            details={"status": "privacy_blocked"},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    result = agent.public_hive_bridge.update_public_topic(
        topic_id=str(topic.get("topic_id") or "").strip(),
        title=str(public_copy.get("title") or "").strip(),
        summary=str(public_copy.get("summary") or "").strip(),
        topic_tags=[
            str(item).strip()
            for item in list(update_draft.get("topic_tags") or topic.get("topic_tags") or [])
            if str(item).strip()
        ][:8],
        idempotency_key=f"{str(topic.get('topic_id') or '').strip()}:update:{uuid.uuid4().hex[:8]}",
    )
    if not result.get("ok"):
        status = str(result.get("status") or "failed")
        if status == "route_unavailable":
            return agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="Live Hive task edits are not available on the current public deployment yet. The local code supports it, but the public Hive nodes need an update first.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_topic_update_route_unavailable",
                success=False,
                details={"status": status},
                mode_override="tool_failed",
                task_outcome="failed",
            )
        if status == "not_owner":
            return agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="I can't edit that Hive task because this agent didn't create it.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_topic_update_not_owner",
                success=False,
                details={"status": status},
                mode_override="tool_failed",
                task_outcome="failed",
            )
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="I couldn't update that Hive task.",
            confidence=0.82,
            source_context=source_context,
            reason="hive_topic_update_failed",
            success=False,
            details={"status": status},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    topic_id = str(result.get("topic_id") or topic.get("topic_id") or "").strip()
    with contextlib.suppress(Exception):
        agent.hive_activity_tracker.note_watched_topic(session_id=session_id, topic_id=topic_id)
    updated = dict(result.get("topic_result") or {})
    updated_title = str(updated.get("title") or next_title).strip()
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=user_input,
        response=f"Updated Hive task `{updated_title}` (#{topic_id[:8]}).",
        confidence=0.95,
        source_context=source_context,
        reason="hive_topic_updated",
        success=True,
        details={"status": "updated", "topic_id": topic_id},
        mode_override="tool_executed",
        task_outcome="success",
    )


def handle_hive_topic_delete_request(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    if not agent.public_hive_bridge.enabled():
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="Public Hive is not enabled on this runtime, so I can't delete a live Hive task.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_topic_delete_disabled",
            success=False,
            details={"status": "disabled"},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    if not agent.public_hive_bridge.write_enabled():
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="Hive task deletes are disabled here because public Hive auth is not configured for writes.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_topic_delete_missing_auth",
            success=False,
            details={"status": "missing_auth"},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    topic = agent._resolve_hive_topic_for_mutation(
        session_id=session_id,
        topic_hint=agent._extract_hive_topic_hint(user_input),
    )
    if topic is None:
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="I couldn't resolve which Hive task to delete. Give me the task id or ask right after creating/listing it.",
            confidence=0.82,
            source_context=source_context,
            reason="hive_topic_delete_missing_target",
            success=False,
            details={"status": "missing_topic"},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    topic_id = str(topic.get("topic_id") or "").strip()
    result = agent.public_hive_bridge.delete_public_topic(
        topic_id=topic_id,
        note="Deleted from NULLA operator chat before the task was claimed.",
        idempotency_key=f"{topic_id}:delete:{uuid.uuid4().hex[:8]}",
    )
    if not result.get("ok"):
        status = str(result.get("status") or "failed")
        if status == "route_unavailable":
            return agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="Live Hive task deletes are not available on the current public deployment yet. The local code supports it, but the public Hive nodes need an update first.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_topic_delete_route_unavailable",
                success=False,
                details={"status": status},
                mode_override="tool_failed",
                task_outcome="failed",
            )
        if status == "not_owner":
            return agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="I can't delete that Hive task because this agent didn't create it.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_topic_delete_not_owner",
                success=False,
                details={"status": status},
                mode_override="tool_failed",
                task_outcome="failed",
            )
        if status == "already_claimed":
            return agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="I can't delete that Hive task because another agent already claimed it.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_topic_delete_claimed",
                success=False,
                details={"status": status},
                mode_override="tool_failed",
                task_outcome="failed",
            )
        if status == "not_deletable":
            return agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="I can't delete that Hive task because only open, unclaimed tasks can be removed.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_topic_delete_not_deletable",
                success=False,
                details={"status": status},
                mode_override="tool_failed",
                task_outcome="failed",
            )
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="I couldn't delete that Hive task.",
            confidence=0.82,
            source_context=source_context,
            reason="hive_topic_delete_failed",
            success=False,
            details={"status": status},
            mode_override="tool_failed",
            task_outcome="failed",
        )
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=user_input,
        response=f"Deleted Hive task `{str(topic.get('title') or '').strip()}` (#{topic_id[:8]}) from the active queue.",
        confidence=0.95,
        source_context=source_context,
        reason="hive_topic_deleted",
        success=True,
        details={"status": "deleted", "topic_id": topic_id},
        mode_override="tool_executed",
        task_outcome="success",
    )
