from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import policy_engine, runtime_paths
from storage.db import DEFAULT_DB_PATH, get_connection
from storage.migrations import run_migrations
from storage.useful_output_store import summarize_useful_outputs, sync_useful_outputs

CONTROL_DIRS = (
    "queue",
    "leases",
    "runs",
    "budgets",
    "approvals",
    "deadletters",
    "metrics",
    "policies",
    "schemas",
)

TEMPLATE_NAMES = (
    "research_worker",
    "liquefy_worker",
    "monitor_worker",
    "personal_assistant",
    "reviewer",
    "archivist",
    "router",
)

_PATH_KEY_RE = re.compile(r"(?:^|_)(?:path|paths|root|roots|dir|directory|file|target|destination)$", re.IGNORECASE)


def sync_control_plane_workspace(
    *,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
    queue_limit: int = 64,
    run_limit: int = 32,
    event_limit: int = 24,
) -> dict[str, Any]:
    target_root = Path(workspace_root or _workspace_root()).resolve()
    control_root = target_root / "control"
    templates_root = target_root / "templates"
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    useful_output_summary = sync_useful_outputs(db_target)

    for name in CONTROL_DIRS:
        (control_root / name).mkdir(parents=True, exist_ok=True)
    (control_root / "runs" / "sessions").mkdir(parents=True, exist_ok=True)
    (templates_root).mkdir(parents=True, exist_ok=True)

    conn = get_connection(db_target)
    try:
        open_offers = _load_open_task_offers(conn, limit=queue_limit)
        reviewer_lane = _load_reviewer_lane(conn, limit=queue_limit)
        archivist_lane = _load_archivist_lane(conn, limit=queue_limit)
        commons_queue = _load_commons_promotion_queue(conn, limit=queue_limit)
        active_assignments = _load_active_assignments(conn, limit=queue_limit)
        active_hive_claims = _load_active_hive_claims(conn, limit=queue_limit)
        runtime_sessions = _load_runtime_sessions(conn, limit=run_limit, event_limit=event_limit)
        runtime_checkpoints = _load_runtime_checkpoints(conn, limit=run_limit)
        task_results = _load_recent_task_results(conn, limit=run_limit)
        pending_actions = _load_pending_operator_actions(conn, limit=queue_limit)
        pending_runtime = _load_pending_runtime_checkpoints(conn, limit=queue_limit)
        failed_runs = _load_failed_runtime_sessions(conn, limit=run_limit)
        rejected_results = _load_rejected_results(conn, limit=run_limit)
        swarm_budget = _load_swarm_budget_summary(conn)
        public_hive_budget = _load_public_hive_budget_summary(conn)
        proof_of_useful_work = _load_proof_of_useful_work_summary(
            conn,
            limit=min(20, max(5, queue_limit // 2)),
            db_path=db_target,
        )
        adaptation_status = _load_adaptation_status(conn, db_path=db_target)
        adaptation_proof = _load_adaptation_proof_summary(conn, db_path=db_target)
    finally:
        conn.close()

    reviewer_policy = _reviewer_lane_policy()
    archivist_policy = _archivist_lane_policy()
    overview = {
        "generated_at": _utcnow(),
        "workspace_root": str(target_root),
        "open_task_count": len(open_offers),
        "active_assignment_count": len(active_assignments),
        "active_hive_claim_count": len(active_hive_claims),
        "runtime_session_count": len(runtime_sessions),
        "pending_approval_count": len(pending_actions) + len(pending_runtime),
        "failed_runtime_count": len(failed_runs),
        "review_pending_count": len(reviewer_lane.get("items") or []),
        "archive_candidate_count": len(archivist_lane.get("items") or []),
        "commons_candidate_count": len(commons_queue.get("items") or []),
        "commons_review_ready_count": sum(1 for item in commons_queue.get("items") or [] if str(item.get("status") or "") == "review_required"),
        "useful_outputs": useful_output_summary,
        "swarm_dispatch_budget_today": swarm_budget,
        "public_hive_budget_today": public_hive_budget,
        "proof_of_useful_work": proof_of_useful_work,
        "adaptation": adaptation_status,
        "adaptation_proof": adaptation_proof,
    }

    writes = 0
    writes += _write_json(control_root / "queue" / "open_task_offers.json", {"generated_at": _utcnow(), "items": open_offers})
    writes += _write_json(control_root / "queue" / "reviewer_lane.json", reviewer_lane)
    writes += _write_json(control_root / "queue" / "archivist_lane.json", archivist_lane)
    writes += _write_json(control_root / "queue" / "commons_promotion_queue.json", commons_queue)
    writes += _write_json(
        control_root / "queue" / "useful_outputs.json",
        {
            "generated_at": _utcnow(),
            "summary": useful_output_summary,
            "items": list_useful_outputs_for_workspace(db_target, limit=queue_limit),
        },
    )
    writes += _write_json(control_root / "leases" / "active_assignments.json", {"generated_at": _utcnow(), "items": active_assignments})
    writes += _write_json(control_root / "leases" / "active_hive_claims.json", {"generated_at": _utcnow(), "items": active_hive_claims})
    writes += _write_json(control_root / "runs" / "runtime_sessions.json", {"generated_at": _utcnow(), "items": runtime_sessions})
    writes += _write_json(control_root / "runs" / "runtime_checkpoints.json", {"generated_at": _utcnow(), "items": runtime_checkpoints})
    writes += _write_json(control_root / "runs" / "task_results_recent.json", {"generated_at": _utcnow(), "items": task_results})
    session_dir = control_root / "runs" / "sessions"
    expected_session_files: set[str] = set()
    for session in runtime_sessions:
        filename = f"{_safe_name(str(session.get('session_id') or 'session'))}.json"
        expected_session_files.add(filename)
        writes += _write_json(session_dir / filename, session)
    for stale in session_dir.glob("*.json"):
        if stale.name in expected_session_files:
            continue
        stale.unlink(missing_ok=True)
        writes += 1
    writes += _write_json(control_root / "budgets" / "swarm_dispatch_today.json", swarm_budget)
    writes += _write_json(control_root / "budgets" / "public_hive_quota_today.json", public_hive_budget)
    writes += _write_json(control_root / "metrics" / "proof_of_useful_work.json", proof_of_useful_work)
    writes += _write_json(control_root / "metrics" / "adaptation_proof.json", adaptation_proof)
    writes += _write_json(control_root / "approvals" / "pending_operator_actions.json", {"generated_at": _utcnow(), "items": pending_actions})
    writes += _write_json(control_root / "approvals" / "pending_runtime_checkpoints.json", {"generated_at": _utcnow(), "items": pending_runtime})
    writes += _write_json(control_root / "deadletters" / "failed_runtime_sessions.json", {"generated_at": _utcnow(), "items": failed_runs})
    writes += _write_json(control_root / "deadletters" / "rejected_or_harmful_results.json", {"generated_at": _utcnow(), "items": rejected_results})
    writes += _write_json(control_root / "metrics" / "overview.json", overview)
    writes += _write_json(control_root / "metrics" / "adaptation.json", adaptation_status)
    writes += _write_json(control_root / "policies" / "budget_caps.json", _budget_caps_policy())
    writes += _write_json(control_root / "policies" / "reviewer_lane.json", reviewer_policy)
    writes += _write_json(control_root / "policies" / "archivist_lane.json", archivist_policy)
    writes += _write_text(control_root / "policies" / "control_plane_policy.md", _control_plane_policy_text())

    for relative_path, payload in _schema_library().items():
        writes += _write_json(control_root / "schemas" / relative_path, payload)

    for template_name, files in _template_library().items():
        template_root = templates_root / template_name
        template_root.mkdir(parents=True, exist_ok=True)
        for filename, payload in files.items():
            target = template_root / filename
            if filename.endswith(".json"):
                writes += _write_json(target, payload)
            else:
                writes += _write_text(target, str(payload))

    return {
        "ok": True,
        "workspace_root": str(target_root),
        "control_root": str(control_root),
        "templates_root": str(templates_root),
        "writes": writes,
        "open_task_count": len(open_offers),
        "runtime_session_count": len(runtime_sessions),
        "pending_approval_count": len(pending_actions) + len(pending_runtime),
        "template_count": len(TEMPLATE_NAMES),
    }


def collect_control_plane_status(
    *,
    db_path: str | Path | None = None,
    queue_limit: int = 64,
    run_limit: int = 32,
    event_limit: int = 24,
) -> dict[str, Any]:
    db_target = db_path or DEFAULT_DB_PATH
    run_migrations(db_target)
    useful_output_summary = sync_useful_outputs(db_target)
    conn = get_connection(db_target)
    try:
        open_offers = _load_open_task_offers(conn, limit=queue_limit)
        active_assignments = _load_active_assignments(conn, limit=queue_limit)
        active_hive_claims = _load_active_hive_claims(conn, limit=queue_limit)
        runtime_sessions = _load_runtime_sessions(conn, limit=run_limit, event_limit=event_limit)
        pending_actions = _load_pending_operator_actions(conn, limit=queue_limit)
        pending_runtime = _load_pending_runtime_checkpoints(conn, limit=queue_limit)
        reviewer_lane = _load_reviewer_lane(conn, limit=queue_limit)
        archivist_lane = _load_archivist_lane(conn, limit=queue_limit)
        commons_queue = _load_commons_promotion_queue(conn, limit=queue_limit)
        swarm_budget = _load_swarm_budget_summary(conn)
        public_hive_budget = _load_public_hive_budget_summary(conn)
        proof_of_useful_work = _load_proof_of_useful_work_summary(
            conn,
            limit=min(20, max(5, queue_limit // 2)),
            db_path=db_target,
        )
        adaptation_status = _load_adaptation_status(conn, db_path=db_target)
        adaptation_proof = _load_adaptation_proof_summary(conn, db_path=db_target)
    finally:
        conn.close()
    return {
        "generated_at": _utcnow(),
        "open_task_count": len(open_offers),
        "active_assignment_count": len(active_assignments),
        "active_hive_claim_count": len(active_hive_claims),
        "runtime_session_count": len(runtime_sessions),
        "pending_approval_count": len(pending_actions) + len(pending_runtime),
        "review_pending_count": len(reviewer_lane.get("items") or []),
        "archive_candidate_count": len(archivist_lane.get("items") or []),
        "commons_candidate_count": len(commons_queue.get("items") or []),
        "commons_review_ready_count": sum(1 for item in commons_queue.get("items") or [] if str(item.get("status") or "") == "review_required"),
        "useful_outputs": useful_output_summary,
        "swarm_dispatch_budget_today": swarm_budget,
        "public_hive_budget_today": public_hive_budget,
        "proof_of_useful_work": proof_of_useful_work,
        "adaptation": adaptation_status,
        "adaptation_proof": adaptation_proof,
    }


def _workspace_root() -> Path:
    workspace_getter = getattr(runtime_paths, "workspace_path", None)
    if callable(workspace_getter):
        return Path(workspace_getter()).resolve()
    return runtime_paths.project_path("workspace")


def _load_open_task_offers(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "task_offers"):
        return []
    rows = conn.execute(
        """
        SELECT task_id, parent_peer_id, task_type, subtask_type, summary, priority, deadline_ts, status, created_at, updated_at
        FROM task_offers
        WHERE status IN ('open', 'claimed', 'assigned')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_reviewer_lane(conn: Any, *, limit: int) -> dict[str, Any]:
    if not _table_exists(conn, "task_results"):
        return {"generated_at": _utcnow(), "lane": "reviewer", "items": []}
    rows = conn.execute(
        """
        SELECT result_id, task_id, helper_peer_id, result_type, summary, confidence, status, created_at, updated_at
        FROM task_results
        WHERE status = 'submitted'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return {
        "generated_at": _utcnow(),
        "lane": "reviewer",
        "review_required": True,
        "items": [dict(row) for row in rows],
    }


def _load_commons_promotion_queue(conn: Any, *, limit: int) -> dict[str, Any]:
    if not _table_exists(conn, "hive_commons_promotion_candidates"):
        return {"generated_at": _utcnow(), "lane": "commons_promotion", "items": []}
    rows = conn.execute(
        """
        SELECT candidate_id, post_id, topic_id, requested_by_agent_id, score, status, review_state,
               archive_state, promoted_topic_id, support_weight, challenge_weight, cite_weight,
               comment_count, evidence_depth, downstream_use_count, training_signal_count,
               reasons_json, metadata_json, created_at, updated_at
        FROM hive_commons_promotion_candidates
        ORDER BY score DESC, updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["reasons"] = _json_loads(item.pop("reasons_json", "[]"), fallback=[])
        item["metadata"] = _json_loads(item.pop("metadata_json", "{}"), fallback={})
        items.append(item)
    return {
        "generated_at": _utcnow(),
        "lane": "commons_promotion",
        "review_required": True,
        "items": items,
    }


def _load_archivist_lane(conn: Any, *, limit: int) -> dict[str, Any]:
    if _table_exists(conn, "useful_outputs"):
        rows = conn.execute(
            """
            SELECT useful_output_id, source_type, source_id, task_id, topic_id, summary,
                   quality_score, archive_state, eligibility_state, source_updated_at
            FROM useful_outputs
            WHERE archive_state IN ('candidate', 'approved')
            ORDER BY quality_score DESC, source_updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return {
            "generated_at": _utcnow(),
            "lane": "archivist",
            "review_required": False,
            "archive_mode": "approved_summaries_only",
            "items": [dict(row) for row in rows],
        }
    if not _table_exists(conn, "task_results"):
        return {"generated_at": _utcnow(), "lane": "archivist", "items": []}
    rows = conn.execute(
        """
        SELECT result_id, task_id, helper_peer_id, result_type, summary, confidence, status, created_at, updated_at
        FROM task_results
        WHERE status IN ('accepted', 'partial', 'reviewed')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return {
        "generated_at": _utcnow(),
        "lane": "archivist",
        "review_required": False,
        "archive_mode": "approved_summaries_only",
        "items": [dict(row) for row in rows],
    }


def _load_active_assignments(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "task_assignments"):
        return []
    rows = conn.execute(
        """
        SELECT assignment_id, task_id, claim_id, parent_peer_id, helper_peer_id, assignment_mode,
               status, capability_token_id, lease_expires_at, last_progress_state, last_progress_note,
               assigned_at, updated_at, progress_updated_at, completed_at
        FROM task_assignments
        WHERE status = 'active'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_active_hive_claims(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "hive_topic_claims"):
        return []
    rows = conn.execute(
        """
        SELECT claim_id, topic_id, agent_id, status, note, capability_tags_json, created_at, updated_at
        FROM hive_topic_claims
        WHERE status = 'active'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["capability_tags"] = _json_loads(item.pop("capability_tags_json", "[]"), fallback=[])
        out.append(item)
    return out


def _load_runtime_sessions(conn: Any, *, limit: int, event_limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "runtime_sessions"):
        return []
    rows = conn.execute(
        """
        SELECT session_id, started_at, updated_at, event_count, last_event_type, last_message,
               request_preview, task_class, status, last_checkpoint_id
        FROM runtime_sessions
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        checkpoint_id = str(item.get("last_checkpoint_id") or "").strip()
        item["checkpoint"] = _runtime_checkpoint(conn, checkpoint_id) if checkpoint_id else None
        item["recent_events"] = _runtime_events(conn, str(item["session_id"]), limit=event_limit)
        receipts = _runtime_receipts(conn, str(item["session_id"]), limit=12)
        item["tool_receipts"] = receipts
        item["touched_paths"] = sorted({path for receipt in receipts for path in _paths_from_payload(receipt)})
        out.append(item)
    return out


def _load_runtime_checkpoints(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "runtime_checkpoints"):
        return []
    rows = conn.execute(
        """
        SELECT checkpoint_id, session_id, task_id, task_class, status, step_count, last_tool_name,
               final_response, failure_text, resume_count, created_at, updated_at, completed_at
        FROM runtime_checkpoints
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_recent_task_results(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "task_results"):
        return []
    rows = conn.execute(
        """
        SELECT result_id, task_id, helper_peer_id, result_type, summary, confidence,
               evidence_json, abstract_steps_json, risk_flags_json, status, created_at, updated_at
        FROM task_results
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["evidence"] = _json_loads(item.pop("evidence_json", "[]"), fallback=[])
        item["abstract_steps"] = _json_loads(item.pop("abstract_steps_json", "[]"), fallback=[])
        item["risk_flags"] = _json_loads(item.pop("risk_flags_json", "[]"), fallback=[])
        out.append(item)
    return out


def _load_pending_operator_actions(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "operator_action_requests"):
        return []
    rows = conn.execute(
        """
        SELECT action_id, session_id, task_id, action_kind, scope_json, status, created_at, updated_at
        FROM operator_action_requests
        WHERE status = 'pending_approval'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["scope"] = _json_loads(item.pop("scope_json", "{}"), fallback={})
        out.append(item)
    return out


def _load_pending_runtime_checkpoints(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "runtime_checkpoints"):
        return []
    rows = conn.execute(
        """
        SELECT checkpoint_id, session_id, task_id, task_class, status, step_count, last_tool_name,
               created_at, updated_at
        FROM runtime_checkpoints
        WHERE status = 'pending_approval'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_failed_runtime_sessions(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "runtime_sessions"):
        return []
    rows = conn.execute(
        """
        SELECT session_id, started_at, updated_at, event_count, last_event_type,
               last_message, request_preview, task_class, status, last_checkpoint_id
        FROM runtime_sessions
        WHERE status IN ('failed', 'interrupted')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_rejected_results(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "task_results"):
        return []
    rows = conn.execute(
        """
        SELECT result_id, task_id, helper_peer_id, summary, confidence, status, created_at, updated_at
        FROM task_results
        WHERE status IN ('rejected', 'harmful', 'failed')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_swarm_budget_summary(conn: Any) -> dict[str, Any]:
    day_bucket = _utc_day_bucket()
    items: list[dict[str, Any]] = []
    if _table_exists(conn, "swarm_dispatch_budget_events"):
        rows = conn.execute(
            """
            SELECT peer_id, day_bucket, dispatch_mode, reason, SUM(amount) AS amount, COUNT(*) AS event_count
            FROM swarm_dispatch_budget_events
            WHERE day_bucket = ?
            GROUP BY peer_id, day_bucket, dispatch_mode, reason
            ORDER BY amount DESC
            """,
            (day_bucket,),
        ).fetchall()
        items = [dict(row) for row in rows]
    used_total = round(sum(float(item.get("amount") or 0.0) for item in items), 4)
    daily_cap = float(policy_engine.get("economics.free_tier_daily_swarm_points", 24.0) or 24.0)
    return {
        "generated_at": _utcnow(),
        "day_bucket": day_bucket,
        "free_tier_daily_swarm_points": daily_cap,
        "free_tier_max_dispatch_points": float(policy_engine.get("economics.free_tier_max_dispatch_points", 12.0) or 12.0),
        "used_total": used_total,
        "remaining_estimated": round(max(0.0, daily_cap - used_total), 4),
        "items": items,
    }


def _load_public_hive_budget_summary(conn: Any) -> dict[str, Any]:
    day_bucket = _utc_day_bucket()
    items: list[dict[str, Any]] = []
    if _table_exists(conn, "public_hive_write_quota_events"):
        rows = conn.execute(
            """
            SELECT peer_id, day_bucket, route, MAX(trust_score) AS trust_score, MAX(trust_tier) AS trust_tier,
                   SUM(amount) AS amount, COUNT(*) AS event_count
            FROM public_hive_write_quota_events
            WHERE day_bucket = ?
            GROUP BY peer_id, day_bucket, route
            ORDER BY amount DESC
            """,
            (day_bucket,),
        ).fetchall()
        items = [dict(row) for row in rows]
    active_claim_count = 0
    if _table_exists(conn, "hive_topic_claims"):
        row = conn.execute("SELECT COUNT(*) AS cnt FROM hive_topic_claims WHERE status = 'active'").fetchone()
        active_claim_count = int((row["cnt"] if row else 0) or 0)
    used_total = round(sum(float(item.get("amount") or 0.0) for item in items), 4)
    trust_tier = str(items[0].get("trust_tier") or "low") if items else "low"
    quota_low = float(policy_engine.get("economics.public_hive_daily_quota_low", 24.0) or 24.0)
    quota_mid = float(policy_engine.get("economics.public_hive_daily_quota_mid", 192.0) or 192.0)
    quota_high = float(policy_engine.get("economics.public_hive_daily_quota_high", 768.0) or 768.0)
    bonus_per_claim = float(
        policy_engine.get("economics.public_hive_daily_quota_bonus_per_active_claim", 24.0) or 24.0
    )
    bonus_cap = float(
        policy_engine.get("economics.public_hive_daily_quota_max_active_claim_bonus", 192.0) or 192.0
    )
    base_quota = quota_mid if trust_tier == "established" else quota_high if trust_tier == "trusted" else quota_low
    active_claim_bonus = min(bonus_cap, active_claim_count * bonus_per_claim)
    estimated_daily_quota = round(base_quota + active_claim_bonus, 4)
    return {
        "generated_at": _utcnow(),
        "day_bucket": day_bucket,
        "daily_quota_low": quota_low,
        "daily_quota_mid": quota_mid,
        "daily_quota_high": quota_high,
        "active_claim_bonus_per_claim": bonus_per_claim,
        "active_claim_bonus_cap": bonus_cap,
        "active_claim_count": active_claim_count,
        "used_total": used_total,
        "trust_tier": trust_tier,
        "estimated_daily_quota": estimated_daily_quota,
        "remaining_estimated": round(max(0.0, estimated_daily_quota - used_total), 4),
        "route_costs": dict(policy_engine.get("economics.public_hive_route_costs", {}) or {}),
        "items": items,
    }


def _load_adaptation_status(conn: Any, *, db_path: str | Path | None = None) -> dict[str, Any]:
    loop_state = {}
    if _table_exists(conn, "adaptation_loop_state"):
        row = conn.execute(
            """
            SELECT loop_name, status, base_model_ref, base_provider_name, base_model_name,
                   active_job_id, active_provider_name, active_model_name,
                   previous_job_id, previous_provider_name, previous_model_name,
                   last_corpus_id, last_example_count, last_quality_score, last_eval_id,
                   last_canary_eval_id, last_decision, last_reason, last_error_text,
                   last_tick_at, last_completed_tick_at, last_metadata_publish_at, metrics_json
            FROM adaptation_loop_state
            WHERE loop_name = 'default'
            LIMIT 1
            """
        ).fetchone()
        if row:
            loop_state = dict(row)
            loop_state["metrics"] = _json_loads(loop_state.pop("metrics_json", "{}"), fallback={})
    try:
        from core.trainable_base_manager import list_staged_trainable_bases

        staged_bases = list_staged_trainable_bases()
    except Exception:
        staged_bases = []
    try:
        from storage.adaptation_store import list_adaptation_eval_runs

        recent_evals = list_adaptation_eval_runs(limit=8)
    except Exception:
        recent_evals = []
    return {
        "generated_at": _utcnow(),
        "loop_state": loop_state,
        "staged_bases": staged_bases,
        "recent_evals": recent_evals,
        "useful_outputs": summarize_useful_outputs(str(db_path) if db_path is not None else None),
    }


def _load_proof_of_useful_work_summary(conn: Any, *, limit: int, db_path: str | Path | None = None) -> dict[str, Any]:
    if not _table_exists(conn, "contribution_ledger"):
        return {
            "generated_at": _utcnow(),
            "pending_count": 0,
            "confirmed_count": 0,
            "finalized_count": 0,
            "rejected_count": 0,
            "slashed_count": 0,
            "released_compute_credits": 0.0,
            "finalized_compute_credits": 0.0,
            "leaders": [],
        }

    finality_state = """
    CASE
        WHEN finality_state IS NOT NULL AND TRIM(finality_state) != '' THEN LOWER(finality_state)
        WHEN outcome = 'pending' THEN 'pending'
        WHEN outcome = 'released' THEN 'confirmed'
        WHEN outcome = 'slashed' THEN 'slashed'
        WHEN outcome IN ('rejected', 'harmful', 'failed') THEN 'rejected'
        ELSE 'pending'
    END
    """
    row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN {finality_state} = 'pending' THEN 1 ELSE 0 END) AS pending_count,
            SUM(CASE WHEN {finality_state} = 'confirmed' THEN 1 ELSE 0 END) AS confirmed_count,
            SUM(CASE WHEN {finality_state} = 'finalized' THEN 1 ELSE 0 END) AS finalized_count,
            SUM(CASE WHEN {finality_state} = 'rejected' THEN 1 ELSE 0 END) AS rejected_count,
            SUM(CASE WHEN {finality_state} = 'slashed' THEN 1 ELSE 0 END) AS slashed_count,
            COALESCE(SUM(compute_credits_released), 0) AS released_compute_credits,
            COALESCE(SUM(CASE WHEN {finality_state} = 'finalized' THEN compute_credits_released ELSE 0 END), 0) AS finalized_compute_credits
        FROM contribution_ledger
        """
    ).fetchone()

    try:
        from core.contribution_proof import list_contribution_proof_receipts
        from core.scoreboard_engine import get_glory_leaderboard

        leaders = get_glory_leaderboard(limit=max(1, int(limit)), db_path=db_path)
        recent_receipts = list_contribution_proof_receipts(limit=max(1, int(limit)), db_path=db_path)
        challenged_receipts = list_contribution_proof_receipts(
            limit=max(1, min(8, int(limit))),
            stages=["slashed", "rejected"],
            db_path=db_path,
        )
    except Exception:
        leaders = []
        recent_receipts = []
        challenged_receipts = []

    return {
        "generated_at": _utcnow(),
        "pending_count": int((row["pending_count"] if row else 0) or 0),
        "confirmed_count": int((row["confirmed_count"] if row else 0) or 0),
        "finalized_count": int((row["finalized_count"] if row else 0) or 0),
        "rejected_count": int((row["rejected_count"] if row else 0) or 0),
        "slashed_count": int((row["slashed_count"] if row else 0) or 0),
        "released_compute_credits": round(float((row["released_compute_credits"] if row else 0.0) or 0.0), 4),
        "finalized_compute_credits": round(float((row["finalized_compute_credits"] if row else 0.0) or 0.0), 4),
        "leaders": leaders,
        "recent_receipts": recent_receipts,
        "challenged_receipts": challenged_receipts,
    }


def _load_adaptation_proof_summary(conn: Any, *, db_path: str | Path | None = None) -> dict[str, Any]:
    evals: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    if _table_exists(conn, "adaptation_eval_runs"):
        rows = conn.execute(
            """
            SELECT *
            FROM adaptation_eval_runs
            ORDER BY updated_at DESC
            LIMIT 32
            """
        ).fetchall()
        for row in rows:
            item = dict(row)
            item["metrics"] = _json_loads(item.pop("metrics_json", "{}"), fallback={})
            evals.append(item)
    if _table_exists(conn, "adaptation_jobs"):
        rows = conn.execute(
            """
            SELECT *
            FROM adaptation_jobs
            ORDER BY updated_at DESC
            LIMIT 24
            """
        ).fetchall()
        for row in rows:
            item = dict(row)
            item["dependency_status"] = _json_loads(item.pop("dependency_status_json", "{}"), fallback={})
            item["training_config"] = _json_loads(item.pop("training_config_json", "{}"), fallback={})
            item["metrics"] = _json_loads(item.pop("metrics_json", "{}"), fallback={})
            item["metadata"] = _json_loads(item.pop("metadata_json", "{}"), fallback={})
            item["registered_manifest"] = _json_loads(item.pop("registered_manifest_json", "{}"), fallback={})
            jobs.append(item)

    completed_evals = [row for row in evals if str(row.get("status") or "").strip().lower() == "completed"]
    promotion_evals = [row for row in completed_evals if str(row.get("eval_kind") or "") == "promotion_gate"]
    pre_canaries = [row for row in completed_evals if str(row.get("eval_kind") or "") == "pre_promotion_canary"]
    post_canaries = [row for row in completed_evals if str(row.get("eval_kind") or "") == "post_promotion_canary"]
    promoted_jobs = [row for row in jobs if str(row.get("promoted_at") or "").strip()]
    rolled_back_jobs = [row for row in jobs if str(row.get("rolled_back_at") or "").strip()]
    active_promoted = next(
        (
            row
            for row in jobs
            if str(row.get("status") or "").strip().lower() == "promoted"
            and not str(row.get("rolled_back_at") or "").strip()
        ),
        {},
    )
    latest_eval = dict(completed_evals[0] or {}) if completed_evals else {}
    latest_promotion_eval = dict(promotion_evals[0] or {}) if promotion_evals else {}
    latest_canary = dict((post_canaries or pre_canaries or [None])[0] or {})
    positive_eval_count = sum(1 for row in completed_evals if float(row.get("score_delta") or 0.0) > 0.0)
    negative_eval_count = sum(1 for row in completed_evals if float(row.get("score_delta") or 0.0) < 0.0)
    mean_delta = round(
        sum(float(row.get("score_delta") or 0.0) for row in completed_evals) / max(1, len(completed_evals)),
        4,
    ) if completed_evals else 0.0
    proof_state = "no_recent_eval"
    if rolled_back_jobs:
        proof_state = "rollback_recorded"
    elif latest_promotion_eval and str(latest_promotion_eval.get("decision") or "") == "promote_candidate":
        if latest_canary and str(latest_canary.get("decision") or "") in {"canary_pass", "keep_live"}:
            proof_state = "candidate_beating_baseline"
        else:
            proof_state = "candidate_unproven_after_eval"
    elif latest_eval:
        proof_state = "positive_eval_signal" if float(latest_eval.get("score_delta") or 0.0) > 0.0 else "flat_or_negative_eval_signal"

    return {
        "generated_at": _utcnow(),
        "proof_state": proof_state,
        "recent_eval_count": len(completed_evals),
        "positive_eval_count": positive_eval_count,
        "negative_eval_count": negative_eval_count,
        "mean_delta": mean_delta,
        "promoted_job_count": len(promoted_jobs),
        "rolled_back_job_count": len(rolled_back_jobs),
        "latest_eval": latest_eval,
        "latest_promotion_eval": latest_promotion_eval,
        "latest_canary": latest_canary,
        "active_promoted_job": active_promoted,
        "promotion_history": [
            {
                "job_id": str(row.get("job_id") or ""),
                "label": str(row.get("label") or ""),
                "status": str(row.get("status") or ""),
                "promoted_at": str(row.get("promoted_at") or ""),
                "rolled_back_at": str(row.get("rolled_back_at") or ""),
                "quality_score": float((row.get("metadata") or {}).get("quality_score") or 0.0),
                "adapter_provider_name": str(row.get("adapter_provider_name") or ""),
                "adapter_model_name": str(row.get("adapter_model_name") or ""),
            }
            for row in promoted_jobs[:6]
        ],
    }


def list_useful_outputs_for_workspace(db_path: str | Path | None = None, *, limit: int = 64) -> list[dict[str, Any]]:
    rows = []
    conn = get_connection(db_path or DEFAULT_DB_PATH)
    try:
        if _table_exists(conn, "useful_outputs"):
            fetched = conn.execute(
                """
                SELECT useful_output_id, source_type, source_id, task_id, topic_id, summary,
                       quality_score, archive_state, eligibility_state, durability_reasons_json,
                       eligibility_reasons_json, source_updated_at
                FROM useful_outputs
                ORDER BY quality_score DESC, source_updated_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            rows = [dict(row) for row in fetched]
    finally:
        conn.close()
    for item in rows:
        item["durability_reasons"] = _json_loads(item.pop("durability_reasons_json", "[]"), fallback=[])
        item["eligibility_reasons"] = _json_loads(item.pop("eligibility_reasons_json", "[]"), fallback=[])
    return rows


def _runtime_checkpoint(conn: Any, checkpoint_id: str) -> dict[str, Any] | None:
    if not checkpoint_id or not _table_exists(conn, "runtime_checkpoints"):
        return None
    row = conn.execute(
        """
        SELECT checkpoint_id, session_id, task_id, task_class, status, step_count, last_tool_name,
               final_response, failure_text, resume_count, created_at, updated_at, completed_at
        FROM runtime_checkpoints
        WHERE checkpoint_id = ?
        LIMIT 1
        """,
        (checkpoint_id,),
    ).fetchone()
    return dict(row) if row else None


def _runtime_events(conn: Any, session_id: str, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "runtime_session_events"):
        return []
    rows = conn.execute(
        """
        SELECT session_id, seq, event_type, message, details_json, created_at
        FROM runtime_session_events
        WHERE session_id = ?
        ORDER BY seq DESC
        LIMIT ?
        """,
        (session_id, max(1, int(limit))),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows[::-1]:
        item = dict(row)
        item["details"] = _json_loads(item.pop("details_json", "{}"), fallback={})
        events.append(item)
    return events


def _runtime_receipts(conn: Any, session_id: str, *, limit: int) -> list[dict[str, Any]]:
    if not _table_exists(conn, "runtime_tool_receipts"):
        return []
    rows = conn.execute(
        """
        SELECT receipt_key, session_id, checkpoint_id, tool_name, idempotency_key,
               arguments_json, execution_json, created_at, updated_at
        FROM runtime_tool_receipts
        WHERE session_id = ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (session_id, max(1, int(limit))),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["arguments"] = _json_loads(item.pop("arguments_json", "{}"), fallback={})
        item["execution"] = _json_loads(item.pop("execution_json", "{}"), fallback={})
        out.append(item)
    return out


def _budget_caps_policy() -> dict[str, Any]:
    return {
        "generated_at": _utcnow(),
        "swarm_dispatch": {
            "free_tier_daily_swarm_points": float(policy_engine.get("economics.free_tier_daily_swarm_points", 24.0) or 24.0),
            "free_tier_max_dispatch_points": float(policy_engine.get("economics.free_tier_max_dispatch_points", 12.0) or 12.0),
        },
        "public_hive": {
            "daily_quota_low": float(policy_engine.get("economics.public_hive_daily_quota_low", 24.0) or 24.0),
            "daily_quota_mid": float(policy_engine.get("economics.public_hive_daily_quota_mid", 192.0) or 192.0),
            "daily_quota_high": float(policy_engine.get("economics.public_hive_daily_quota_high", 768.0) or 768.0),
            "bonus_per_active_claim": float(
                policy_engine.get("economics.public_hive_daily_quota_bonus_per_active_claim", 24.0) or 24.0
            ),
            "bonus_cap": float(
                policy_engine.get("economics.public_hive_daily_quota_max_active_claim_bonus", 192.0) or 192.0
            ),
            "route_costs": dict(policy_engine.get("economics.public_hive_route_costs", {}) or {}),
        },
        "adaptation": {
            "tick_interval_seconds": int(policy_engine.get("adaptation.tick_interval_seconds", 1800) or 1800),
            "max_running_jobs": int(policy_engine.get("adaptation.max_running_jobs", 1) or 1),
            "min_examples_to_train": int(policy_engine.get("adaptation.min_examples_to_train", 24) or 24),
            "min_structured_examples": int(policy_engine.get("adaptation.min_structured_examples", 12) or 12),
            "min_high_signal_examples": int(policy_engine.get("adaptation.min_high_signal_examples", 8) or 8),
            "min_new_examples_since_last_job": int(
                policy_engine.get("adaptation.min_new_examples_since_last_job", 8) or 8
            ),
            "max_conversation_ratio": float(policy_engine.get("adaptation.max_conversation_ratio", 0.45) or 0.45),
            "promotion_margin": float(policy_engine.get("adaptation.promotion_margin", 0.03) or 0.03),
            "rollback_margin": float(policy_engine.get("adaptation.rollback_margin", 0.04) or 0.04),
        },
    }


def _reviewer_lane_policy() -> dict[str, Any]:
    return {
        "lane": "reviewer",
        "purpose": "Validate schema, policy compliance, and output quality before durable promotion.",
        "source_of_truth": "task_results.status = submitted; useful_outputs stay ineligible until reviewed or approved",
        "required_checks": [
            "schema_valid",
            "policy_compliant",
            "unexpected_write_behavior_absent",
            "output_complete_or_explicitly_partial",
            "human_approval_required_if_risky",
            "training_eligibility_explicit",
        ],
    }


def _archivist_lane_policy() -> dict[str, Any]:
    return {
        "lane": "archivist",
        "purpose": "Compact approved outputs into durable summaries without polluting memory with transient noise.",
        "source_of_truth": "useful_outputs.archive_state in candidate|approved",
        "archive_rules": [
            "approved_summaries_only",
            "strip transient chatter",
            "preserve task/result IDs for traceability",
            "prefer role-scoped or shared semantic memory, not raw chat dumps",
        ],
    }


def _control_plane_policy_text() -> str:
    return (
        "# NULLA Control Plane Mirror\n\n"
        "This workspace is an operator-facing mirror of NULLA's real runtime state.\n"
        "It does not replace the DB-backed source of truth.\n\n"
        "Safety posture:\n"
        "- additive only\n"
        "- read-mostly mirrors\n"
        "- review before archive\n"
        "- approvals stay explicit\n"
        "- failed or policy-rejected work stays visible in deadletters\n"
    )


def _schema_library() -> dict[str, dict[str, Any]]:
    return {
        "task-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["task_id", "summary", "status", "created_at", "updated_at"],
            "properties": {
                "task_id": {"type": "string"},
                "summary": {"type": "string"},
                "status": {"type": "string"},
                "priority": {"type": "string"},
                "created_at": {"type": "string"},
                "updated_at": {"type": "string"},
            },
        },
        "lease-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["assignment_id", "task_id", "helper_peer_id", "status", "assigned_at"],
            "properties": {
                "assignment_id": {"type": "string"},
                "task_id": {"type": "string"},
                "helper_peer_id": {"type": "string"},
                "status": {"type": "string"},
                "lease_expires_at": {"type": ["string", "null"]},
            },
        },
        "run-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["session_id", "status", "request_preview", "started_at", "updated_at"],
            "properties": {
                "session_id": {"type": "string"},
                "status": {"type": "string"},
                "request_preview": {"type": "string"},
                "task_class": {"type": "string"},
                "tool_receipts": {"type": "array"},
                "touched_paths": {"type": "array", "items": {"type": "string"}},
            },
        },
        "approval-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["action_id", "session_id", "action_kind", "status", "created_at"],
            "properties": {
                "action_id": {"type": "string"},
                "session_id": {"type": "string"},
                "action_kind": {"type": "string"},
                "status": {"type": "string"},
                "scope": {"type": "object"},
            },
        },
        "budget-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["generated_at", "day_bucket", "items"],
            "properties": {
                "generated_at": {"type": "string"},
                "day_bucket": {"type": "string"},
                "items": {"type": "array"},
            },
        },
        "review-manifest.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["lane", "items"],
            "properties": {
                "lane": {"type": "string"},
                "items": {"type": "array"},
                "review_required": {"type": "boolean"},
            },
        },
    }


def _template_library() -> dict[str, dict[str, Any]]:
    common = {
        "research_worker": {
            "AGENT_ROLE.md": (
                "# Research Worker\n\n"
                "Purpose: bounded reading, synthesis, and evidence gathering.\n"
                "Must not mutate system state or widen permissions.\n"
            ),
            "TOOLS.md": (
                "- read-only file inspection\n"
                "- bounded web/retrieval when policy allows\n"
                "- no broad shell writes\n"
            ),
            "HEARTBEAT.md": "- confirm queue/task scope\n- confirm read-only posture\n- stop on ambiguity\n",
            "POLICY.md": "Default deny. Read-mostly. Human approval on any write or risky network action.\n",
            "spawn.json": _spawn_policy(
                purpose="bounded_research",
                allowed_tools=["read", "search", "summarize"],
                allowed_read_roots=["./workspace", "./docs", "./data"],
                allowed_write_roots=["./workspace/logs"],
                shell_allowed=False,
                network_allowed=True,
                credential_use=False,
                max_steps=12,
                max_lifetime_seconds=900,
                max_retries=1,
                max_requests_per_minute=12,
                review_required=True,
                archive_behavior="review_then_archive_summary",
            ),
        },
        "liquefy_worker": {
            "AGENT_ROLE.md": (
                "# Liquefy Worker\n\n"
                "Purpose: approved project work, compression/integration support, bounded file operations.\n"
                "Must stay inside approved project/workspace roots.\n"
            ),
            "TOOLS.md": "- bounded shell\n- git status/diff/show\n- python/node project tools within approved roots\n",
            "HEARTBEAT.md": "- verify path scope\n- verify no destructive command\n- stop for escalation\n",
            "POLICY.md": "Broader than research but still path-scoped and approval-gated for destructive or external changes.\n",
            "spawn.json": _spawn_policy(
                purpose="project_implementation",
                allowed_tools=["read", "write", "edit", "exec", "diff"],
                allowed_read_roots=["./workspace", "./docs", "./data", "./core", "./apps", "./storage", "./ops", "./tests"],
                allowed_write_roots=["./workspace", "./tmp", "./core", "./apps", "./storage", "./ops", "./tests"],
                shell_allowed=True,
                network_allowed=False,
                credential_use=False,
                max_steps=24,
                max_lifetime_seconds=1800,
                max_retries=1,
                max_requests_per_minute=24,
                review_required=True,
                archive_behavior="review_then_archive_summary",
            ),
        },
        "monitor_worker": {
            "AGENT_ROLE.md": "# Monitor Worker\n\nPurpose: inspect system health, logs, runtime status, and anomaly summaries.\n",
            "TOOLS.md": "- read-only diagnostics\n- log/status inspection\n- no state changes\n",
            "HEARTBEAT.md": "- verify read-only mode\n- capture failures with timestamps\n- no mutating actions\n",
            "POLICY.md": "Read-heavy and conservative. Never change system state without explicit approval.\n",
            "spawn.json": _spawn_policy(
                purpose="health_monitoring",
                allowed_tools=["read", "status", "inspect"],
                allowed_read_roots=["./workspace", "./data", "./logs", "./config"],
                allowed_write_roots=["./workspace/logs"],
                shell_allowed=False,
                network_allowed=False,
                credential_use=False,
                max_steps=10,
                max_lifetime_seconds=600,
                max_retries=1,
                max_requests_per_minute=10,
                review_required=False,
                archive_behavior="log_summary_only",
            ),
        },
        "personal_assistant": {
            "AGENT_ROLE.md": "# Personal Assistant\n\nPurpose: general user-facing help, notes, and bounded personal task organization.\n",
            "TOOLS.md": "- low-risk note and summary tools\n- no coding or infra powers by default\n",
            "HEARTBEAT.md": "- keep tone/user context clean\n- avoid coding/infra privileges\n",
            "POLICY.md": "No broad shell, no infra mutation, no credential use by default.\n",
            "spawn.json": _spawn_policy(
                purpose="general_assistance",
                allowed_tools=["read", "write_notes", "summarize"],
                allowed_read_roots=["./workspace", "./docs"],
                allowed_write_roots=["./workspace/logs", "./workspace/memory"],
                shell_allowed=False,
                network_allowed=False,
                credential_use=False,
                max_steps=8,
                max_lifetime_seconds=600,
                max_retries=0,
                max_requests_per_minute=8,
                review_required=False,
                archive_behavior="summary_only",
            ),
        },
        "reviewer": {
            "AGENT_ROLE.md": "# Reviewer\n\nPurpose: validate worker output before durable promotion.\n",
            "TOOLS.md": "- schema validation\n- policy checks\n- no broad execution\n",
            "HEARTBEAT.md": "- verify schema\n- verify policy\n- block on ambiguity\n",
            "POLICY.md": "Reviewer must never approve ambiguous or policy-violating output.\n",
            "spawn.json": _spawn_policy(
                purpose="output_review",
                allowed_tools=["read", "validate_schema", "compare", "summarize"],
                allowed_read_roots=["./workspace/control", "./workspace/templates", "./data"],
                allowed_write_roots=["./workspace/control/approvals", "./workspace/control/queue"],
                shell_allowed=False,
                network_allowed=False,
                credential_use=False,
                max_steps=10,
                max_lifetime_seconds=600,
                max_retries=1,
                max_requests_per_minute=8,
                review_required=False,
                archive_behavior="review_only",
            ),
        },
        "archivist": {
            "AGENT_ROLE.md": "# Archivist\n\nPurpose: compact approved outputs into durable summaries and logs.\n",
            "TOOLS.md": "- summary writing\n- memory compaction\n- no direct risky execution\n",
            "HEARTBEAT.md": "- archive only approved outputs\n- strip transient noise\n- preserve traceability IDs\n",
            "POLICY.md": "Archivist writes summaries only after review passes. No raw dump of transient chatter.\n",
            "spawn.json": _spawn_policy(
                purpose="approved_archive",
                allowed_tools=["read", "write_notes", "compact"],
                allowed_read_roots=["./workspace/control", "./workspace/memory", "./workspace/roles"],
                allowed_write_roots=["./workspace/memory", "./workspace/logs", "./workspace/roles"],
                shell_allowed=False,
                network_allowed=False,
                credential_use=False,
                max_steps=12,
                max_lifetime_seconds=900,
                max_retries=1,
                max_requests_per_minute=8,
                review_required=False,
                archive_behavior="approved_summary_only",
            ),
        },
        "router": {
            "AGENT_ROLE.md": "# Router\n\nPurpose: classify incoming work and map it onto the narrowest safe worker template.\n",
            "TOOLS.md": "- classify\n- route\n- no direct execution\n",
            "HEARTBEAT.md": "- choose narrowest safe template\n- no self-spawn\n- no recursive fanout by default\n",
            "POLICY.md": "Router may propose worker selection but should not bypass reviewer or approval gates.\n",
            "spawn.json": _spawn_policy(
                purpose="routing_only",
                allowed_tools=["classify", "route", "summarize"],
                allowed_read_roots=["./workspace/control", "./workspace/templates", "./workspace/core"],
                allowed_write_roots=["./workspace/control/queue"],
                shell_allowed=False,
                network_allowed=False,
                credential_use=False,
                max_steps=6,
                max_lifetime_seconds=300,
                max_retries=0,
                max_requests_per_minute=12,
                review_required=False,
                archive_behavior="none",
            ),
        },
    }
    return common


def _spawn_policy(
    *,
    purpose: str,
    allowed_tools: list[str],
    allowed_read_roots: list[str],
    allowed_write_roots: list[str],
    shell_allowed: bool,
    network_allowed: bool,
    credential_use: bool,
    max_steps: int,
    max_lifetime_seconds: int,
    max_retries: int,
    max_requests_per_minute: int,
    review_required: bool,
    archive_behavior: str,
) -> dict[str, Any]:
    return {
        "purpose": purpose,
        "allowed_tools": allowed_tools,
        "allowed_read_roots": allowed_read_roots,
        "allowed_write_roots": allowed_write_roots,
        "shell_allowed": shell_allowed,
        "network_allowed": network_allowed,
        "credential_use": credential_use,
        "max_steps": max_steps,
        "max_lifetime_seconds": max_lifetime_seconds,
        "max_retries": max_retries,
        "max_requests_per_minute": max_requests_per_minute,
        "review_required": review_required,
        "archive_behavior": archive_behavior,
        "termination_conditions": [
            "task_complete",
            "timeout",
            "budget_exhausted",
            "policy_violation",
            "approval_required",
        ],
    }


def _paths_from_payload(payload: dict[str, Any]) -> list[str]:
    paths: set[str] = set()

    def _visit(obj: Any, key: str = "") -> None:
        if isinstance(obj, dict):
            for child_key, child_value in obj.items():
                _visit(child_value, str(child_key))
            return
        if isinstance(obj, list):
            for item in obj:
                _visit(item, key)
            return
        text = str(obj or "").strip()
        if not text:
            return
        if _PATH_KEY_RE.search(key) or text.startswith("/") or re.match(r"^[A-Za-z]:\\\\", text):
            paths.add(text)

    _visit(payload)
    return sorted(paths)


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (str(table_name),),
    ).fetchone()
    return bool(row)


def _json_loads(raw: Any, *, fallback: Any) -> Any:
    try:
        loaded = json.loads(str(raw or ""))
    except Exception:
        return fallback
    if isinstance(fallback, dict):
        return loaded if isinstance(loaded, dict) else fallback
    if isinstance(fallback, list):
        return loaded if isinstance(loaded, list) else fallback
    return loaded


def _write_json(path: Path, payload: Any) -> int:
    return _write_text(path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n")


def _write_text(path: Path, content: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    if previous == content:
        return 0
    path.write_text(content, encoding="utf-8")
    return 1


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    return cleaned.strip("-._") or "item"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_day_bucket() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
