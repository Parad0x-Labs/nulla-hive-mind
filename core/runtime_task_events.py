from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

from core.runtime_continuity import (
    append_runtime_event,
    configure_runtime_continuity_db_path,
    reset_runtime_continuity_state,
)

RuntimeEventSink = Callable[[dict[str, Any]], None]

_SINKS: dict[str, RuntimeEventSink] = {}
_LOCK = threading.RLock()


def new_runtime_event_stream_id() -> str:
    return f"runtime-stream-{uuid.uuid4().hex}"


def register_runtime_event_sink(stream_id: str, sink: RuntimeEventSink) -> None:
    key = str(stream_id or "").strip()
    if not key:
        return
    with _LOCK:
        _SINKS[key] = sink


def unregister_runtime_event_sink(stream_id: str) -> None:
    key = str(stream_id or "").strip()
    if not key:
        return
    with _LOCK:
        _SINKS.pop(key, None)


def configure_runtime_event_store(db_path: str | None) -> None:
    configure_runtime_continuity_db_path(db_path)


def emit_runtime_event(
    source_context: dict[str, Any] | None,
    *,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    payload = {
        "event_type": str(event_type or "status").strip() or "status",
        "message": str(message or "").strip(),
    }
    payload.update(dict(details or {}))
    stream_id = str((source_context or {}).get("runtime_event_stream_id") or "").strip()
    session_id = str((source_context or {}).get("runtime_session_id") or (source_context or {}).get("session_id") or "").strip()
    sink: RuntimeEventSink | None = None
    if session_id:
        payload = append_runtime_event(
            session_id=session_id,
            event_type=payload["event_type"],
            message=payload["message"],
            details={key: value for key, value in payload.items() if key not in {"event_type", "message"}},
        )
    if stream_id:
        with _LOCK:
            sink = _SINKS.get(stream_id)
    if sink is not None:
        try:
            sink(dict(payload))
        except Exception:
            return


def reset_runtime_event_state() -> None:
    with _LOCK:
        _SINKS.clear()
    reset_runtime_continuity_state()
