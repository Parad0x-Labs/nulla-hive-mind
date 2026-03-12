from __future__ import annotations

import platform
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection
from storage.event_hash_chain import verify_chain
from storage.knowledge_index import active_presence
from storage.knowledge_manifests import all_manifests
from storage.replica_table import all_holders


@dataclass
class AuditSection:
    name: str
    status: str
    summary: str
    details: dict[str, Any]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process_survival_section() -> AuditSection:
    return AuditSection(
        name="process_survival",
        status="manual",
        summary="Confirm which long-running processes survived the soak and whether any restarted.",
        details={"requires_manual_check": True},
    )


def _task_state_section() -> AuditSection:
    conn = get_connection()
    try:
        latest_rows = conn.execute(
            """
            WITH latest AS (
                SELECT entity_type, entity_id, MAX(seq) AS max_seq
                FROM task_state_events
                GROUP BY entity_type, entity_id
            )
            SELECT e.entity_type, e.entity_id, e.to_state, e.created_at
            FROM task_state_events e
            INNER JOIN latest l
              ON l.entity_type = e.entity_type
             AND l.entity_id = e.entity_id
             AND l.max_seq = e.seq
            """
        ).fetchall()
        orphan_results = int((conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM task_results r
            LEFT JOIN task_assignments a ON a.task_id = r.task_id AND a.helper_peer_id = r.helper_peer_id
            WHERE a.assignment_id IS NULL
            """
        ).fetchone() or {"cnt": 0})["cnt"])
        orphan_claims = int((conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM task_claims c
            LEFT JOIN task_offers o ON o.task_id = c.task_id
            WHERE o.task_id IS NULL
            """
        ).fetchone() or {"cnt": 0})["cnt"])
    finally:
        conn.close()
    stuck = [dict(row) for row in latest_rows if str(row["to_state"]) in {"offered", "claimed", "assigned", "running"}]
    status = "warn" if stuck or orphan_results or orphan_claims else "pass"
    return AuditSection(
        name="task_state",
        status=status,
        summary="Task state scan completed.",
        details={
            "active_or_stuck_rows": stuck[:50],
            "active_or_stuck_count": len(stuck),
            "orphan_results": orphan_results,
            "orphan_claims": orphan_claims,
        },
    )


def _knowledge_state_section() -> AuditSection:
    now_iso = _utcnow()
    presence = active_presence(limit=4096)
    manifests = all_manifests(limit=4096)
    holders = all_holders(limit=4096)
    impossible_replication = []
    counts: dict[str, int] = {}
    for holder in holders:
        counts[holder["shard_id"]] = counts.get(holder["shard_id"], 0) + 1
    for manifest in manifests:
        shard_id = str(manifest["shard_id"])
        if counts.get(shard_id, 0) < 0:
            impossible_replication.append(shard_id)
    stale_active_holders = [
        holder for holder in holders if str(holder.get("expires_at") or "") < now_iso and str(holder.get("status")) == "active"
    ]
    status = "warn" if stale_active_holders or impossible_replication else "pass"
    return AuditSection(
        name="knowledge_state",
        status=status,
        summary="Knowledge presence and holder scan completed.",
        details={
            "active_presence_count": len(presence),
            "manifest_count": len(manifests),
            "holder_count": len(holders),
            "stale_active_holders": stale_active_holders[:50],
            "impossible_replication": impossible_replication[:50],
        },
    )


def _event_chain_section() -> AuditSection:
    ok = verify_chain()
    conn = get_connection()
    try:
        count = int((conn.execute("SELECT COUNT(*) AS cnt FROM event_hash_chain").fetchone() or {"cnt": 0})["cnt"])
    finally:
        conn.close()
    return AuditSection(
        name="event_chain",
        status="pass" if ok else "fail",
        summary="Event hash chain verification completed.",
        details={"verified": ok, "entry_count": count},
    )


def _candidate_lane_section() -> AuditSection:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_candidates,
                SUM(CASE WHEN promotion_state != 'candidate' THEN 1 ELSE 0 END) AS promoted_like_rows,
                SUM(CASE WHEN invalidated_at IS NOT NULL THEN 1 ELSE 0 END) AS invalidated_rows
            FROM candidate_knowledge_lane
            """
        ).fetchone()
        learning_rows = conn.execute(
            """
            SELECT source_type, COUNT(*) AS cnt
            FROM learning_shards
            GROUP BY source_type
            """
        ).fetchall()
    finally:
        conn.close()
    promoted_like = int((row or {"promoted_like_rows": 0})["promoted_like_rows"] or 0)
    status = "pass" if promoted_like == 0 else "warn"
    return AuditSection(
        name="candidate_lane",
        status=status,
        summary="Candidate-versus-canonical boundary scan completed.",
        details={
            "total_candidates": int((row or {"total_candidates": 0})["total_candidates"] or 0),
            "promoted_like_rows": promoted_like,
            "invalidated_rows": int((row or {"invalidated_rows": 0})["invalidated_rows"] or 0),
            "learning_by_source_type": {str(item["source_type"]): int(item["cnt"]) for item in learning_rows},
        },
    )


def _context_usage_section() -> AuditSection:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT retrieval_confidence, swarm_metadata_consulted, cold_archive_opened,
                   bootstrap_tokens_used, relevant_tokens_used, cold_tokens_used
            FROM context_access_log
            ORDER BY created_at DESC
            LIMIT 500
            """
        ).fetchall()
    finally:
        conn.close()
    cold_open_count = sum(int(row["cold_archive_opened"] or 0) for row in rows)
    status = "warn" if rows and cold_open_count > max(3, len(rows) // 2) else "pass"
    return AuditSection(
        name="context_usage",
        status=status,
        summary="Context budget usage scan completed.",
        details={
            "sample_count": len(rows),
            "cold_open_count": cold_open_count,
            "swarm_metadata_consulted_count": sum(int(row["swarm_metadata_consulted"] or 0) for row in rows),
            "retrieval_confidence_breakdown": {
                "high": sum(1 for row in rows if str(row["retrieval_confidence"]) == "high"),
                "medium": sum(1 for row in rows if str(row["retrieval_confidence"]) == "medium"),
                "low": sum(1 for row in rows if str(row["retrieval_confidence"]) == "low"),
            },
        },
    )


def build_morning_after_audit_report() -> dict[str, Any]:
    sections = [
        _process_survival_section(),
        _task_state_section(),
        _knowledge_state_section(),
        _event_chain_section(),
        _candidate_lane_section(),
        _context_usage_section(),
    ]
    status = "fail" if any(section.status == "fail" for section in sections) else ("warn" if any(section.status == "warn" for section in sections) else "pass")
    return {
        "generated_at": _utcnow(),
        "host": socket.gethostname(),
        "platform": f"{platform.system()} {platform.release()}",
        "status": status,
        "sections": [asdict(section) for section in sections],
    }


def render_morning_after_audit_report(report: dict[str, Any]) -> str:
    lines = [
        "NULLA MORNING-AFTER AUDIT",
        "",
        f"Generated: {report['generated_at']}",
        f"Host: {report['host']}",
        f"Platform: {report['platform']}",
        f"Status: {report['status'].upper()}",
        "",
        "Sections:",
    ]
    for section in report["sections"]:
        lines.append(f"- [{section['status'].upper()}] {section['name']}: {section['summary']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_morning_after_audit_report(build_morning_after_audit_report()))
