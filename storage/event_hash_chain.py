from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


_CHAIN_LOCK = threading.Lock()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_hash_chain (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                prev_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _compute_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"prev_hash": prev_hash, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def latest_hash() -> str:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT event_hash FROM event_hash_chain ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return str(row["event_hash"]) if row else "genesis"
    finally:
        conn.close()


def append_hashed_event(event_id: str, payload: dict[str, Any]) -> str:
    _init_table()
    with _CHAIN_LOCK:
        conn = get_connection()
        try:
            existing = conn.execute(
                "SELECT event_hash FROM event_hash_chain WHERE event_id = ? LIMIT 1",
                (event_id,),
            ).fetchone()
            if existing:
                return str(existing["event_hash"])

            latest = conn.execute(
                "SELECT event_hash FROM event_hash_chain ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = str(latest["event_hash"]) if latest else "genesis"
            event_hash = _compute_hash(prev_hash, payload)
            conn.execute(
                """
                INSERT INTO event_hash_chain (
                    event_id, prev_hash, event_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    prev_hash,
                    event_hash,
                    json.dumps(payload, sort_keys=True),
                    _utcnow(),
                ),
            )
            conn.commit()
            return event_hash
        finally:
            conn.close()


def verify_chain() -> bool:
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT event_id, prev_hash, event_hash, payload_json
            FROM event_hash_chain
            ORDER BY seq ASC
            """
        ).fetchall()
    finally:
        conn.close()

    prev_hash = "genesis"
    for row in rows:
        payload = json.loads(row["payload_json"])
        if str(row["prev_hash"]) != prev_hash:
            return False
        expected = _compute_hash(prev_hash, payload)
        if expected != row["event_hash"]:
            return False
        prev_hash = row["event_hash"]
    return True


def repair_chain() -> int:
    _init_table()
    with _CHAIN_LOCK:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT seq, event_id, payload_json
                FROM event_hash_chain
                ORDER BY seq ASC
                """
            ).fetchall()
            prev_hash = "genesis"
            repaired = 0
            for row in rows:
                payload = json.loads(row["payload_json"])
                event_hash = _compute_hash(prev_hash, payload)
                current = conn.execute(
                    "SELECT prev_hash, event_hash FROM event_hash_chain WHERE seq = ?",
                    (row["seq"],),
                ).fetchone()
                if not current or str(current["prev_hash"]) != prev_hash or str(current["event_hash"]) != event_hash:
                    conn.execute(
                        """
                        UPDATE event_hash_chain
                        SET prev_hash = ?, event_hash = ?
                        WHERE seq = ?
                        """,
                        (prev_hash, event_hash, row["seq"]),
                    )
                    repaired += 1
                prev_hash = event_hash
            conn.commit()
            return repaired
        finally:
            conn.close()
