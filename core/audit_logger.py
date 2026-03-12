from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from storage.db import execute_query, get_connection
from storage.event_log import append_event


def _ensure_audit_log_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON audit_log(event_type)"
        )
        conn.commit()
    finally:
        conn.close()

def log(
    event_type: str,
    target_id: Optional[str] = None,
    details: Optional[dict] = None,
    *,
    actor: str = "system",
    target_type: str = "generic",
    trace_id: str | None = None,
) -> None:
    event_id = str(uuid.uuid4())
    payload = details or {}
    _ensure_audit_log_table()
    execute_query(
        """
        INSERT INTO audit_log (
            event_id, event_type, actor, target_type, target_id, details_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            actor,
            target_type,
            target_id,
            json.dumps(payload, sort_keys=True),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    append_event(
        category=event_type,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        trace_id=trace_id,
        event_id=event_id,
    )
