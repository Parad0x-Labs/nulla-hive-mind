from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional
    psutil = None

_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from apps.meet_and_greet_server import MeetAndGreetServerConfig, build_server
from core.model_registry import ModelRegistry
from core.runtime_paths import NULLA_HOME, PROJECT_ROOT
from core.knowledge_freshness import iso_now
from network.protocol import prune_nonce_cache
from storage.db import DEFAULT_DB_PATH, get_connection
from storage.event_hash_chain import verify_chain
from storage.knowledge_index import active_presence
from storage.knowledge_manifests import all_manifests
from storage.migrations import run_migrations
from storage.replica_table import all_holders


_REQUIRED_TABLES = {
    "local_tasks",
    "task_offers",
    "task_claims",
    "task_assignments",
    "task_results",
    "task_reviews",
    "peers",
    "agent_capabilities",
    "finalized_responses",
    "anti_abuse_signals",
    "scoreboard",
    "event_log_v2",
    "event_hash_chain",
    "knowledge_manifests",
    "knowledge_holders",
    "presence_leases",
    "model_provider_manifests",
    "candidate_knowledge_lane",
    "context_access_log",
    "nonce_cache",
}

_IMPORT_TARGETS = [
    "apps.nulla_agent",
    "apps.nulla_daemon",
    "apps.meet_and_greet_server",
    "apps.meet_and_greet_node",
    "core.human_input_adapter",
    "core.tiered_context_loader",
    "core.memory_first_router",
    "core.meet_and_greet_service",
    "core.channel_gateway",
]


