from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_columns(conn: Any, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _add_column_if_missing(conn: Any, table_name: str, name: str, definition: str) -> None:
    if name not in _table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")


def _init_tables() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dialogue_sessions (
                session_id TEXT PRIMARY KEY,
                last_subject TEXT,
                topic_hints_json TEXT NOT NULL DEFAULT '[]',
                last_intent_mode TEXT,
                current_user_goal TEXT,
                assistant_commitments_json TEXT NOT NULL DEFAULT '[]',
                unresolved_followups_json TEXT NOT NULL DEFAULT '[]',
                user_stance TEXT,
                emotional_tone TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        _add_column_if_missing(conn, "dialogue_sessions", "current_user_goal", "TEXT")
        _add_column_if_missing(conn, "dialogue_sessions", "assistant_commitments_json", "TEXT NOT NULL DEFAULT '[]'")
        _add_column_if_missing(conn, "dialogue_sessions", "unresolved_followups_json", "TEXT NOT NULL DEFAULT '[]'")
        _add_column_if_missing(conn, "dialogue_sessions", "user_stance", "TEXT")
        _add_column_if_missing(conn, "dialogue_sessions", "emotional_tone", "TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dialogue_turns (
                turn_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                raw_input TEXT NOT NULL,
                normalized_input TEXT NOT NULL,
                reconstructed_input TEXT NOT NULL,
                speaker_role TEXT NOT NULL DEFAULT 'user',
                topic_hints_json TEXT NOT NULL DEFAULT '[]',
                reference_targets_json TEXT NOT NULL DEFAULT '[]',
                understanding_confidence REAL NOT NULL DEFAULT 0.0,
                quality_flags_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """
        )
        _add_column_if_missing(conn, "dialogue_turns", "speaker_role", "TEXT NOT NULL DEFAULT 'user'")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dialogue_turns_session_created ON dialogue_turns(session_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dialogue_topic_archives (
                archive_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                last_subject TEXT,
                topic_hints_json TEXT NOT NULL DEFAULT '[]',
                current_user_goal TEXT,
                assistant_commitments_json TEXT NOT NULL DEFAULT '[]',
                unresolved_followups_json TEXT NOT NULL DEFAULT '[]',
                closure_status TEXT NOT NULL DEFAULT 'resolved',
                closure_reason TEXT NOT NULL DEFAULT 'topic_shift',
                summary TEXT NOT NULL DEFAULT '',
                closing_user_input TEXT,
                closing_assistant_output TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dialogue_topic_archives_session_created ON dialogue_topic_archives(session_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adaptive_lexicon (
                term TEXT NOT NULL,
                canonical TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'global',
                source TEXT NOT NULL DEFAULT 'manual',
                confidence REAL NOT NULL DEFAULT 0.75,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (term, scope)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS response_feedback (
                feedback_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn_id TEXT,
                task_id TEXT,
                feedback_type TEXT NOT NULL,
                feedback_value REAL NOT NULL DEFAULT 0.0,
                user_correction TEXT,
                context_snapshot TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_response_feedback_session ON response_feedback(session_id, created_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def session_lexicon(session_id: str) -> dict[str, str]:
    _init_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT term, canonical
            FROM adaptive_lexicon
            WHERE scope IN ('global', ?)
            ORDER BY scope = ? DESC, confidence DESC, updated_at DESC
            """,
            (session_id, session_id),
        ).fetchall()
        return {str(row["term"]).lower(): str(row["canonical"]).lower() for row in rows}
    finally:
        conn.close()


def upsert_lexicon_term(term: str, canonical: str, *, scope: str = "global", source: str = "manual", confidence: float = 0.75) -> None:
    _init_tables()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO adaptive_lexicon (
                term, canonical, scope, source, confidence, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (term.strip().lower(), canonical.strip().lower(), scope, source, float(confidence), _utcnow()),
        )
        conn.commit()
    finally:
        conn.close()


def get_dialogue_session(session_id: str) -> dict[str, Any]:
    _init_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT session_id, last_subject, topic_hints_json, last_intent_mode,
                   current_user_goal, assistant_commitments_json, unresolved_followups_json,
                   user_stance, emotional_tone, updated_at
            FROM dialogue_sessions
            WHERE session_id = ?
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return {
                "session_id": session_id,
                "last_subject": None,
                "topic_hints": [],
                "last_intent_mode": None,
                "current_user_goal": None,
                "assistant_commitments": [],
                "unresolved_followups": [],
                "user_stance": None,
                "emotional_tone": None,
                "updated_at": None,
            }
        data = dict(row)
        data["topic_hints"] = json.loads(data.pop("topic_hints_json") or "[]")
        data["assistant_commitments"] = json.loads(data.pop("assistant_commitments_json") or "[]")
        data["unresolved_followups"] = json.loads(data.pop("unresolved_followups_json") or "[]")
        return data
    finally:
        conn.close()


def update_dialogue_session(
    session_id: str,
    *,
    last_subject: str | None,
    topic_hints: list[str],
    last_intent_mode: str | None,
    current_user_goal: str | None = None,
    assistant_commitments: list[str] | None = None,
    unresolved_followups: list[str] | None = None,
    user_stance: str | None = None,
    emotional_tone: str | None = None,
) -> None:
    _init_tables()
    existing = get_dialogue_session(session_id)
    resolved_last_subject = existing.get("last_subject") if last_subject is None else (str(last_subject).strip() or None)
    resolved_last_intent_mode = existing.get("last_intent_mode") if last_intent_mode is None else (str(last_intent_mode).strip() or None)
    resolved_current_user_goal = existing.get("current_user_goal") if current_user_goal is None else (str(current_user_goal).strip() or None)
    resolved_user_stance = existing.get("user_stance") if user_stance is None else (str(user_stance).strip() or None)
    resolved_emotional_tone = existing.get("emotional_tone") if emotional_tone is None else (str(emotional_tone).strip() or None)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO dialogue_sessions (
                session_id, last_subject, topic_hints_json, last_intent_mode,
                current_user_goal, assistant_commitments_json, unresolved_followups_json,
                user_stance, emotional_tone, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                resolved_last_subject,
                json.dumps(topic_hints, sort_keys=True),
                resolved_last_intent_mode,
                resolved_current_user_goal,
                json.dumps(
                    list(assistant_commitments if assistant_commitments is not None else existing.get("assistant_commitments") or []),
                    sort_keys=True,
                ),
                json.dumps(
                    list(unresolved_followups if unresolved_followups is not None else existing.get("unresolved_followups") or []),
                    sort_keys=True,
                ),
                resolved_user_stance,
                resolved_emotional_tone,
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _archive_summary(
    *,
    last_subject: str | None,
    current_user_goal: str | None,
    assistant_commitments: list[str] | None,
    unresolved_followups: list[str] | None,
) -> str:
    fragments: list[str] = []
    goal = str(current_user_goal or "").strip()
    subject = str(last_subject or "").strip()
    commitments = [str(item).strip() for item in list(assistant_commitments or []) if str(item).strip()]
    unresolved = [str(item).strip() for item in list(unresolved_followups or []) if str(item).strip()]
    if goal:
        fragments.append(goal)
    elif subject:
        fragments.append(subject)
    if unresolved:
        fragments.append(f"unresolved: {unresolved[0]}")
    elif commitments:
        fragments.append(f"commitment: {commitments[0]}")
    summary = " | ".join(fragment for fragment in fragments if fragment).strip()
    return summary[:280]


def archive_dialogue_topic(
    session_id: str,
    *,
    last_subject: str | None,
    topic_hints: list[str] | None,
    current_user_goal: str | None,
    assistant_commitments: list[str] | None = None,
    unresolved_followups: list[str] | None = None,
    closure_status: str,
    closure_reason: str,
    closing_user_input: str | None = None,
    closing_assistant_output: str | None = None,
) -> str | None:
    _init_tables()
    normalized_session = str(session_id or "").strip()
    normalized_goal = str(current_user_goal or "").strip()
    normalized_subject = str(last_subject or "").strip()
    normalized_topics = [str(item).strip() for item in list(topic_hints or []) if str(item).strip()]
    normalized_commitments = [str(item).strip() for item in list(assistant_commitments or []) if str(item).strip()]
    normalized_unresolved = [str(item).strip() for item in list(unresolved_followups or []) if str(item).strip()]
    if not normalized_session:
        return None
    if not any([normalized_goal, normalized_subject, normalized_topics, normalized_commitments, normalized_unresolved]):
        return None
    archive_id = str(uuid.uuid4())
    summary = _archive_summary(
        last_subject=normalized_subject,
        current_user_goal=normalized_goal,
        assistant_commitments=normalized_commitments,
        unresolved_followups=normalized_unresolved,
    )
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO dialogue_topic_archives (
                archive_id, session_id, last_subject, topic_hints_json, current_user_goal,
                assistant_commitments_json, unresolved_followups_json, closure_status,
                closure_reason, summary, closing_user_input, closing_assistant_output, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                archive_id,
                normalized_session,
                normalized_subject or None,
                json.dumps(normalized_topics, sort_keys=True),
                normalized_goal or None,
                json.dumps(normalized_commitments, sort_keys=True),
                json.dumps(normalized_unresolved, sort_keys=True),
                str(closure_status or "resolved").strip().lower() or "resolved",
                str(closure_reason or "topic_shift").strip().lower() or "topic_shift",
                summary,
                str(closing_user_input or "").strip() or None,
                str(closing_assistant_output or "").strip() or None,
                _utcnow(),
            ),
        )
        conn.commit()
        return archive_id
    finally:
        conn.close()


def recent_archived_dialogue_topics(session_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    _init_tables()
    normalized_session = str(session_id or "").strip()
    if not normalized_session:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM dialogue_topic_archives
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (normalized_session, max(1, int(limit))),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["topic_hints"] = json.loads(data.pop("topic_hints_json") or "[]")
            data["assistant_commitments"] = json.loads(data.pop("assistant_commitments_json") or "[]")
            data["unresolved_followups"] = json.loads(data.pop("unresolved_followups_json") or "[]")
            out.append(data)
        return out
    finally:
        conn.close()


def recent_dialogue_turns(
    session_id: str,
    *,
    limit: int = 5,
    speaker_roles: tuple[str, ...] | list[str] | None = ("user",),
) -> list[dict[str, Any]]:
    _init_tables()
    normalized_roles = tuple(
        role
        for role in (str(item or "").strip().lower() for item in list(speaker_roles or []))
        if role
    )
    conn = get_connection()
    try:
        if normalized_roles:
            placeholders = ", ".join("?" for _ in normalized_roles)
            rows = conn.execute(
                f"""
                SELECT *
                FROM dialogue_turns
                WHERE session_id = ?
                  AND lower(speaker_role) IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, *normalized_roles, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM dialogue_turns
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["topic_hints"] = json.loads(data.pop("topic_hints_json") or "[]")
            data["reference_targets"] = json.loads(data.pop("reference_targets_json") or "[]")
            data["quality_flags"] = json.loads(data.pop("quality_flags_json") or "[]")
            data["speaker_role"] = str(data.get("speaker_role") or "user").strip().lower() or "user"
            out.append(data)
        return out
    finally:
        conn.close()


def recent_dialogue_turns_any(
    *,
    limit: int = 10,
    speaker_roles: tuple[str, ...] | list[str] | None = ("user",),
) -> list[dict[str, Any]]:
    _init_tables()
    normalized_roles = tuple(
        role
        for role in (str(item or "").strip().lower() for item in list(speaker_roles or []))
        if role
    )
    conn = get_connection()
    try:
        if normalized_roles:
            placeholders = ", ".join("?" for _ in normalized_roles)
            rows = conn.execute(
                f"""
                SELECT *
                FROM dialogue_turns
                WHERE lower(speaker_role) IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*normalized_roles, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM dialogue_turns
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["topic_hints"] = json.loads(data.pop("topic_hints_json") or "[]")
            data["reference_targets"] = json.loads(data.pop("reference_targets_json") or "[]")
            data["quality_flags"] = json.loads(data.pop("quality_flags_json") or "[]")
            data["speaker_role"] = str(data.get("speaker_role") or "user").strip().lower() or "user"
            out.append(data)
        return out
    finally:
        conn.close()


def record_dialogue_turn(
    session_id: str,
    *,
    raw_input: str,
    normalized_input: str,
    reconstructed_input: str,
    speaker_role: str = "user",
    topic_hints: list[str],
    reference_targets: list[str],
    understanding_confidence: float,
    quality_flags: list[str],
) -> str:
    _init_tables()
    turn_id = str(uuid.uuid4())
    normalized_role = str(speaker_role or "user").strip().lower() or "user"
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO dialogue_turns (
                turn_id, session_id, raw_input, normalized_input, reconstructed_input,
                speaker_role, topic_hints_json, reference_targets_json, understanding_confidence,
                quality_flags_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id,
                session_id,
                raw_input,
                normalized_input,
                reconstructed_input,
                normalized_role,
                json.dumps(topic_hints, sort_keys=True),
                json.dumps(reference_targets, sort_keys=True),
                float(understanding_confidence),
                json.dumps(quality_flags, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
        return turn_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Response feedback loop
# ---------------------------------------------------------------------------

def record_response_feedback(
    session_id: str,
    *,
    feedback_type: str,
    feedback_value: float = 1.0,
    turn_id: str | None = None,
    task_id: str | None = None,
    user_correction: str | None = None,
    context_snapshot: str | None = None,
) -> str:
    """
    Record user feedback on a response.
    feedback_type: 'approve', 'reject', 'correction', 'thumbs_up', 'thumbs_down'
    feedback_value: -1.0 to 1.0 (negative = bad, positive = good)
    user_correction: the corrected text if the user rephrased/fixed the answer
    """
    _init_tables()
    feedback_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO response_feedback (
                feedback_id, session_id, turn_id, task_id,
                feedback_type, feedback_value, user_correction,
                context_snapshot, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback_id,
                session_id,
                turn_id,
                task_id,
                str(feedback_type or "unknown").strip().lower(),
                max(-1.0, min(1.0, float(feedback_value))),
                user_correction,
                context_snapshot,
                _utcnow(),
            ),
        )
        conn.commit()
        return feedback_id
    finally:
        conn.close()


def recent_feedback(
    session_id: str | None = None,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Retrieve recent feedback entries, optionally scoped to a session."""
    _init_tables()
    conn = get_connection()
    try:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM response_feedback WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM response_feedback ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def feedback_stats(*, lookback_days: int = 7) -> dict[str, Any]:
    """Aggregate feedback stats for heuristic weight adjustment."""
    _init_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT feedback_type,
                   COUNT(*) as count,
                   AVG(feedback_value) as avg_value,
                   SUM(CASE WHEN feedback_value > 0 THEN 1 ELSE 0 END) as positive,
                   SUM(CASE WHEN feedback_value < 0 THEN 1 ELSE 0 END) as negative
            FROM response_feedback
            WHERE created_at >= datetime('now', ?)
            GROUP BY feedback_type
            """,
            (f"-{lookback_days} days",),
        ).fetchall()
        stats: dict[str, Any] = {"total": 0, "by_type": {}}
        for row in rows:
            entry = dict(row)
            stats["by_type"][entry["feedback_type"]] = entry
            stats["total"] += int(entry["count"])
        return stats
    finally:
        conn.close()
