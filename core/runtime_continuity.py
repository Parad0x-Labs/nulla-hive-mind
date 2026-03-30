from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.runtime_execution_history import build_runtime_execution_history
from storage.db import DEFAULT_DB_PATH, get_connection

_DB_PATH_OVERRIDE: str | Path | None = None
_LOCK = threading.RLock()
_RESUMABLE_STATUSES = {"running", "interrupted", "pending_approval"}
_MUTATING_TOOL_INTENTS = {
    "workspace.ensure_directory",
    "workspace.write_file",
    "workspace.replace_in_file",
    "workspace.apply_unified_diff",
    "workspace.rollback_last_change",
    "workspace.run_formatter",
    "sandbox.run_command",
    "hive.research_topic",
    "hive.create_topic",
    "hive.claim_task",
    "hive.post_progress",
    "hive.submit_result",
    "operator.cleanup_temp_files",
    "operator.move_path",
    "operator.schedule_calendar_event",
}


def configure_runtime_continuity_db_path(db_path: str | Path | None) -> None:
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = None if db_path is None else str(Path(db_path).expanduser().resolve())


def reset_runtime_continuity_state() -> None:
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            for table in (
                "runtime_tool_receipts",
                "runtime_checkpoints",
                "runtime_session_events",
                "runtime_sessions",
                "hive_idempotency_keys",
            ):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()