@dataclass
class CheckResult:
    name: str
    status: str
    blocking: bool
    summary: str
    details: dict[str, Any]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_git(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(PROJECT_ROOT),
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _git_context() -> dict[str, Any]:
    return {
        "branch": _run_git(["rev-parse", "--abbrev-ref", "HEAD"]),
        "commit": _run_git(["rev-parse", "HEAD"]),
        "dirty": bool(_run_git(["status", "--porcelain"])),
    }


def _disk_snapshot(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    free_gb = usage.free / (1024**3)
    total_gb = usage.total / (1024**3)
    free_ratio = 0.0 if usage.total <= 0 else usage.free / usage.total
    return {
        "path": str(path),
        "free_gb": round(free_gb, 2),
        "total_gb": round(total_gb, 2),
        "free_ratio": round(free_ratio, 4),
    }


def _memory_snapshot() -> dict[str, Any]:
    if psutil is None:
        return {"available": False}
    vm = psutil.virtual_memory()
    return {
        "available": True,
        "total_gb": round(vm.total / (1024**3), 2),
        "available_gb": round(vm.available / (1024**3), 2),
        "used_percent": round(float(vm.percent), 1),
    }


def _workspace_runtime_artifacts() -> list[str]:
    patterns = [
        PROJECT_ROOT / "storage" / "nulla_web0_v2.db",
        PROJECT_ROOT / "storage" / "nulla_web0_v2.db-shm",
        PROJECT_ROOT / "storage" / "nulla_web0_v2.db-wal",
        PROJECT_ROOT / "data" / "keys" / "node_signing_key.b64",
    ]
    return [str(path) for path in patterns if path.exists()]


def _check_runtime_hygiene() -> CheckResult:
    artifacts = _workspace_runtime_artifacts()
    if artifacts:
        return CheckResult(
            name="runtime_hygiene",
            status="warn",
            blocking=False,
            summary="Workspace still contains stray runtime artifacts outside .nulla_local.",
            details={"artifacts": artifacts},
        )
    return CheckResult(
        name="runtime_hygiene",
        status="pass",
        blocking=False,
        summary="No stray runtime artifacts detected outside .nulla_local.",
        details={"artifacts": []},
    )


def _check_runtime_baseline() -> CheckResult:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM local_tasks) AS local_tasks,
                (SELECT COUNT(*) FROM task_state_events) AS task_state_events,
                (SELECT COUNT(*) FROM event_hash_chain) AS event_hash_chain,
                (SELECT COUNT(*) FROM knowledge_holders) AS knowledge_holders,
                (SELECT COUNT(*) FROM presence_leases) AS presence_leases,
                (SELECT COUNT(*) FROM candidate_knowledge_lane) AS candidate_knowledge_lane
            """
        ).fetchone()
    finally:
        conn.close()
    counts = dict(row or {})
    historical_load = sum(int(counts.get(key) or 0) for key in counts)
    fresh_runtime = historical_load == 0
    status = "pass" if fresh_runtime else "warn"
    return CheckResult(
        name="runtime_baseline",
        status=status,
        blocking=False,
        summary="Runtime baseline is fresh for a clean soak."
        if fresh_runtime
        else "Runtime already contains historical operational state; use a fresh NULLA_HOME for the soak if you want clean evidence.",
        details={
            "nulla_home": str(NULLA_HOME),
            "db_path": str(DEFAULT_DB_PATH),
            "table_counts": counts,
            "fresh_runtime": fresh_runtime,
        },
    )


def _check_disk() -> CheckResult:
    project_disk = _disk_snapshot(PROJECT_ROOT)
    runtime_disk = _disk_snapshot(NULLA_HOME)
    low = []
    for label, snap in (("project", project_disk), ("runtime", runtime_disk)):
        if snap["free_gb"] < 2.0 or snap["free_ratio"] < 0.05:
            low.append(label)
    status = "fail" if low else "pass"
    return CheckResult(
        name="disk_headroom",
        status=status,
        blocking=bool(low),
        summary="Disk headroom is healthy." if not low else "Disk headroom is too low for a safe soak run.",
        details={"project": project_disk, "runtime": runtime_disk, "low_targets": low},
    )


def _check_runtime_writable() -> CheckResult:
    try:
        NULLA_HOME.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="nulla-preflight-", dir=NULLA_HOME, delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
        return CheckResult(
            name="runtime_writable",
            status="pass",
            blocking=True,
            summary="Runtime directories are writable.",
            details={"nulla_home": str(NULLA_HOME)},
        )
    except Exception as exc:
        return CheckResult(
            name="runtime_writable",
            status="fail",
            blocking=True,
            summary="Runtime directories are not writable.",
            details={"error": str(exc), "nulla_home": str(NULLA_HOME)},
        )


def _check_imports() -> CheckResult:
    failures: list[dict[str, str]] = []
    for target in _IMPORT_TARGETS:
        try:
            importlib.import_module(target)
        except Exception as exc:
            failures.append({"module": target, "error": str(exc)})
    if failures:
        return CheckResult(
            name="import_sanity",
            status="fail",
            blocking=True,
            summary="One or more core modules failed to import.",
            details={"failures": failures},
        )
    return CheckResult(
        name="import_sanity",
        status="pass",
        blocking=True,
        summary="Core modules import cleanly.",
        details={"modules_checked": list(_IMPORT_TARGETS)},
    )


def _check_schema() -> CheckResult:
    run_migrations()
    conn = get_connection()
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        tables = {str(row["name"]) for row in rows}
    finally:
        conn.close()
    missing = sorted(_REQUIRED_TABLES - tables)
    if missing:
        return CheckResult(
            name="schema_integrity",
            status="fail",
            blocking=True,
            summary="Required schema tables are missing.",
            details={"missing_tables": missing},
        )
    return CheckResult(
        name="schema_integrity",
        status="pass",
        blocking=True,
        summary="Required schema tables are present.",
        details={"table_count": len(tables)},
    )


def _check_event_chain() -> CheckResult:
    ok = verify_chain()
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM event_hash_chain").fetchone()
        count = int((row or {"cnt": 0})["cnt"])
    finally:
        conn.close()
    return CheckResult(
        name="event_hash_chain",
        status="pass" if ok else "fail",
        blocking=True,
        summary="Event hash chain verified cleanly." if ok else "Event hash chain verification failed.",
        details={"entry_count": count},
    )


def _check_meet_defaults() -> CheckResult:
    defaults = MeetAndGreetServerConfig()
    public_without_token_ok = True
    error_text = ""
    try:
        build_server(MeetAndGreetServerConfig(host="0.0.0.0", port=0, auth_token=None))
    except Exception as exc:
        public_without_token_ok = False
        error_text = str(exc)
    status = "pass" if defaults.host == "127.0.0.1" and not public_without_token_ok else "fail"
    return CheckResult(
        name="meet_safe_defaults",
        status=status,
        blocking=True,
        summary="Meet service defaults are loopback-safe and public binds require auth."
        if status == "pass"
        else "Meet service defaults are not enforcing safe bind posture.",
        details={
            "default_host": defaults.host,
            "default_port": defaults.port,
            "default_max_request_bytes": defaults.max_request_bytes,
            "default_write_requests_per_minute": defaults.write_requests_per_minute,
            "public_bind_without_token_blocked": not public_without_token_ok,
            "public_bind_error": error_text,
        },
    )


def _check_provider_posture() -> CheckResult:
    registry = ModelRegistry()
    manifests = registry.list_manifests(enabled_only=False)
    warnings = registry.startup_warnings()
    enabled = [manifest.provider_id for manifest in manifests if manifest.enabled]
    return CheckResult(
        name="provider_posture",
        status="pass" if not warnings else "warn",
        blocking=False,
        summary="Provider manifests are clean." if not warnings else "Provider manifests or runtimes have warnings.",
        details={
            "registered_provider_count": len(manifests),
            "enabled_provider_ids": enabled,
            "warnings": warnings,
        },
    )


def _check_task_state_health() -> CheckResult:
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
        pending_tasks = int((conn.execute("SELECT COUNT(*) AS cnt FROM local_tasks WHERE outcome = 'pending'").fetchone() or {"cnt": 0})["cnt"])
    finally:
        conn.close()
    stuck_states = [
        dict(row)
        for row in latest_rows
        if str(row["to_state"]) in {"offered", "claimed", "assigned", "running"}
    ]
    status = "warn" if stuck_states or pending_tasks else "pass"
    return CheckResult(
        name="task_state_health",
        status=status,
        blocking=False,
        summary="No obviously stuck task lifecycle rows detected."
        if status == "pass"
        else "Pending or still-active task lifecycle rows exist and should be reviewed before soak.",
        details={
            "pending_local_tasks": pending_tasks,
            "active_state_rows": stuck_states[:25],
            "active_state_count": len(stuck_states),
        },
    )


def _check_knowledge_state() -> CheckResult:
    now_iso = iso_now()
    presence = active_presence(limit=2048)
    manifests = all_manifests(limit=4096)
    holders = all_holders(limit=4096)
    expired_presence = [row for row in presence if str(row.get("lease_expires_at") or "") < now_iso]
    expired_holders = [row for row in holders if str(row.get("expires_at") or "") < now_iso and str(row.get("status")) == "active"]
    status = "warn" if expired_presence or expired_holders else "pass"
    return CheckResult(
        name="knowledge_state",
        status=status,
        blocking=False,
        summary="Presence leases and holder rows look coherent."
        if status == "pass"
        else "Expired presence or holder rows are still marked active.",
        details={
            "active_presence_count": len(presence),
            "manifest_count": len(manifests),
            "holder_count": len(holders),
            "expired_presence_rows": expired_presence[:25],
            "expired_holder_rows": expired_holders[:25],
        },
    )


def _check_nonce_cache() -> CheckResult:
    conn = get_connection()
    try:
        before = int((conn.execute("SELECT COUNT(*) AS cnt FROM nonce_cache").fetchone() or {"cnt": 0})["cnt"])
    finally:
        conn.close()
    pruned = prune_nonce_cache()
    conn = get_connection()
    try:
        after = int((conn.execute("SELECT COUNT(*) AS cnt FROM nonce_cache").fetchone() or {"cnt": 0})["cnt"])
    finally:
        conn.close()
    return CheckResult(
        name="nonce_cache",
        status="pass",
        blocking=False,
        summary="Nonce cache prune completed.",
        details={"before": before, "after": after, "pruned": pruned},
    )


def _check_system_headroom() -> CheckResult:
    memory = _memory_snapshot()
    if not memory.get("available"):
        return CheckResult(
            name="system_headroom",
            status="warn",
            blocking=False,
            summary="psutil not available; memory headroom not measured.",
            details={"memory": memory},
        )
    status = "fail" if float(memory["available_gb"]) < 1.0 or float(memory["used_percent"]) > 95.0 else "pass"
    return CheckResult(
        name="system_headroom",
        status=status,
        blocking=bool(status == "fail"),
        summary="Memory headroom looks healthy." if status == "pass" else "Memory headroom is too low for a safe soak run.",
        details={"memory": memory},
    )


def build_overnight_readiness_report() -> dict[str, Any]:
    run_migrations()
    checks = [
        _check_runtime_hygiene(),
        _check_runtime_baseline(),
        _check_disk(),
        _check_runtime_writable(),
        _check_imports(),
        _check_schema(),
        _check_event_chain(),
        _check_meet_defaults(),
        _check_provider_posture(),
        _check_task_state_health(),
        _check_knowledge_state(),
        _check_nonce_cache(),
        _check_system_headroom(),
    ]
    blocking_failures = [check for check in checks if check.blocking and check.status == "fail"]
    warning_checks = [check for check in checks if check.status == "warn"]
    go_no_go = "NO_GO" if blocking_failures else ("GO_WITH_WARNINGS" if warning_checks else "GO")
    return {
        "generated_at": _utcnow(),
        "host": socket.gethostname(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python_version": platform.python_version(),
        },
        "git": _git_context(),
        "runtime": {
            "project_root": str(PROJECT_ROOT),
            "nulla_home": str(NULLA_HOME),
            "db_path": str(DEFAULT_DB_PATH),
        },
        "go_no_go": go_no_go,
        "blocking_failures": [asdict(item) for item in blocking_failures],
        "warnings": [asdict(item) for item in warning_checks],
        "checks": [asdict(item) for item in checks],
    }


def render_overnight_readiness_report(report: dict[str, Any]) -> str:
    lines = [
        "NULLA OVERNIGHT READINESS REPORT",
        "",
        f"Generated: {report['generated_at']}",
        f"Host: {report['host']}",
        f"Platform: {report['platform']['system']} {report['platform']['release']}",
        f"Python: {report['platform']['python_version']}",
        f"Branch: {report['git'].get('branch') or 'unknown'}",
        f"Commit: {report['git'].get('commit') or 'unknown'}",
        f"Go / No-Go: {report['go_no_go']}",
        "",
        "Checks:",
    ]
    for item in report["checks"]:
        lines.append(f"- [{item['status'].upper()}] {item['name']}: {item['summary']}")
    if report["blocking_failures"]:
        lines.extend(["", "Blocking Failures:"])
        for item in report["blocking_failures"]:
            lines.append(f"- {item['name']}: {item['summary']}")
    if report["warnings"]:
        lines.extend(["", "Warnings:"])
        for item in report["warnings"]:
            lines.append(f"- {item['name']}: {item['summary']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_overnight_readiness_report(build_overnight_readiness_report()))
