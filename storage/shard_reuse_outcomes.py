from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shard_reuse_outcomes (
                event_id TEXT PRIMARY KEY,
                shard_id TEXT NOT NULL,
                receipt_id TEXT,
                source_peer_id TEXT,
                source_node_id TEXT,
                manifest_id TEXT,
                content_hash TEXT,
                validation_state TEXT,
                task_id TEXT,
                session_id TEXT,
                task_class TEXT,
                response_class TEXT,
                outcome_label TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 0,
                durable INTEGER NOT NULL DEFAULT 0,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_shard_reuse_outcomes_shard
            ON shard_reuse_outcomes(shard_id, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_shard_reuse_outcomes_receipt
            ON shard_reuse_outcomes(receipt_id, created_at DESC)
            """
        )
        conn.commit()
    finally:
        conn.close()


def record_shard_reuse_outcomes(
    *,
    citations: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    task_id: str,
    session_id: str,
    task_class: str,
    response_class: str,
    success: bool,
    durable: bool,
    details: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    clean = _normalize_remote_shard_citations(citations)
    if not clean:
        return []
    _init_table()
    outcome_label = _outcome_label(success=success, durable=durable)
    created_at = _utcnow()
    rows: list[dict[str, Any]] = []
    conn = get_connection()
    try:
        for citation in clean:
            row = {
                "event_id": f"reuse-{uuid.uuid4().hex}",
                "shard_id": str(citation.get("shard_id") or "").strip(),
                "receipt_id": str(citation.get("receipt_id") or "").strip() or None,
                "source_peer_id": str(citation.get("source_peer_id") or "").strip() or None,
                "source_node_id": str(citation.get("source_node_id") or "").strip() or None,
                "manifest_id": str(citation.get("manifest_id") or "").strip() or None,
                "content_hash": str(citation.get("content_hash") or "").strip() or None,
                "validation_state": str(citation.get("validation_state") or "").strip() or None,
                "task_id": str(task_id or "").strip() or None,
                "session_id": str(session_id or "").strip() or None,
                "task_class": str(task_class or "").strip() or None,
                "response_class": str(response_class or "").strip() or None,
                "outcome_label": outcome_label,
                "success": bool(success),
                "durable": bool(durable),
                "details": dict(details or {}),
                "created_at": created_at,
            }
            conn.execute(
                """
                INSERT INTO shard_reuse_outcomes (
                    event_id, shard_id, receipt_id, source_peer_id, source_node_id,
                    manifest_id, content_hash, validation_state, task_id, session_id,
                    task_class, response_class, outcome_label, success, durable,
                    details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["event_id"],
                    row["shard_id"],
                    row["receipt_id"],
                    row["source_peer_id"],
                    row["source_node_id"],
                    row["manifest_id"],
                    row["content_hash"],
                    row["validation_state"],
                    row["task_id"],
                    row["session_id"],
                    row["task_class"],
                    row["response_class"],
                    row["outcome_label"],
                    1 if row["success"] else 0,
                    1 if row["durable"] else 0,
                    json.dumps(row["details"], sort_keys=True),
                    row["created_at"],
                ),
            )
            rows.append(row)
        conn.commit()
    finally:
        conn.close()
    return rows


def summarize_reuse_outcomes_for_shards(shard_ids: list[str] | tuple[str, ...]) -> dict[str, dict[str, Any]]:
    clean_ids = [str(item or "").strip() for item in shard_ids if str(item or "").strip()]
    if not clean_ids:
        return {}
    _init_table()
    placeholders = ", ".join("?" for _ in clean_ids)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM shard_reuse_outcomes
            WHERE shard_id IN ({placeholders})
            ORDER BY created_at DESC
            """,
            tuple(clean_ids),
        ).fetchall()
    finally:
        conn.close()
    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = _row_to_outcome(dict(row))
        shard_id = str(data.get("shard_id") or "").strip()
        if not shard_id:
            continue
        summary = summaries.setdefault(
            shard_id,
            {
                "total_count": 0,
                "success_count": 0,
                "durable_count": 0,
                "last_recorded_at": "",
                "last_outcome_label": "",
                "last_response_class": "",
                "last_validation_state": "",
                "last_receipt_id": "",
            },
        )
        summary["total_count"] += 1
        summary["success_count"] += 1 if data.get("success") else 0
        summary["durable_count"] += 1 if data.get("durable") else 0
        if not summary["last_recorded_at"]:
            summary["last_recorded_at"] = str(data.get("created_at") or "")
            summary["last_outcome_label"] = str(data.get("outcome_label") or "")
            summary["last_response_class"] = str(data.get("response_class") or "")
            summary["last_validation_state"] = str(data.get("validation_state") or "")
            summary["last_receipt_id"] = str(data.get("receipt_id") or "")
    return summaries


def _normalize_remote_shard_citations(
    citations: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for citation in list(citations or []):
        if not isinstance(citation, dict):
            continue
        if str(citation.get("kind") or "").strip() != "remote_shard":
            continue
        shard_id = str(citation.get("shard_id") or "").strip()
        if not shard_id:
            continue
        receipt_id = str(citation.get("receipt_id") or "").strip()
        dedupe_key = (shard_id, receipt_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        clean.append(dict(citation))
    return clean


def _outcome_label(*, success: bool, durable: bool) -> str:
    if success and durable:
        return "durable_success"
    if success:
        return "successful"
    return "unsuccessful"


def _row_to_outcome(row: dict[str, Any]) -> dict[str, Any]:
    row["details"] = json.loads(row.pop("details_json") or "{}")
    row["success"] = bool(row.get("success"))
    row["durable"] = bool(row.get("durable"))
    return row
