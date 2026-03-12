from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def queue_curiosity_topic(
    *,
    session_id: str,
    task_id: str,
    trace_id: str,
    topic: str,
    topic_kind: str,
    reason: str,
    priority: float,
    source_profiles: list[dict[str, Any]],
) -> str:
    topic_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO curiosity_topics (
                topic_id, session_id, originating_task_id, trace_id, topic, topic_kind,
                reason, priority, source_profiles_json, status, created_at, updated_at,
                last_run_at, candidate_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, NULL, NULL)
            """,
            (
                topic_id,
                session_id,
                task_id,
                trace_id,
                topic,
                topic_kind,
                reason,
                float(priority),
                json.dumps(source_profiles, sort_keys=True),
                now,
                now,
            ),
        )
        conn.commit()
        return topic_id
    finally:
        conn.close()


def update_curiosity_topic(
    topic_id: str,
    *,
    status: str,
    candidate_id: str | None = None,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE curiosity_topics
            SET status = ?, candidate_id = COALESCE(?, candidate_id), last_run_at = ?, updated_at = ?
            WHERE topic_id = ?
            """,
            (status, candidate_id, _utcnow(), _utcnow(), topic_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_curiosity_run(
    *,
    topic_id: str,
    task_id: str,
    trace_id: str,
    query_text: str,
    source_profile_ids: list[str],
    snippets: list[dict[str, Any]],
    candidate_id: str | None,
    outcome: str,
) -> str:
    run_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO curiosity_runs (
                run_id, topic_id, task_id, trace_id, query_text, source_profile_ids_json,
                snippets_json, candidate_id, outcome, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                topic_id,
                task_id,
                trace_id,
                query_text,
                json.dumps(source_profile_ids, sort_keys=True),
                json.dumps(snippets, sort_keys=True),
                candidate_id,
                outcome,
                _utcnow(),
            ),
        )
        conn.commit()
        return run_id
    finally:
        conn.close()


def recent_curiosity_topics(limit: int = 20) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM curiosity_topics
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_topic(dict(row)) for row in rows]
    finally:
        conn.close()


def recent_curiosity_topics_for_session(session_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    normalized = str(session_id or "").strip()
    if not normalized:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM curiosity_topics
            WHERE session_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (normalized, limit),
        ).fetchall()
        return [_row_to_topic(dict(row)) for row in rows]
    finally:
        conn.close()


def recent_curiosity_runs(limit: int = 20) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM curiosity_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_run(dict(row)) for row in rows]
    finally:
        conn.close()


def recent_curiosity_runs_for_session(session_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    normalized = str(session_id or "").strip()
    if not normalized:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT runs.*, topics.session_id, topics.topic
            FROM curiosity_runs AS runs
            JOIN curiosity_topics AS topics
              ON topics.topic_id = runs.topic_id
            WHERE topics.session_id = ?
            ORDER BY runs.created_at DESC
            LIMIT ?
            """,
            (normalized, limit),
        ).fetchall()
        return [_row_to_run(dict(row)) for row in rows]
    finally:
        conn.close()


def _row_to_topic(row: dict[str, Any]) -> dict[str, Any]:
    row["source_profiles"] = json.loads(row.pop("source_profiles_json") or "[]")
    return row


def _row_to_run(row: dict[str, Any]) -> dict[str, Any]:
    row["source_profile_ids"] = json.loads(row.pop("source_profile_ids_json") or "[]")
    row["snippets"] = json.loads(row.pop("snippets_json") or "[]")
    return row
