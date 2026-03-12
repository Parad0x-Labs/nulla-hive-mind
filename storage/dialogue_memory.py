from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dialogue_turns (
                turn_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                raw_input TEXT NOT NULL,
                normalized_input TEXT NOT NULL,
                reconstructed_input TEXT NOT NULL,
                topic_hints_json TEXT NOT NULL DEFAULT '[]',
                reference_targets_json TEXT NOT NULL DEFAULT '[]',
                understanding_confidence REAL NOT NULL DEFAULT 0.0,
                quality_flags_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dialogue_turns_session_created ON dialogue_turns(session_id, created_at DESC)"
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
            SELECT session_id, last_subject, topic_hints_json, last_intent_mode, updated_at
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
                "updated_at": None,
            }
        data = dict(row)
        data["topic_hints"] = json.loads(data.pop("topic_hints_json") or "[]")
        return data
    finally:
        conn.close()


def update_dialogue_session(session_id: str, *, last_subject: str | None, topic_hints: list[str], last_intent_mode: str | None) -> None:
    _init_tables()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO dialogue_sessions (
                session_id, last_subject, topic_hints_json, last_intent_mode, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, last_subject, json.dumps(topic_hints, sort_keys=True), last_intent_mode, _utcnow()),
        )
        conn.commit()
    finally:
        conn.close()


def recent_dialogue_turns(session_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    _init_tables()
    conn = get_connection()
    try:
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
            out.append(data)
        return out
    finally:
        conn.close()


def recent_dialogue_turns_any(*, limit: int = 10) -> list[dict[str, Any]]:
    _init_tables()
    conn = get_connection()
    try:
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
    topic_hints: list[str],
    reference_targets: list[str],
    understanding_confidence: float,
    quality_flags: list[str],
) -> str:
    _init_tables()
    turn_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO dialogue_turns (
                turn_id, session_id, raw_input, normalized_input, reconstructed_input,
                topic_hints_json, reference_targets_json, understanding_confidence,
                quality_flags_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                turn_id,
                session_id,
                raw_input,
                normalized_input,
                reconstructed_input,
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
