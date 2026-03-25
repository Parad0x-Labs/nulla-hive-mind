from __future__ import annotations

import difflib
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.runtime_paths import data_path


def truncate_text(text: str, *, limit: int = 1800) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n...[truncated]"


def diff_preview(*, before: str, after: str, path: str, limit: int = 1600) -> str:
    diff_lines = list(
        difflib.unified_diff(
            str(before or "").splitlines(),
            str(after or "").splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=2,
        )
    )
    if not diff_lines:
        return ""
    preview = "\n".join(diff_lines[:40]).strip()
    if len(preview) <= limit:
        return preview
    return preview[: max(1, limit - 3)].rstrip() + "..."


def build_file_diff_artifact(
    *,
    path: str,
    action: str,
    before: str,
    after: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = {
        "artifact_type": "file_diff",
        "path": str(path or "").strip(),
        "action": str(action or "").strip(),
        "diff_preview": diff_preview(before=before, after=after, path=str(path or "").strip()),
    }
    artifact.update(dict(extra or {}))
    return artifact


def build_command_artifact(
    *,
    command: str,
    cwd: str,
    returncode: int,
    stdout: str,
    stderr: str,
    status: str,
    artifact_type: str = "command_output",
) -> dict[str, Any]:
    return {
        "artifact_type": artifact_type,
        "command": str(command or "").strip(),
        "cwd": str(cwd or "").strip(),
        "returncode": int(returncode or 0),
        "stdout": truncate_text(stdout, limit=2400),
        "stderr": truncate_text(stderr, limit=1600),
        "status": str(status or "").strip(),
    }


def build_failure_artifact(
    *,
    command: str,
    cwd: str,
    returncode: int,
    stdout: str,
    stderr: str,
    summary: str,
) -> dict[str, Any]:
    return {
        "artifact_type": "failure",
        "command": str(command or "").strip(),
        "cwd": str(cwd or "").strip(),
        "returncode": int(returncode or 0),
        "summary": str(summary or "").strip(),
        "stdout": truncate_text(stdout, limit=2400),
        "stderr": truncate_text(stderr, limit=1600),
    }


def session_key_for_workspace(session_id: str | None, workspace_root: Path) -> str:
    clean_session = str(session_id or "").strip()
    if clean_session:
        return clean_session.replace("/", "_")
    digest = hashlib.sha1(str(workspace_root.resolve()).encode("utf-8")).hexdigest()
    return f"workspace-{digest[:16]}"


def _force_fresh_source_timestamp(path: Path, *, minimum_mtime_ns: int = 0) -> None:
    if not path.exists() or not path.is_file():
        return
    stat_result = path.stat()
    target_ns = max(
        int(stat_result.st_mtime_ns) + 1,
        int(minimum_mtime_ns or 0) + 1_000_000_000,
        time.time_ns() + 1_000_000_000,
    )
    os.utime(path, ns=(target_ns, target_ns))


def record_workspace_mutation(
    *,
    session_id: str | None,
    workspace_root: Path,
    intent: str,
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    clean_changes = [dict(change) for change in changes if isinstance(change, dict)]
    if not clean_changes:
        return {}
    record = {
        "mutation_id": f"mutation-{uuid.uuid4().hex}",
        "session_key": session_key_for_workspace(session_id, workspace_root),
        "workspace_root": str(workspace_root.resolve()),
        "intent": str(intent or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "changes": clean_changes,
        "rolled_back_at": None,
    }
    records = _load_mutation_records(record["session_key"])
    records.append(record)
    _store_mutation_records(record["session_key"], records)
    return record


def rollback_last_workspace_mutation(
    *,
    session_id: str | None,
    workspace_root: Path,
) -> dict[str, Any] | None:
    session_key = session_key_for_workspace(session_id, workspace_root)
    records = _load_mutation_records(session_key)
    workspace_text = str(workspace_root.resolve())
    target_index = -1
    target_record: dict[str, Any] | None = None
    for index in range(len(records) - 1, -1, -1):
        record = dict(records[index] or {})
        if str(record.get("workspace_root") or "") != workspace_text:
            continue
        if record.get("rolled_back_at"):
            continue
        target_record = record
        target_index = index
        break
    if target_record is None:
        return None

    restored_paths: list[str] = []
    removed_paths: list[str] = []
    for change in reversed(list(target_record.get("changes") or [])):
        relative_path = str(change.get("path") or "").strip()
        if not relative_path:
            continue
        target = (workspace_root / relative_path).resolve()
        existed_before = bool(change.get("existed_before", False))
        before_text = str(change.get("before_text") or "")
        if existed_before:
            previous_mtime_ns = int(target.stat().st_mtime_ns) if target.exists() and target.is_file() else 0
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(before_text, encoding="utf-8")
            _force_fresh_source_timestamp(target, minimum_mtime_ns=previous_mtime_ns)
            restored_paths.append(relative_path)
        else:
            if target.exists():
                target.unlink()
                removed_paths.append(relative_path)

    target_record["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    records[target_index] = target_record
    _store_mutation_records(session_key, records)
    return {
        "mutation_id": str(target_record.get("mutation_id") or "").strip(),
        "intent": str(target_record.get("intent") or "").strip(),
        "restored_paths": restored_paths,
        "removed_paths": removed_paths,
        "changes": list(target_record.get("changes") or []),
    }


def latest_workspace_mutation(
    *,
    session_id: str | None,
    workspace_root: Path,
    require_unpromoted: bool = False,
) -> dict[str, Any] | None:
    session_key = session_key_for_workspace(session_id, workspace_root)
    workspace_text = str(workspace_root.resolve())
    records = _load_mutation_records(session_key)
    for index in range(len(records) - 1, -1, -1):
        record = dict(records[index] or {})
        if str(record.get("workspace_root") or "") != workspace_text:
            continue
        if record.get("rolled_back_at"):
            continue
        if require_unpromoted and str(record.get("procedure_id") or "").strip():
            continue
        return record
    return None


def mark_workspace_mutation_promoted(
    *,
    session_id: str | None,
    workspace_root: Path,
    mutation_id: str,
    procedure_id: str,
) -> None:
    clean_mutation_id = str(mutation_id or "").strip()
    clean_procedure_id = str(procedure_id or "").strip()
    if not clean_mutation_id or not clean_procedure_id:
        return
    session_key = session_key_for_workspace(session_id, workspace_root)
    records = _load_mutation_records(session_key)
    changed = False
    for index, raw_record in enumerate(records):
        record = dict(raw_record or {})
        if str(record.get("mutation_id") or "").strip() != clean_mutation_id:
            continue
        record["procedure_id"] = clean_procedure_id
        records[index] = record
        changed = True
        break
    if changed:
        _store_mutation_records(session_key, records)


def _mutation_log_path(session_key: str) -> Path:
    return data_path("runtime_execution", "mutations", f"{session_key}.json")


def _load_mutation_records(session_key: str) -> list[dict[str, Any]]:
    path = _mutation_log_path(session_key)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [dict(item) for item in payload if isinstance(item, dict)]


def _store_mutation_records(session_key: str, records: list[dict[str, Any]]) -> None:
    path = _mutation_log_path(session_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
