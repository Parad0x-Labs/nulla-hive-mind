from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage.db import DEFAULT_DB_PATH, get_connection
from storage.migrations import run_migrations


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_loads(raw: str | None, fallback: Any) -> Any:
    try:
        if raw is None or raw == "":
            return fallback
        return json.loads(raw)
    except Exception:
        return fallback


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def append_contribution_proof_receipt(
    *,
    entry_id: str,
    task_id: str,
    helper_peer_id: str,
    parent_peer_id: str = "",
    stage: str,
    outcome: str = "",
    finality_state: str = "",
    finality_depth: int = 0,
    finality_target: int = 0,
    compute_credits: float = 0.0,
    points_awarded: int = 0,
    challenge_reason: str = "",
    evidence: dict[str, Any] | None = None,
    created_at: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    now = str(created_at or _utcnow()).strip() or _utcnow()
    conn = get_connection(db_target)
    try:
        previous = conn.execute(
            """
            SELECT receipt_id, receipt_hash
            FROM contribution_proof_receipts
            WHERE entry_id = ?
            ORDER BY created_at DESC, receipt_id DESC
            LIMIT 1
            """,
            (str(entry_id or "").strip(),),
        ).fetchone()
        previous_receipt_id = str(previous["receipt_id"] or "") if previous else ""
        previous_receipt_hash = str(previous["receipt_hash"] or "") if previous else ""
        payload = {
            "entry_id": str(entry_id or "").strip(),
            "task_id": str(task_id or "").strip(),
            "helper_peer_id": str(helper_peer_id or "").strip(),
            "parent_peer_id": str(parent_peer_id or "").strip(),
            "stage": str(stage or "").strip(),
            "outcome": str(outcome or "").strip(),
            "finality_state": str(finality_state or "").strip(),
            "finality_depth": max(0, int(finality_depth)),
            "finality_target": max(0, int(finality_target)),
            "compute_credits": round(max(0.0, float(compute_credits or 0.0)), 6),
            "points_awarded": max(0, int(points_awarded or 0)),
            "challenge_reason": str(challenge_reason or "").strip(),
            "evidence": dict(evidence or {}),
            "created_at": now,
            "previous_receipt_hash": previous_receipt_hash,
        }
        payload_json = _canonical_payload(payload)
        receipt_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        receipt_id = f"proof-{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO contribution_proof_receipts (
                receipt_id, entry_id, task_id, helper_peer_id, parent_peer_id,
                stage, outcome, finality_state, finality_depth, finality_target,
                compute_credits, points_awarded, challenge_reason,
                previous_receipt_id, previous_receipt_hash, receipt_hash, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                payload["entry_id"],
                payload["task_id"],
                payload["helper_peer_id"],
                payload["parent_peer_id"],
                payload["stage"],
                payload["outcome"],
                payload["finality_state"],
                payload["finality_depth"],
                payload["finality_target"],
                payload["compute_credits"],
                payload["points_awarded"],
                payload["challenge_reason"],
                previous_receipt_id,
                previous_receipt_hash,
                receipt_hash,
                payload_json,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "receipt_id": receipt_id,
        "entry_id": str(entry_id or "").strip(),
        "task_id": str(task_id or "").strip(),
        "helper_peer_id": str(helper_peer_id or "").strip(),
        "parent_peer_id": str(parent_peer_id or "").strip(),
        "stage": str(stage or "").strip(),
        "outcome": str(outcome or "").strip(),
        "finality_state": str(finality_state or "").strip(),
        "finality_depth": max(0, int(finality_depth)),
        "finality_target": max(0, int(finality_target)),
        "compute_credits": round(max(0.0, float(compute_credits or 0.0)), 6),
        "points_awarded": max(0, int(points_awarded or 0)),
        "challenge_reason": str(challenge_reason or "").strip(),
        "previous_receipt_id": previous_receipt_id,
        "previous_receipt_hash": previous_receipt_hash,
        "receipt_hash": receipt_hash,
        "payload": dict(evidence or {}),
        "created_at": now,
    }


def list_contribution_proof_receipts(
    *,
    entry_id: str | None = None,
    helper_peer_id: str | None = None,
    stages: list[str] | tuple[str, ...] | None = None,
    limit: int = 50,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    where: list[str] = []
    params: list[Any] = []
    if str(entry_id or "").strip():
        where.append("entry_id = ?")
        params.append(str(entry_id or "").strip())
    if str(helper_peer_id or "").strip():
        where.append("helper_peer_id = ?")
        params.append(str(helper_peer_id or "").strip())
    clean_stages = [str(item or "").strip() for item in list(stages or []) if str(item or "").strip()]
    if clean_stages:
        where.append(f"stage IN ({', '.join('?' for _ in clean_stages)})")
        params.extend(clean_stages)
    query = "SELECT * FROM contribution_proof_receipts"
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY created_at DESC, receipt_id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    conn = get_connection(db_target)
    try:
        rows = conn.execute(query, tuple(params)).fetchall()
    finally:
        conn.close()
    return [
        {
            "receipt_id": str(row["receipt_id"] or ""),
            "entry_id": str(row["entry_id"] or ""),
            "task_id": str(row["task_id"] or ""),
            "helper_peer_id": str(row["helper_peer_id"] or ""),
            "parent_peer_id": str(row["parent_peer_id"] or ""),
            "stage": str(row["stage"] or ""),
            "outcome": str(row["outcome"] or ""),
            "finality_state": str(row["finality_state"] or ""),
            "finality_depth": int(row["finality_depth"] or 0),
            "finality_target": int(row["finality_target"] or 0),
            "compute_credits": float(row["compute_credits"] or 0.0),
            "points_awarded": int(row["points_awarded"] or 0),
            "challenge_reason": str(row["challenge_reason"] or ""),
            "previous_receipt_id": str(row["previous_receipt_id"] or ""),
            "previous_receipt_hash": str(row["previous_receipt_hash"] or ""),
            "receipt_hash": str(row["receipt_hash"] or ""),
            "payload": _json_loads(row["payload_json"], {}),
            "created_at": str(row["created_at"] or ""),
        }
        for row in rows
    ]