def append_runtime_event(
    *,
    session_id: str,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        raise ValueError("session_id is required")
    clean_event_type = str(event_type or "status").strip() or "status"
    clean_message = str(message or "").strip()
    clean_details = dict(details or {})
    timestamp = str(created_at or _utcnow())
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            existing = conn.execute(
                "SELECT event_count, started_at, request_preview, task_class, last_checkpoint_id FROM runtime_sessions WHERE session_id = ? LIMIT 1",
                (clean_session_id,),
            ).fetchone()
            seq = int(existing["event_count"] if existing else 0) + 1
            request_preview = _clean_request_preview(clean_details.get("request_preview") or (existing["request_preview"] if existing else ""))
            task_class = str(clean_details.get("task_class") or (existing["task_class"] if existing else "")).strip()
            checkpoint_id = str(clean_details.get("checkpoint_id") or (existing["last_checkpoint_id"] if existing else "")).strip()
            conn.execute(
                """
                INSERT INTO runtime_session_events (
                    session_id, seq, event_type, message, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_session_id,
                    seq,
                    clean_event_type,
                    clean_message,
                    _json_dumps(clean_details),
                    timestamp,
                ),
            )
            _upsert_runtime_session(
                conn,
                session_id=clean_session_id,
                request_preview=request_preview,
                task_class=task_class,
                status=_event_status(clean_event_type),
                last_event_type=clean_event_type,
                last_message=clean_message,
                event_count=seq,
                started_at=str(existing["started_at"] if existing else timestamp),
                updated_at=timestamp,
                checkpoint_id=checkpoint_id or None,
            )
            conn.commit()
        finally:
            conn.close()
    stored = {
        "session_id": clean_session_id,
        "seq": seq,
        "event_type": clean_event_type,
        "message": clean_message,
        "created_at": timestamp,
    }
    stored.update(clean_details)
    return stored


def list_runtime_sessions(*, limit: int = 20) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 100))
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            rows = conn.execute(
                """
                SELECT session_id, started_at, updated_at, event_count, last_event_type,
                       last_message, request_preview, task_class, status, last_checkpoint_id
                FROM runtime_sessions
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
            session_rows = [dict(row) for row in rows]
        finally:
            conn.close()
    for row in session_rows:
        session_id = str(row.get("session_id") or "")
        checkpoint = get_runtime_checkpoint(str(row.get("last_checkpoint_id") or ""))
        if checkpoint:
            row["resume_available"] = bool(checkpoint.get("status") in _RESUMABLE_STATUSES)
            row["checkpoint_status"] = str(checkpoint.get("status") or "")
            row["checkpoint_step_count"] = int(checkpoint.get("step_count") or 0)
        else:
            row["resume_available"] = False
            row["checkpoint_status"] = ""
            row["checkpoint_step_count"] = 0
        events = list_runtime_session_events(session_id, after_seq=0, limit=200)
        receipts = list_runtime_tool_receipts(session_id, limit=64)
        row["execution_history"] = build_runtime_execution_history(
            session=row,
            checkpoint=checkpoint,
            events=events,
            receipts=receipts,
        )
    return session_rows


def list_runtime_session_events(
    session_id: str,
    *,
    after_seq: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return []
    bounded_limit = max(1, min(int(limit), 200))
    clean_after = max(0, int(after_seq))
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            rows = conn.execute(
                """
                SELECT session_id, seq, event_type, message, details_json, created_at
                FROM runtime_session_events
                WHERE session_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (clean_session_id, clean_after, bounded_limit),
            ).fetchall()
        finally:
            conn.close()
    events: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "session_id": str(row["session_id"]),
            "seq": int(row["seq"]),
            "event_type": str(row["event_type"]),
            "message": str(row["message"]),
            "created_at": str(row["created_at"]),
        }
        item.update(_json_loads(row["details_json"], fallback={}))
        events.append(item)
    return events


def list_runtime_tool_receipts(
    session_id: str,
    *,
    limit: int = 64,
) -> list[dict[str, Any]]:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return []
    bounded_limit = max(1, min(int(limit), 200))
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            rows = conn.execute(
                """
                SELECT receipt_key, session_id, checkpoint_id, tool_name, idempotency_key,
                       arguments_json, execution_json, created_at, updated_at
                FROM runtime_tool_receipts
                WHERE session_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (clean_session_id, bounded_limit),
            ).fetchall()
        finally:
            conn.close()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["arguments"] = _json_loads(item.pop("arguments_json"), fallback={})
        item["execution"] = _json_loads(item.pop("execution_json"), fallback={})
        out.append(item)
    return out


def create_runtime_checkpoint(
    *,
    session_id: str,
    request_text: str,
    source_context: dict[str, Any] | None = None,
    task_id: str = "",
    task_class: str = "",
) -> dict[str, Any]:
    checkpoint_id = f"runtime-{uuid.uuid4().hex}"
    timestamp = _utcnow()
    clean_session_id = str(session_id or "").strip()
    state = {
        "executed_steps": [],
        "seen_tool_payloads": [],
        "loop_source_context": _stable_source_context(source_context),
        "pending_tool_payload": None,
        "last_tool_payload": None,
        "last_tool_response": None,
    }
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            conn.execute(
                """
                INSERT INTO runtime_checkpoints (
                    checkpoint_id, session_id, task_id, task_class, request_text, source_context_json,
                    status, step_count, last_tool_name, pending_intent_json, state_json, final_response,
                    failure_text, resume_count, created_at, updated_at, completed_at, resumed_from_checkpoint_id
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', 0, '', '{}', ?, '', '', 0, ?, ?, NULL, NULL)
                """,
                (
                    checkpoint_id,
                    clean_session_id,
                    str(task_id or "").strip(),
                    str(task_class or "").strip(),
                    str(request_text or ""),
                    _json_dumps(_stable_source_context(source_context)),
                    _json_dumps(state),
                    timestamp,
                    timestamp,
                ),
            )
            _upsert_runtime_session(
                conn,
                session_id=clean_session_id,
                request_preview=_preview_text(request_text),
                task_class=str(task_class or "").strip(),
                status="running",
                last_event_type="task_received",
                last_message="Task checkpoint created.",
                event_count=_session_event_count(conn, clean_session_id),
                started_at=_session_started_at(conn, clean_session_id, timestamp),
                updated_at=timestamp,
                checkpoint_id=checkpoint_id,
            )
            conn.commit()
        finally:
            conn.close()
    return get_runtime_checkpoint(checkpoint_id) or {}


def get_runtime_checkpoint(checkpoint_id: str) -> dict[str, Any] | None:
    clean_checkpoint_id = str(checkpoint_id or "").strip()
    if not clean_checkpoint_id:
        return None
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            row = conn.execute(
                """
                SELECT checkpoint_id, session_id, task_id, task_class, request_text, source_context_json,
                       status, step_count, last_tool_name, pending_intent_json, state_json, final_response,
                       failure_text, resume_count, created_at, updated_at, completed_at, resumed_from_checkpoint_id
                FROM runtime_checkpoints
                WHERE checkpoint_id = ?
                LIMIT 1
                """,
                (clean_checkpoint_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    data = dict(row)
    data["source_context"] = _json_loads(data.pop("source_context_json"), fallback={})
    data["pending_intent"] = _json_loads(data.pop("pending_intent_json"), fallback={})
    data["state"] = _json_loads(data.pop("state_json"), fallback={})
    return data


def latest_resumable_checkpoint(session_id: str) -> dict[str, Any] | None:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return None
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            row = conn.execute(
                """
                SELECT checkpoint_id
                FROM runtime_checkpoints
                WHERE session_id = ? AND status IN ('running', 'interrupted', 'pending_approval')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (clean_session_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return get_runtime_checkpoint(str(row["checkpoint_id"]))


def resume_runtime_checkpoint(
    checkpoint_id: str,
    *,
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    current = get_runtime_checkpoint(checkpoint_id)
    if not current:
        return None
    merged_source_context = dict(current.get("source_context") or {})
    merged_source_context.update(_stable_source_context(source_context))
    state = dict(current.get("state") or {})
    state["loop_source_context"] = _merge_loop_source_context(
        state.get("loop_source_context"),
        merged_source_context,
    )
    updated = update_runtime_checkpoint(
        checkpoint_id,
        status="running",
        source_context=merged_source_context,
        state=state,
        resume_count=int(current.get("resume_count") or 0) + 1,
    )
    return updated


def update_runtime_checkpoint(
    checkpoint_id: str,
    *,
    task_id: str | None = None,
    task_class: str | None = None,
    source_context: dict[str, Any] | None = None,
    status: str | None = None,
    step_count: int | None = None,
    last_tool_name: str | None = None,
    pending_intent: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    final_response: str | None = None,
    failure_text: str | None = None,
    resume_count: int | None = None,
) -> dict[str, Any] | None:
    current = get_runtime_checkpoint(checkpoint_id)
    if not current:
        return None
    merged_state = dict(current.get("state") or {})
    if state:
        merged_state.update(dict(state))
    merged_source_context = dict(current.get("source_context") or {})
    if source_context is not None:
        merged_source_context = _stable_source_context(source_context)
    pending_value = current.get("pending_intent") or {}
    if pending_intent is not None:
        pending_value = dict(pending_intent)
    next_status = str(status or current.get("status") or "running").strip() or "running"
    next_task_id = str(task_id if task_id is not None else current.get("task_id") or "").strip()
    next_task_class = str(task_class if task_class is not None else current.get("task_class") or "").strip()
    next_step_count = int(step_count if step_count is not None else current.get("step_count") or len(list(merged_state.get("executed_steps") or [])))
    next_last_tool_name = str(last_tool_name if last_tool_name is not None else current.get("last_tool_name") or "").strip()
    next_resume_count = int(resume_count if resume_count is not None else current.get("resume_count") or 0)
    updated_at = _utcnow()
    completed_at = updated_at if next_status in {"completed", "failed"} else None
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            conn.execute(
                """
                UPDATE runtime_checkpoints
                SET task_id = ?,
                    task_class = ?,
                    source_context_json = ?,
                    status = ?,
                    step_count = ?,
                    last_tool_name = ?,
                    pending_intent_json = ?,
                    state_json = ?,
                    final_response = ?,
                    failure_text = ?,
                    resume_count = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE checkpoint_id = ?
                """,
                (
                    next_task_id,
                    next_task_class,
                    _json_dumps(merged_source_context),
                    next_status,
                    next_step_count,
                    next_last_tool_name,
                    _json_dumps(pending_value),
                    _json_dumps(merged_state),
                    str(final_response if final_response is not None else current.get("final_response") or ""),
                    str(failure_text if failure_text is not None else current.get("failure_text") or ""),
                    next_resume_count,
                    updated_at,
                    completed_at,
                    checkpoint_id,
                ),
            )
            _upsert_runtime_session(
                conn,
                session_id=str(current.get("session_id") or ""),
                request_preview=_preview_text(str(current.get("request_text") or "")),
                task_class=next_task_class,
                status=next_status,
                last_event_type=None,
                last_message=None,
                event_count=_session_event_count(conn, str(current.get("session_id") or "")),
                started_at=_session_started_at(conn, str(current.get("session_id") or ""), str(current.get("created_at") or updated_at)),
                updated_at=updated_at,
                checkpoint_id=checkpoint_id,
            )
            conn.commit()
        finally:
            conn.close()
    return get_runtime_checkpoint(checkpoint_id)


def record_runtime_tool_progress(
    checkpoint_id: str,
    *,
    executed_steps: list[dict[str, Any]],
    loop_source_context: dict[str, Any] | None,
    seen_tool_payloads: set[str] | list[str],
    pending_tool_payload: dict[str, Any] | None,
    last_tool_payload: dict[str, Any] | None,
    last_tool_response: dict[str, Any] | None,
    last_tool_name: str | None = None,
    task_class: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    state = {
        "executed_steps": [dict(step) for step in list(executed_steps or [])],
        "seen_tool_payloads": sorted({str(item) for item in list(seen_tool_payloads or []) if str(item)}),
        "loop_source_context": _stable_source_context(loop_source_context),
        "pending_tool_payload": dict(pending_tool_payload) if isinstance(pending_tool_payload, dict) else None,
        "last_tool_payload": dict(last_tool_payload) if isinstance(last_tool_payload, dict) else None,
        "last_tool_response": dict(last_tool_response) if isinstance(last_tool_response, dict) else None,
    }
    return update_runtime_checkpoint(
        checkpoint_id,
        state=state,
        pending_intent=state["pending_tool_payload"] or {},
        step_count=len(state["executed_steps"]),
        last_tool_name=str(last_tool_name or ""),
        task_class=task_class,
        status=status or "running",
    )


def finalize_runtime_checkpoint(
    checkpoint_id: str,
    *,
    status: str,
    final_response: str = "",
    failure_text: str = "",
) -> dict[str, Any] | None:
    clean_status = str(status or "completed").strip() or "completed"
    return update_runtime_checkpoint(
        checkpoint_id,
        status=clean_status,
        pending_intent={},
        final_response=final_response,
        failure_text=failure_text,
    )


def mark_stale_runtime_checkpoints_interrupted() -> int:
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            rows = conn.execute(
                """
                SELECT checkpoint_id, session_id, request_text, task_class
                FROM runtime_checkpoints
                WHERE status = 'running'
                ORDER BY updated_at ASC
                """
            ).fetchall()
            count = 0
            for row in rows:
                checkpoint_id = str(row["checkpoint_id"])
                timestamp = _utcnow()
                conn.execute(
                    """
                    UPDATE runtime_checkpoints
                    SET status = 'interrupted',
                        failure_text = ?,
                        updated_at = ?
                    WHERE checkpoint_id = ?
                    """,
                    ("Runtime stopped before the task finished.", timestamp, checkpoint_id),
                )
                session_id = str(row["session_id"] or "")
                existing = conn.execute(
                    "SELECT event_count, started_at, request_preview, last_checkpoint_id FROM runtime_sessions WHERE session_id = ? LIMIT 1",
                    (session_id,),
                ).fetchone()
                seq = int(existing["event_count"] if existing else 0) + 1
                details = {
                    "checkpoint_id": checkpoint_id,
                    "request_preview": _preview_text(str(row["request_text"] or "")),
                    "task_class": str(row["task_class"] or "").strip(),
                    "resume_available": True,
                }
                conn.execute(
                    """
                    INSERT INTO runtime_session_events (
                        session_id, seq, event_type, message, details_json, created_at
                    ) VALUES (?, ?, 'task_interrupted', ?, ?, ?)
                    """,
                    (
                        session_id,
                        seq,
                        "Previous runtime stopped before completion. Resume is available.",
                        _json_dumps(details),
                        timestamp,
                    ),
                )
                _upsert_runtime_session(
                    conn,
                    session_id=session_id,
                    request_preview=details["request_preview"],
                    task_class=details["task_class"],
                    status="interrupted",
                    last_event_type="task_interrupted",
                    last_message="Previous runtime stopped before completion. Resume is available.",
                    event_count=seq,
                    started_at=str(existing["started_at"] if existing else timestamp),
                    updated_at=timestamp,
                    checkpoint_id=str(existing["last_checkpoint_id"] if existing and existing["last_checkpoint_id"] else checkpoint_id),
                )
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()


def build_tool_receipt_key(
    *,
    checkpoint_id: str,
    step_index: int,
    intent: str,
    arguments: dict[str, Any] | None,
) -> str:
    payload = {
        "checkpoint_id": str(checkpoint_id or "").strip(),
        "step_index": max(0, int(step_index)),
        "intent": str(intent or "").strip(),
        "arguments": _normalize_for_hash(arguments or {}),
    }
    digest = hashlib.sha256(_json_dumps(payload).encode("utf-8")).hexdigest()
    return f"receipt-{digest}"


def is_mutating_tool_intent(intent: str) -> bool:
    return str(intent or "").strip() in _MUTATING_TOOL_INTENTS


def load_tool_receipt(receipt_key: str) -> dict[str, Any] | None:
    clean_key = str(receipt_key or "").strip()
    if not clean_key:
        return None
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            row = conn.execute(
                """
                SELECT receipt_key, session_id, checkpoint_id, tool_name, idempotency_key,
                       arguments_json, execution_json, created_at, updated_at
                FROM runtime_tool_receipts
                WHERE receipt_key = ?
                LIMIT 1
                """,
                (clean_key,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    data = dict(row)
    data["arguments"] = _json_loads(data.pop("arguments_json"), fallback={})
    data["execution"] = _json_loads(data.pop("execution_json"), fallback={})
    return data


def store_tool_receipt(
    *,
    receipt_key: str,
    session_id: str,
    checkpoint_id: str,
    tool_name: str,
    idempotency_key: str,
    arguments: dict[str, Any] | None,
    execution: dict[str, Any] | None,
) -> dict[str, Any]:
    clean_receipt_key = str(receipt_key or "").strip()
    if not clean_receipt_key:
        raise ValueError("receipt_key is required")
    timestamp = _utcnow()
    payload = {
        "receipt_key": clean_receipt_key,
        "session_id": str(session_id or "").strip(),
        "checkpoint_id": str(checkpoint_id or "").strip(),
        "tool_name": str(tool_name or "").strip(),
        "idempotency_key": str(idempotency_key or "").strip(),
        "arguments": dict(arguments or {}),
        "execution": dict(execution or {}),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            conn.execute(
                """
                INSERT INTO runtime_tool_receipts (
                    receipt_key, session_id, checkpoint_id, tool_name, idempotency_key,
                    arguments_json, execution_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(receipt_key) DO UPDATE SET
                    execution_json = excluded.execution_json,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_receipt_key,
                    payload["session_id"],
                    payload["checkpoint_id"],
                    payload["tool_name"],
                    payload["idempotency_key"],
                    _json_dumps(payload["arguments"]),
                    _json_dumps(payload["execution"]),
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return payload


def store_hive_idempotent_result(
    *,
    idempotency_key: str,
    operation_kind: str,
    response_payload: dict[str, Any],
) -> None:
    clean_key = str(idempotency_key or "").strip()
    if not clean_key:
        return
    timestamp = _utcnow()
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            conn.execute(
                """
                INSERT INTO hive_idempotency_keys (
                    idempotency_key, operation_kind, response_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    response_json = excluded.response_json,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_key,
                    str(operation_kind or "").strip(),
                    _json_dumps(response_payload),
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def load_hive_idempotent_result(idempotency_key: str) -> dict[str, Any] | None:
    clean_key = str(idempotency_key or "").strip()
    if not clean_key:
        return None
    with _LOCK:
        conn = get_connection(_runtime_db_path())
        try:
            row = conn.execute(
                """
                SELECT response_json
                FROM hive_idempotency_keys
                WHERE idempotency_key = ?
                LIMIT 1
                """,
                (clean_key,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return _json_loads(row["response_json"], fallback={})


def _runtime_db_path() -> str | Path:
    return _DB_PATH_OVERRIDE or DEFAULT_DB_PATH


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def _json_loads(raw: Any, *, fallback: Any) -> Any:
    try:
        loaded = json.loads(str(raw or ""))
    except Exception:
        return fallback
    if isinstance(fallback, dict):
        return loaded if isinstance(loaded, dict) else fallback
    if isinstance(fallback, list):
        return loaded if isinstance(loaded, list) else fallback
    if isinstance(fallback, str):
        return loaded if isinstance(loaded, str) else fallback
    return loaded


def _preview_text(text: str, *, limit: int = 180) -> str:
    compact = " ".join(str(text or "").split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 3)].rstrip() + "..."


def _clean_request_preview(value: Any) -> str:
    return _preview_text(str(value or ""))


def _stable_source_context(source_context: dict[str, Any] | None) -> dict[str, Any]:
    stable = dict(source_context or {})
    history = []
    for item in list(stable.get("conversation_history") or [])[-12:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        history.append({"role": role, "content": content[:4000]})
    stable["conversation_history"] = history
    return stable


def _merge_loop_source_context(existing: Any, incoming: dict[str, Any]) -> dict[str, Any]:
    merged = _stable_source_context(existing if isinstance(existing, dict) else {})
    merged.update(_stable_source_context(incoming))
    history = []
    for item in list(merged.get("conversation_history") or [])[-12:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        history.append({"role": role, "content": content[:4000]})
    merged["conversation_history"] = history
    return merged


def _normalize_for_hash(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_for_hash(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_for_hash(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_for_hash(item) for item in value]
    return value


def _event_status(event_type: str) -> str:
    lowered = str(event_type or "").strip().lower()
    if lowered in {
        "tool_failed",
        "task_failed",
        "task_envelope_failed",
        "task_envelope_step_failed",
        "task_envelope_merge_failed",
        "task_envelope_capacity_blocked",
        "task_envelope_missing_receipts",
        "task_envelope_missing_subtasks",
        "task_envelope_dependency_blocked",
        "task_envelope_dependency_failed",
        "task_envelope_restore_failed",
        "task_envelope_rollback_failed",
    }:
        return "failed"
    if lowered in {"task_interrupted"}:
        return "interrupted"
    if lowered in {"tool_preview", "task_pending_approval"}:
        return "pending_approval"
    if lowered in {"task_completed", "tool_loop_completed", "task_envelope_completed", "task_envelope_merge_completed"}:
        return "completed"
    if lowered in {
        "task_received",
        "task_resumed",
        "task_classified",
        "tool_selected",
        "tool_started",
        "tool_loop_resumed",
        "tool_executed",
        "tool_synthesizing",
        "task_envelope_started",
        "task_envelope_children_scheduled",
        "task_envelope_step_completed",
        "task_envelope_restore_completed",
        "task_envelope_rollback_completed",
    }:
        return "running"
    return lowered or "running"


def _session_event_count(conn: Any, session_id: str) -> int:
    row = conn.execute(
        "SELECT event_count FROM runtime_sessions WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    return int(row["event_count"]) if row else 0


def _session_started_at(conn: Any, session_id: str, fallback: str) -> str:
    row = conn.execute(
        "SELECT started_at FROM runtime_sessions WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    return str(row["started_at"]) if row and row["started_at"] else fallback


def _upsert_runtime_session(
    conn: Any,
    *,
    session_id: str,
    request_preview: str,
    task_class: str,
    status: str,
    last_event_type: str | None,
    last_message: str | None,
    event_count: int,
    started_at: str,
    updated_at: str,
    checkpoint_id: str | None,
) -> None:
    existing = conn.execute(
        """
        SELECT request_preview, task_class, status, last_event_type, last_message, event_count, started_at, last_checkpoint_id
        FROM runtime_sessions
        WHERE session_id = ?
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO runtime_sessions (
            session_id, started_at, updated_at, event_count, last_event_type,
            last_message, request_preview, task_class, status, last_checkpoint_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            started_at = excluded.started_at,
            updated_at = excluded.updated_at,
            event_count = excluded.event_count,
            last_event_type = excluded.last_event_type,
            last_message = excluded.last_message,
            request_preview = excluded.request_preview,
            task_class = excluded.task_class,
            status = excluded.status,
            last_checkpoint_id = excluded.last_checkpoint_id
        """,
        (
            session_id,
            started_at,
            updated_at,
            event_count,
            last_event_type if last_event_type is not None else str(existing["last_event_type"] if existing else ""),
            last_message if last_message is not None else str(existing["last_message"] if existing else ""),
            request_preview or str(existing["request_preview"] if existing else ""),
            task_class or str(existing["task_class"] if existing else ""),
            status or str(existing["status"] if existing else "running"),
            checkpoint_id or str(existing["last_checkpoint_id"] if existing else ""),
        ),
    )
