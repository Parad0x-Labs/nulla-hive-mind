from __future__ import annotations

from typing import Any

from core.persistent_memory import memory_lifecycle_snapshot
from core.runtime_execution_history import summarize_runtime_surface
from core.runtime_task_events import list_runtime_session_events, list_runtime_sessions


def build_runtime_operator_snapshot(
    *,
    session_id: str = "",
    query_text: str = "",
    topic_hints: list[str] | None = None,
    session_limit: int = 24,
    event_limit: int = 12,
) -> dict[str, Any]:
    sessions = [
        dict(item)
        for item in list_runtime_sessions(limit=max(1, min(int(session_limit or 24), 200)))
        if isinstance(item, dict)
    ]
    runtime_surface = summarize_runtime_surface(sessions)
    selected_session = _select_session(sessions, session_id=session_id)
    selected_session_id = str(selected_session.get("session_id") or session_id or "").strip()
    execution_history = dict(selected_session.get("execution_history") or {})
    recent_runtime_events = [
        _event_preview(item)
        for item in list_runtime_session_events(selected_session_id, after_seq=0, limit=max(1, min(int(event_limit or 12), 200)))
    ] if selected_session_id else []
    memory_snapshot = memory_lifecycle_snapshot(
        session_id=selected_session_id,
        query_text=query_text,
        topic_hints=topic_hints,
    )
    return {
        "session_id": selected_session_id,
        "query_text": str(query_text or "").strip(),
        "topic_hints": [str(item).strip() for item in list(topic_hints or []) if str(item).strip()],
        "runtime_surface": runtime_surface,
        "session": {
            "session_id": selected_session_id,
            "title": str(execution_history.get("title") or selected_session.get("request_preview") or "").strip(),
            "status": str(execution_history.get("status") or selected_session.get("status") or "").strip(),
            "request_status": str(execution_history.get("request_status") or selected_session.get("status") or "").strip(),
            "updated_at": str(selected_session.get("updated_at") or "").strip(),
            "execution_history": execution_history,
            "recent_runtime_event_count": len(recent_runtime_events),
            "recent_runtime_events": recent_runtime_events[-max(1, min(int(event_limit or 12), 200)):],
        },
        "memory_lifecycle": memory_snapshot,
        "inspection_summary": _inspection_summary(
            runtime_surface=runtime_surface,
            execution_history=execution_history,
            memory_snapshot=memory_snapshot,
        ),
    }


def _select_session(
    sessions: list[dict[str, Any]],
    *,
    session_id: str,
) -> dict[str, Any]:
    normalized_session = str(session_id or "").strip()
    if normalized_session:
        for item in sessions:
            if str(item.get("session_id") or "").strip() == normalized_session:
                return item
    latest_session = dict(summarize_runtime_surface(sessions).get("latest_session") or {})
    latest_session_id = str(latest_session.get("session_id") or "").strip()
    if latest_session_id:
        for item in sessions:
            if str(item.get("session_id") or "").strip() == latest_session_id:
                return item
    return dict((sessions[:1] or [{}])[0] or {})


def _event_preview(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": int(event.get("seq") or 0),
        "event_type": str(event.get("event_type") or "").strip(),
        "status": str(event.get("status") or "").strip(),
        "tool_name": str(event.get("tool_name") or event.get("intent") or "").strip(),
        "message": _trim_text(str(event.get("message") or ""), 180),
    }


def _inspection_summary(
    *,
    runtime_surface: dict[str, Any],
    execution_history: dict[str, Any],
    memory_snapshot: dict[str, Any],
) -> list[str]:
    summary: list[str] = []
    latest_tool = str(execution_history.get("latest_tool") or "").strip()
    status = str(execution_history.get("status") or "").strip()
    if latest_tool or status:
        summary.append(
            f"Latest session status: {status or 'unknown'}"
            + (f"; latest tool: {latest_tool}" if latest_tool else "")
        )
    pending_approval_count = int(runtime_surface.get("approval_pending_count") or 0)
    if pending_approval_count > 0:
        summary.append(f"Approval backlog visible: {pending_approval_count}.")
    changed_paths = [str(item).strip() for item in list(execution_history.get("changed_paths") or []) if str(item).strip()]
    if changed_paths:
        summary.append(f"Changed paths: {', '.join(changed_paths[:3])}.")
    summary.append(str(memory_snapshot.get("selection_summary") or "").strip())
    return [item for item in summary if item]


def _trim_text(text: str, limit: int) -> str:
    normalized = " ".join(str(text or "").split()).strip()
    if len(normalized) <= max(1, int(limit)):
        return normalized
    return normalized[: max(1, int(limit) - 3)].rstrip() + "..."
