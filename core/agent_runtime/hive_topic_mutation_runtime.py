from __future__ import annotations

import contextlib
import uuid
from typing import Any

from core.hive_activity_tracker import session_hive_state


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
