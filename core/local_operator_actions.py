from __future__ import annotations

import csv
import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core import audit_logger, policy_engine
from core.execution_gate import ExecutionGate
from core.reasoning_engine import Plan
from core.runtime_paths import data_path
from storage.db import get_connection


_INSPECT_HINTS = (
    "disk bloat",
    "what is eating space",
    "what's eating space",
    "taking space",
    "space usage",
    "find large files",
    "find c drive",
    "find disk",
)
_CLEAN_HINTS = ("clean temp", "cleanup temp", "clean cache", "cleanup cache", "remove temp", "delete temp")
_PROCESS_HINTS = (
    "what processes",
    "top processes",
    "startup offenders",
    "process offenders",
    "memory hogs",
    "cpu hogs",
)
_SERVICE_HINTS = (
    "what services",
    "running services",
    "service offenders",
    "startup services",
    "startup items",
    "launch agents",
)
_TOOL_HINTS = (
    "what tools do you have",
    "list tools",
    "show tools",
    "tool inventory",
    "what can you execute",
    "what actions can you take",
)
_SCHEDULE_HINTS = (
    "schedule a meeting",
    "schedule meeting",
    "create calendar event",
    "create meeting",
    "schedule an event",
)
_APPROVAL_HINTS = ("approve", "go ahead", "do it", "proceed", "yes", "fuck it", "clean all", "delete all", "remove all")
_APPROVAL_ID_RE = re.compile(
    r"\b(?:approve|cleanup|clean|schedule|calendar|meeting|move|archive)\s+([0-9a-f]{8}-[0-9a-f-]{27,})\b",
    re.IGNORECASE,
)
_MOVE_WORD_RE = re.compile(r"\b(?:move|relocate|archive)\b", re.IGNORECASE)
_QUOTED_PATH_RE = re.compile(r"""["']([^"']+)["']""")
_WINDOWS_PATH_RE = re.compile(r"\b([A-Za-z]:\\[^\n\r\"']*)")
_POSIX_PATH_RE = re.compile(r"\b(?:in|on|under|at)\s+(/[^?\n\r]+)")
_TIME_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
_ISO_DATETIME_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?\b")
_DURATION_RE = re.compile(r"\bfor\s+(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)\b", re.IGNORECASE)
_TEMPISH_NAMES = {"temp", "tmp", "cache", "caches"}


@dataclass(frozen=True)
class OperatorActionIntent:
    kind: str
    target_path: str | None = None
    destination_path: str | None = None
    approval_requested: bool = False
    action_id: str | None = None
    raw_text: str = ""


@dataclass
class OperatorActionResult:
    ok: bool
    status: str
    response_text: str
    details: dict[str, Any]
    learned_plan: Plan | None = None


def operator_capability_ledger() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for tool in list_operator_tools():
        tool_id = str(tool.get("tool_id") or "").strip()
        if not tool_id:
            continue
        guardrails = _operator_action_guardrails(tool_id, destructive=bool(tool.get("destructive")))
        available = bool(tool.get("available"))
        entries.append(
            {
                "capability_id": f"operator.{tool_id}",
                "surface": str(tool.get("category") or "local_operator").strip() or "local_operator",
                "claim": _operator_capability_claim(tool_id, destructive=guardrails["destructive"]),
                "supported": available,
                "support_level": _operator_capability_support_level(tool_id, available=available),
                "partial_reason": _operator_partial_support_reason(tool_id, available=available),
                "unsupported_reason": _operator_capability_unavailable_reason(tool_id),
                "nearby_capability_ids": _operator_nearby_capability_ids(tool_id),
                "intents": [f"operator.{tool_id}"] if tool_id not in {"discord_post", "telegram_send"} else [],
                "public_tag": f"operator.{tool_id}",
                "requires_approval": bool(guardrails["requires_approval"]),
                "destructive": bool(guardrails["destructive"]),
                "outward_facing": bool(guardrails["outward_facing"]),
                "privacy_sensitive": bool(guardrails["privacy_sensitive"]),
            }
        )
    return entries


def parse_operator_action_intent(user_text: str) -> OperatorActionIntent | None:
    text = str(user_text or "").strip()
    if not text:
        return None
    lowered = text.lower()
    quoted_values = _extract_quoted_values(text)
    target_path = quoted_values[0] if quoted_values else _extract_path(text)
    destination_path = quoted_values[1] if len(quoted_values) >= 2 else None
    action_id = _extract_action_id(text)

    if any(hint in lowered for hint in _TOOL_HINTS):
        return OperatorActionIntent(kind="list_tools", raw_text=text)

    if any(hint in lowered for hint in _PROCESS_HINTS):
        return OperatorActionIntent(kind="inspect_processes", raw_text=text)

    if any(hint in lowered for hint in _SERVICE_HINTS):
        return OperatorActionIntent(kind="inspect_services", raw_text=text)

    if any(hint in lowered for hint in _SCHEDULE_HINTS) or (
        action_id and "approve" in lowered and any(token in lowered for token in ("calendar", "meeting", "schedule"))
    ):
        approval_requested = any(marker in lowered for marker in _APPROVAL_HINTS)
        return OperatorActionIntent(
            kind="schedule_calendar_event",
            approval_requested=approval_requested,
            action_id=action_id,
            raw_text=text,
        )

    if any(hint in lowered for hint in _CLEAN_HINTS) or ("temp files" in lowered and "clean" in lowered):
        approval_requested = any(marker in lowered for marker in _APPROVAL_HINTS)
        return OperatorActionIntent(
            kind="cleanup_temp_files",
            target_path=target_path,
            approval_requested=approval_requested,
            action_id=action_id,
            raw_text=text,
        )

    if (target_path and _MOVE_WORD_RE.search(lowered)) or (
        action_id and "approve" in lowered and any(token in lowered for token in ("move", "archive"))
    ):
        approval_requested = any(marker in lowered for marker in _APPROVAL_HINTS)
        return OperatorActionIntent(
            kind="move_path",
            target_path=target_path,
            destination_path=destination_path,
            approval_requested=approval_requested,
            action_id=action_id,
            raw_text=text,
        )

    if any(hint in lowered for hint in _INSPECT_HINTS) or (
        "space" in lowered and any(token in lowered for token in ("disk", "drive", "folder", "storage"))
    ):
        return OperatorActionIntent(kind="inspect_disk_usage", target_path=target_path, raw_text=text)

    return None


def dispatch_operator_action(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    if intent.kind == "list_tools":
        return _handle_list_tools(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "inspect_processes":
        return _handle_inspect_processes(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "inspect_services":
        return _handle_inspect_services(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "inspect_disk_usage":
        return _handle_inspect_disk_usage(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "cleanup_temp_files":
        return _handle_cleanup_temp_files(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "move_path":
        return _handle_move_path(intent, task_id=task_id, session_id=session_id)
    if intent.kind == "schedule_calendar_event":
        return _handle_schedule_calendar_event(intent, task_id=task_id, session_id=session_id)
    return OperatorActionResult(
        ok=False,
        status="unsupported",
        response_text="I recognized an operator action request, but that action is not wired on this runtime yet.",
        details={
            "kind": intent.kind,
            "capability_gap": {
                "requested_capability": f"operator.{intent.kind}",
                "requested_label": intent.kind,
                "support_level": "unsupported",
                "gap_kind": "unwired",
                "reason": f"Operator action `{intent.kind}` is not wired on this runtime.",
                "nearby_alternatives": _operator_nearby_alternatives(intent.kind),
            },
        },
    )


def list_operator_tools() -> list[dict[str, Any]]:
    tools = [
        {
            "tool_id": "inspect_disk_usage",
            "category": "local_operator",
            "destructive": False,
            **_operator_action_guardrails("inspect_disk_usage", destructive=False),
            "available": True,
            "description": "Inspect disk usage and identify large directories or temp bloat.",
        },
        {
            "tool_id": "cleanup_temp_files",
            "category": "local_operator",
            "destructive": True,
            **_operator_action_guardrails("cleanup_temp_files", destructive=True),
            "available": True,
            "description": "Delete contents of bounded temp roots after explicit approval.",
        },
        {
            "tool_id": "inspect_processes",
            "category": "local_operator",
            "destructive": False,
            **_operator_action_guardrails("inspect_processes", destructive=False),
            "available": _process_inspection_available(),
            "description": "Inspect the heaviest running processes by CPU and memory use.",
        },
        {
            "tool_id": "inspect_services",
            "category": "local_operator",
            "destructive": False,
            **_operator_action_guardrails("inspect_services", destructive=False),
            "available": _service_inspection_available(),
            "description": "Inspect running services or startup agents on the local machine.",
        },
        {
            "tool_id": "schedule_calendar_event",
            "category": "calendar",
            "destructive": True,
            **_operator_action_guardrails("schedule_calendar_event", destructive=True),
            "available": True,
            "description": "Create a .ics calendar event in the local calendar outbox; balanced/strict modes still require approval.",
        },
        {
            "tool_id": "move_path",
            "category": "local_operator",
            "destructive": True,
            **_operator_action_guardrails("move_path", destructive=True),
            "available": True,
            "description": "Move or archive a bounded local file/folder after explicit approval.",
        },
        {
            "tool_id": "discord_post",
            "category": "communication",
            "destructive": True,
            **_operator_action_guardrails("discord_post", destructive=True),
            "available": _discord_available(),
            "description": "Send a Discord message through the configured bridge credentials.",
        },
        {
            "tool_id": "telegram_send",
            "category": "communication",
            "destructive": True,
            **_operator_action_guardrails("telegram_send", destructive=True),
            "available": _telegram_available(),
            "description": "Send a Telegram message through the configured bridge credentials.",
        },
    ]
    return tools


def _operator_action_guardrails(tool_id: str, *, destructive: bool) -> dict[str, bool]:
    normalized = str(tool_id or "").strip().lower()
    outward_facing = normalized in {"discord_post", "telegram_send"}
    privacy_sensitive = outward_facing
    return {
        "destructive": bool(destructive),
        "outward_facing": outward_facing,
        "privacy_sensitive": privacy_sensitive,
        "requires_approval": bool(destructive or outward_facing or privacy_sensitive),
    }


def _operator_capability_claim(tool_id: str, *, destructive: bool) -> str:
    claims = {
        "inspect_disk_usage": "inspect disk usage on the local machine",
        "cleanup_temp_files": "clean bounded temp roots on the local machine after explicit approval",
        "inspect_processes": "inspect running processes on the local machine",
        "inspect_services": "inspect running services or startup agents on the local machine",
        "schedule_calendar_event": "create local calendar events after approval when required",
        "move_path": "move or archive bounded local paths after explicit approval",
        "discord_post": "send Discord messages through the configured bridge",
        "telegram_send": "send Telegram messages through the configured bridge",
    }
    claim = claims.get(tool_id, f"use operator action `{tool_id}`")
    if destructive and "approval" not in claim:
        return f"{claim} after explicit approval"
    return claim


def _operator_capability_unavailable_reason(tool_id: str) -> str:
    reasons = {
        "discord_post": "Discord bridge sending is not configured on this runtime.",
        "telegram_send": "Telegram bridge sending is not configured on this runtime.",
        "inspect_processes": "Process inspection is not available on this host/runtime.",
        "inspect_services": "Service inspection is not available on this host/runtime.",
    }
    return reasons.get(tool_id, f"Operator capability `{tool_id}` is not available on this host/runtime.")


def _operator_capability_support_level(tool_id: str, *, available: bool) -> str:
    if not available:
        return "unsupported"
    if tool_id == "schedule_calendar_event":
        return "partial"
    return "full"


def _operator_partial_support_reason(tool_id: str, *, available: bool) -> str:
    if not available:
        return ""
    if tool_id == "schedule_calendar_event":
        return "This writes a local .ics event into the calendar outbox, not a universal live calendar-service integration."
    return ""


def _operator_nearby_capability_ids(tool_id: str) -> list[str]:
    mapping = {
        "discord_post": ["operator.telegram_send"],
        "telegram_send": ["operator.discord_post"],
    }
    return list(mapping.get(tool_id, []))


def _operator_nearby_alternatives(tool_id: str) -> list[str]:
    if tool_id in {"discord_post", "telegram_send"}:
        return ["I can draft the message text here before you send it yourself."]
    return []


def _handle_list_tools(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    del intent, session_id
    ledger = operator_capability_ledger()
    available = [entry for entry in ledger if entry.get("supported")]
    partial = [entry for entry in available if str(entry.get("support_level") or "").strip().lower() == "partial"]
    full = [entry for entry in available if str(entry.get("support_level") or "").strip().lower() != "partial"]
    unavailable = [entry for entry in ledger if not entry.get("supported")]
    lines = ["Available tool inventory:"]
    for entry in full:
        flag = "approval required" if entry.get("requires_approval") else "read-only"
        capability_id = str(entry.get("capability_id") or "").strip()
        surface = str(entry.get("surface") or "local_operator").strip()
        lines.append(f"- {capability_id} ({surface}, {flag}): {str(entry.get('claim') or '').strip()}")
    if partial:
        lines.append("")
        lines.append("Partially supported:")
        for entry in partial:
            capability_id = str(entry.get("capability_id") or "").strip()
            note = str(entry.get("partial_reason") or "").strip()
            lines.append(f"- {capability_id}: {str(entry.get('claim') or '').strip()}" + (f" ({note})" if note else ""))
    if unavailable:
        lines.append("")
        lines.append("Configured but currently unavailable:")
        for entry in unavailable:
            capability_id = str(entry.get("capability_id") or "").strip()
            lines.append(f"- {capability_id}: {str(entry.get('unsupported_reason') or '').strip()}")
    audit_logger.log(
        "operator_action_list_tools",
        target_id=task_id,
        target_type="task",
        details={"available_tool_ids": [str(entry.get("capability_id") or "").strip() for entry in available]},
    )
    return OperatorActionResult(
        ok=True,
        status="reported",
        response_text="\n".join(lines),
        details={"available_tools": available, "unavailable_tools": unavailable},
    )


def _handle_inspect_processes(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    del intent, session_id
    gate = ExecutionGate.evaluate_local_action(
        "inspect_processes",
        destructive=False,
        user_approved=True,
    )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't inspect running processes right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )
    rows = _inspect_processes()
    if not rows:
        return OperatorActionResult(
            ok=False,
            status="unavailable",
            response_text="I couldn't inspect running processes on this host.",
            details={},
        )
    lines = ["Top running processes by combined CPU and memory pressure:"]
    for row in rows[:6]:
        lines.append(
            f"- PID {row['pid']} {row['name']}: CPU {row['cpu_percent']:.1f}% | MEM {row['mem_percent']:.1f}%"
        )
    audit_logger.log(
        "operator_action_inspect_processes",
        target_id=task_id,
        target_type="task",
        details={"top_processes": rows[:6]},
    )
    return OperatorActionResult(
        ok=True,
        status="reported",
        response_text="\n".join(lines),
        details={"top_processes": rows[:6]},
    )


def _handle_inspect_services(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    del intent, session_id
    gate = ExecutionGate.evaluate_local_action(
        "inspect_services",
        destructive=False,
        user_approved=True,
    )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't inspect services right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )
    rows = _inspect_services()
    if not rows:
        return OperatorActionResult(
            ok=False,
            status="unavailable",
            response_text="I couldn't inspect services or startup agents on this host.",
            details={},
        )
    lines = ["Visible services or startup agents:"]
    for row in rows[:8]:
        state = str(row.get("state") or "unknown")
        label = str(row.get("name") or "unknown")
        detail = str(row.get("detail") or "").strip()
        if detail:
            lines.append(f"- {label}: {state} | {detail}")
        else:
            lines.append(f"- {label}: {state}")
    audit_logger.log(
        "operator_action_inspect_services",
        target_id=task_id,
        target_type="task",
        details={"services": rows[:8]},
    )
    return OperatorActionResult(
        ok=True,
        status="reported",
        response_text="\n".join(lines),
        details={"services": rows[:8]},
    )


def _handle_inspect_disk_usage(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    gate = ExecutionGate.evaluate_local_action(
        "inspect_disk_usage",
        destructive=False,
        user_approved=True,
        reads_workspace=True,
    )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't inspect storage right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )

    target = _resolve_target_path(intent.target_path)
    if not target.exists():
        return OperatorActionResult(
            ok=False,
            status="missing_path",
            response_text=f"I couldn't inspect storage because this path does not exist: {target}",
            details={"target_path": str(target)},
        )

    summary = _inspect_storage(target)
    cleanup_roots = _candidate_cleanup_roots(intent.target_path)
    preview_total = int(sum(_path_size(path, deadline=time.monotonic() + 0.8)["bytes"] for path in cleanup_roots))
    pending_action_id = None
    if cleanup_roots:
        pending_action_id = _create_pending_action(
            session_id=session_id,
            task_id=task_id,
            action_kind="cleanup_temp_files",
            scope={
                "paths": [str(path) for path in cleanup_roots],
                "target_path": str(target),
                "bytes_preview": preview_total,
            },
        )

    lines = [
        f"Storage scan for {target}",
        f"Free space on volume: {_fmt_bytes(summary['disk_free_bytes'])} / {_fmt_bytes(summary['disk_total_bytes'])}",
    ]
    if summary["top_entries"]:
        lines.append("Largest entries:")
        for row in summary["top_entries"][:6]:
            marker = " (approx)" if row.get("approximate") else ""
            lines.append(f"- {row['name']}: {_fmt_bytes(int(row['bytes']))}{marker}")
    else:
        lines.append("No large entries were found in the requested scope.")

    if cleanup_roots:
        lines.append("")
        lines.append(f"Safe temp cleanup preview: {_fmt_bytes(preview_total)} across {len(cleanup_roots)} bounded temp root(s).")
        for path in cleanup_roots[:4]:
            lines.append(f"- {path}")
        lines.append("")
        lines.append(
            f"If you want me to execute it, reply with: clean all temp files. Pending action id: {pending_action_id}"
        )

    audit_logger.log(
        "operator_action_inspect_disk_usage",
        target_id=task_id,
        target_type="task",
        details={
            "target_path": str(target),
            "cleanup_roots": [str(path) for path in cleanup_roots],
            "pending_action_id": pending_action_id,
        },
    )

    return OperatorActionResult(
        ok=True,
        status="reported",
        response_text="\n".join(lines),
        details={
            "target_path": str(target),
            "pending_action_id": pending_action_id,
            "cleanup_roots": [str(path) for path in cleanup_roots],
            "top_entries": summary["top_entries"],
        },
    )


def _handle_cleanup_temp_files(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    pending = _load_pending_action(session_id=session_id, action_kind="cleanup_temp_files", action_id=intent.action_id)
    if pending is None:
        preview = _handle_inspect_disk_usage(
            OperatorActionIntent(kind="inspect_disk_usage", target_path=intent.target_path),
            task_id=task_id,
            session_id=session_id,
        )
        preview.response_text += "\nCleanup was not executed because there was no approved pending cleanup plan yet."
        preview.details["requires_user_approval"] = True
        return preview

    gate = ExecutionGate.evaluate_local_action(
        "cleanup_temp_files",
        destructive=True,
        user_approved=bool(intent.approval_requested),
        writes_workspace=True,
    )
    if gate.requires_user_approval and not intent.approval_requested:
        return OperatorActionResult(
            ok=False,
            status="approval_required",
            response_text=(
                "Temp cleanup is ready but still needs explicit approval. "
                f"Reply with: approve cleanup {pending['action_id']} or just say clean all temp files."
            ),
            details={"action_id": pending["action_id"]},
        )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't run temp cleanup right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )

    scope = json.loads(str(pending.get("scope_json") or "{}"))
    cleanup_paths = [Path(str(value)).expanduser() for value in scope.get("paths") or []]
    before_total = 0
    after_total = 0
    deleted_files = 0
    deleted_dirs = 0
    errors: list[str] = []
    for root in cleanup_paths:
        if not root.exists():
            continue
        before_info = _path_size(root, deadline=time.monotonic() + 1.2)
        before_total += int(before_info["bytes"])
        counts = _delete_children(root)
        deleted_files += int(counts["deleted_files"])
        deleted_dirs += int(counts["deleted_dirs"])
        errors.extend([str(item) for item in counts["errors"]])
        after_info = _path_size(root, deadline=time.monotonic() + 1.2)
        after_total += int(after_info["bytes"])

    reclaimed = max(0, before_total - after_total)
    _mark_action_executed(
        pending["action_id"],
        result={
            "before_bytes": before_total,
            "after_bytes": after_total,
            "reclaimed_bytes": reclaimed,
            "deleted_files": deleted_files,
            "deleted_dirs": deleted_dirs,
            "errors": errors,
        },
    )

    learned_plan = Plan(
        summary="Verified user-space temp cleanup workflow with approval and before/after verification.",
        abstract_steps=[
            "inspect bounded temp roots",
            "prepare cleanup preview",
            "require explicit user approval",
            "delete child entries inside temp roots",
            "verify reclaimed space after cleanup",
        ],
        confidence=0.93 if reclaimed > 0 and not errors else 0.82,
        risk_flags=[],
        simulation_steps=[],
        safe_actions=[{"action": "cleanup_temp_files", "paths": [str(path) for path in cleanup_paths]}],
        reads_workspace=True,
        writes_workspace=True,
        requests_network=False,
        requests_subprocess=False,
        evidence_sources=["local_operator:cleanup_temp_files"],
    )

    response = (
        f"Temp cleanup finished. Reclaimed {_fmt_bytes(reclaimed)} "
        f"by deleting {deleted_files} files and {deleted_dirs} directories."
    )
    if errors:
        response += f" Some entries could not be removed ({len(errors)} issue(s))."

    audit_logger.log(
        "operator_action_cleanup_temp_files",
        target_id=task_id,
        target_type="task",
        details={
            "action_id": pending["action_id"],
            "paths": [str(path) for path in cleanup_paths],
            "reclaimed_bytes": reclaimed,
            "deleted_files": deleted_files,
            "deleted_dirs": deleted_dirs,
            "error_count": len(errors),
        },
    )

    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=response,
        details={
            "action_id": pending["action_id"],
            "reclaimed_bytes": reclaimed,
            "deleted_files": deleted_files,
            "deleted_dirs": deleted_dirs,
            "error_count": len(errors),
        },
        learned_plan=learned_plan,
    )


def _handle_move_path(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    pending = _load_pending_action(session_id=session_id, action_kind="move_path", action_id=intent.action_id)
    if pending is None:
        parsed = _parse_move_request(
            intent.raw_text,
            fallback_source=intent.target_path,
            fallback_destination=intent.destination_path,
        )
        if not parsed:
            return OperatorActionResult(
                ok=False,
                status="invalid_request",
                response_text=(
                    "I can move or archive a bounded local path, but I need a quoted source path. "
                    'Use a format like: move "/path/to/source" to "/path/to/archive" '
                    'or archive "/path/to/source"'
                ),
                details={},
            )
        source = Path(str(parsed["source_path"])).expanduser()
        destination_dir = Path(str(parsed["destination_dir"])).expanduser()
        validation_error = _validate_move_scope(source, destination_dir)
        if validation_error:
            return OperatorActionResult(
                ok=False,
                status="blocked",
                response_text=validation_error,
                details={
                    "source_path": str(source),
                    "destination_dir": str(destination_dir),
                },
            )
        final_path = _resolved_move_target(source, destination_dir)
        if final_path.exists():
            return OperatorActionResult(
                ok=False,
                status="conflict",
                response_text=f"I won't move {source} because the destination already exists: {final_path}",
                details={
                    "source_path": str(source),
                    "destination_path": str(final_path),
                },
            )
        action_id = _create_pending_action(
            session_id=session_id,
            task_id=task_id,
            action_kind="move_path",
            scope={
                "source_path": str(source),
                "destination_dir": str(destination_dir),
                "destination_path": str(final_path),
            },
        )
        return OperatorActionResult(
            ok=True,
            status="approval_required",
            response_text=(
                f"Move preview ready.\n"
                f"- Source: {source}\n"
                f"- Destination: {final_path}\n\n"
                f"Reply with: approve move {action_id}"
            ),
            details={
                "action_id": action_id,
                "source_path": str(source),
                "destination_dir": str(destination_dir),
                "destination_path": str(final_path),
            },
        )

    gate = ExecutionGate.evaluate_local_action(
        "move_path",
        destructive=True,
        user_approved=bool(intent.approval_requested),
        writes_workspace=True,
    )
    if gate.requires_user_approval and not intent.approval_requested:
        return OperatorActionResult(
            ok=False,
            status="approval_required",
            response_text=(
                "The move/archive action is ready but still needs explicit approval. "
                f"Reply with: approve move {pending['action_id']}"
            ),
            details={"action_id": pending["action_id"]},
        )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't move that path right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )

    scope = json.loads(str(pending.get("scope_json") or "{}"))
    source = Path(str(scope.get("source_path") or "")).expanduser()
    destination_dir = Path(str(scope.get("destination_dir") or "")).expanduser()
    final_path = Path(str(scope.get("destination_path") or "")).expanduser()
    validation_error = _validate_move_scope(source, destination_dir)
    if validation_error:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=validation_error,
            details={
                "action_id": pending["action_id"],
                "source_path": str(source),
                "destination_dir": str(destination_dir),
            },
        )
    if not source.exists():
        return OperatorActionResult(
            ok=False,
            status="missing_path",
            response_text=f"I can't move this path because it no longer exists: {source}",
            details={"action_id": pending["action_id"], "source_path": str(source)},
        )
    destination_dir.mkdir(parents=True, exist_ok=True)
    if final_path.exists():
        return OperatorActionResult(
            ok=False,
            status="conflict",
            response_text=f"I won't overwrite an existing destination: {final_path}",
            details={"action_id": pending["action_id"], "destination_path": str(final_path)},
        )
    try:
        shutil.move(str(source), str(final_path))
    except Exception as exc:
        return OperatorActionResult(
            ok=False,
            status="execution_failed",
            response_text=f"I couldn't move {source} to {final_path}: {exc}",
            details={
                "action_id": pending["action_id"],
                "source_path": str(source),
                "destination_path": str(final_path),
                "error": str(exc),
            },
        )
    verified = final_path.exists() and not source.exists()
    _mark_action_executed(
        pending["action_id"],
        result={
            "source_path": str(source),
            "destination_path": str(final_path),
            "verified": verified,
        },
    )
    learned_plan = Plan(
        summary="Verified bounded file move/archive workflow with preview, approval, relocation, and post-move verification.",
        abstract_steps=[
            "validate the requested source and destination paths",
            "prepare a move preview",
            "require explicit user approval",
            "move the source into the approved destination",
            "verify the new path exists and the original path is gone",
        ],
        confidence=0.9 if verified else 0.72,
        risk_flags=[],
        simulation_steps=[],
        safe_actions=[{"action": "move_path", "source_path": str(source), "destination_path": str(final_path)}],
        reads_workspace=True,
        writes_workspace=True,
        requests_network=False,
        requests_subprocess=False,
        evidence_sources=["local_operator:move_path"],
    )
    audit_logger.log(
        "operator_action_move_path",
        target_id=task_id,
        target_type="task",
        details={
            "action_id": pending["action_id"],
            "source_path": str(source),
            "destination_path": str(final_path),
            "verified": verified,
        },
    )
    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=f"Move finished. {source.name} is now at {final_path}",
        details={
            "action_id": pending["action_id"],
            "source_path": str(source),
            "destination_path": str(final_path),
            "verified": verified,
        },
        learned_plan=learned_plan,
    )


def _handle_schedule_calendar_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
) -> OperatorActionResult:
    pending = _load_pending_action(
        session_id=session_id,
        action_kind="schedule_calendar_event",
        action_id=intent.action_id,
    )
    if pending is None:
        parsed = _parse_calendar_request(intent.raw_text)
        if not parsed:
            return OperatorActionResult(
                ok=False,
                status="invalid_request",
                response_text=(
                    "I can schedule a meeting, but I need a title and time. "
                    'Use a format like: schedule a meeting "Ops Sync" on 2026-03-08 15:30 for 45m'
                ),
                details={},
            )
        action_id = _create_pending_action(
            session_id=session_id,
            task_id=task_id,
            action_kind="schedule_calendar_event",
            scope=parsed,
        )
        gate = ExecutionGate.evaluate_local_action(
            "schedule_calendar_event",
            destructive=True,
            user_approved=bool(intent.approval_requested),
            writes_workspace=True,
        )
        if gate.mode in {"execute", "sandbox"} and not gate.requires_user_approval:
            return _handle_schedule_calendar_event(
                OperatorActionIntent(
                    kind="schedule_calendar_event",
                    approval_requested=True,
                    action_id=action_id,
                    raw_text=intent.raw_text,
                ),
                task_id=task_id,
                session_id=session_id,
            )
        return OperatorActionResult(
            ok=True,
            status="approval_required",
            response_text=(
                f"Meeting preview ready.\n"
                f"- Title: {parsed['title']}\n"
                f"- Starts: {parsed['start_iso']}\n"
                f"- Ends: {parsed['end_iso']}\n"
                f"- Calendar outbox: {parsed['outbox_dir']}\n\n"
                f"Reply with: approve calendar {action_id}"
            ),
            details={"action_id": action_id, **parsed},
        )

    gate = ExecutionGate.evaluate_local_action(
        "schedule_calendar_event",
        destructive=True,
        user_approved=bool(intent.approval_requested),
        writes_workspace=True,
    )
    if gate.requires_user_approval and not intent.approval_requested:
        return OperatorActionResult(
            ok=False,
            status="approval_required",
            response_text=(
                "Calendar event is ready but still needs explicit approval. "
                f"Reply with: approve calendar {pending['action_id']}"
            ),
            details={"action_id": pending["action_id"]},
        )
    if gate.mode not in {"execute", "sandbox"}:
        return OperatorActionResult(
            ok=False,
            status="blocked",
            response_text=f"I can't create that calendar event right now: {gate.reason}",
            details={"gate_mode": gate.mode},
        )

    scope = json.loads(str(pending.get("scope_json") or "{}"))
    title = str(scope.get("title") or "NULLA Meeting")
    start_iso = str(scope.get("start_iso") or "")
    end_iso = str(scope.get("end_iso") or "")
    outbox_dir = Path(str(scope.get("outbox_dir") or data_path("calendar_outbox"))).expanduser()
    outbox_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-").lower() or "meeting"
    filename = f"{slug}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.ics"
    ics_path = outbox_dir / filename
    ics_path.write_text(_render_ics(title=title, start_iso=start_iso, end_iso=end_iso), encoding="utf-8")
    _mark_action_executed(
        pending["action_id"],
        result={
            "title": title,
            "start_iso": start_iso,
            "end_iso": end_iso,
            "ics_path": str(ics_path),
        },
    )
    learned_plan = Plan(
        summary="Verified calendar-event creation workflow with preview, policy-aware approval, .ics emission, and path verification.",
        abstract_steps=[
            "parse requested meeting title and time",
            "prepare calendar preview",
            "request approval only when the current autonomy mode requires it",
            "emit .ics file into calendar outbox",
            "verify event artifact path exists",
        ],
        confidence=0.91,
        risk_flags=[],
        simulation_steps=[],
        safe_actions=[{"action": "schedule_calendar_event", "title": title, "ics_path": str(ics_path)}],
        reads_workspace=False,
        writes_workspace=True,
        requests_network=False,
        requests_subprocess=False,
        evidence_sources=["local_operator:schedule_calendar_event"],
    )
    audit_logger.log(
        "operator_action_schedule_calendar_event",
        target_id=task_id,
        target_type="task",
        details={"action_id": pending["action_id"], "ics_path": str(ics_path), "title": title},
    )
    return OperatorActionResult(
        ok=True,
        status="executed",
        response_text=f"Calendar event created. ICS written to {ics_path}",
        details={"action_id": pending["action_id"], "ics_path": str(ics_path), "title": title},
        learned_plan=learned_plan,
    )


def _resolve_target_path(raw_path: str | None) -> Path:
    if raw_path:
        return Path(os.path.expandvars(raw_path)).expanduser()
    if os.name == "nt":
        drive = os.environ.get("SystemDrive", "C:")
        return Path(f"{drive}\\")
    return Path.home()


def _inspect_storage(target: Path) -> dict[str, Any]:
    try:
        disk_total, _used, disk_free = shutil.disk_usage(target)
    except Exception:
        disk_total, disk_free = 0, 0

    deadline = time.monotonic() + 2.0
    top_entries: list[dict[str, Any]] = []
    try:
        children = list(target.iterdir())
    except Exception:
        children = []

    for child in children[:32]:
        size_info = _path_size(child, deadline=deadline)
        top_entries.append(
            {
                "name": child.name or str(child),
                "path": str(child),
                "bytes": int(size_info["bytes"]),
                "approximate": bool(size_info["approximate"]),
            }
        )
        if time.monotonic() >= deadline:
            break

    top_entries.sort(key=lambda row: int(row["bytes"]), reverse=True)
    return {
        "disk_total_bytes": int(disk_total),
        "disk_free_bytes": int(disk_free),
        "top_entries": top_entries[:8],
    }


def _path_size(path: Path, *, deadline: float, max_entries: int = 6000) -> dict[str, Any]:
    approximate = False
    total = 0
    scanned = 0
    try:
        if path.is_symlink():
            return {"bytes": 0, "approximate": False}
        if path.is_file():
            return {"bytes": int(path.stat().st_size), "approximate": False}
    except Exception:
        return {"bytes": 0, "approximate": True}

    for root, dirs, files in os.walk(path, topdown=True):
        if time.monotonic() >= deadline or scanned >= max_entries:
            approximate = True
            break
        dirs[:] = [name for name in dirs if not name.startswith(".nulla_local")]
        for name in files:
            file_path = Path(root) / name
            try:
                if file_path.is_symlink():
                    continue
                total += int(file_path.stat().st_size)
            except Exception:
                approximate = True
            scanned += 1
            if time.monotonic() >= deadline or scanned >= max_entries:
                approximate = True
                break
        if approximate:
            break
    return {"bytes": total, "approximate": approximate}


def _process_inspection_available() -> bool:
    if os.name == "nt":
        return True
    return shutil.which("ps") is not None


def _service_inspection_available() -> bool:
    if os.name == "nt":
        return True
    return bool(shutil.which("systemctl") or shutil.which("launchctl"))


def _inspect_processes() -> list[dict[str, Any]]:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if completed.returncode != 0:
            return []
        rows: list[dict[str, Any]] = []
        for record in csv.reader(completed.stdout.splitlines()):
            if len(record) < 2:
                continue
            rows.append({"pid": record[1], "name": record[0], "cpu_percent": 0.0, "mem_percent": 0.0})
        return rows[:8]
    completed = subprocess.run(
        ["ps", "-Ao", "pid=,%cpu=,%mem=,comm="],
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
    )
    if completed.returncode != 0:
        return []
    rows = []
    for line in completed.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            rows.append(
                {
                    "pid": parts[0],
                    "cpu_percent": float(parts[1]),
                    "mem_percent": float(parts[2]),
                    "name": parts[3],
                }
            )
        except ValueError:
            continue
    rows.sort(key=lambda row: (row["cpu_percent"] + row["mem_percent"], row["cpu_percent"]), reverse=True)
    return rows[:8]


def _inspect_services() -> list[dict[str, Any]]:
    if os.name == "nt":
        completed = subprocess.run(
            ["sc", "query", "type=", "service", "state=", "all"],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        if completed.returncode != 0:
            return []
        rows: list[dict[str, Any]] = []
        current: dict[str, str] | None = None
        for line in completed.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("SERVICE_NAME:"):
                if current:
                    rows.append(current)
                current = {"name": stripped.split(":", 1)[1].strip(), "state": "unknown", "detail": ""}
                continue
            if current is None:
                continue
            if stripped.startswith("STATE"):
                state_text = stripped.split(":", 1)[1].strip()
                current["state"] = state_text
                continue
            if stripped.startswith("DISPLAY_NAME:"):
                current["detail"] = stripped.split(":", 1)[1].strip()
        if current:
            rows.append(current)
        return rows[:12]

    if shutil.which("systemctl"):
        completed = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        if completed.returncode == 0:
            rows = []
            for line in completed.stdout.splitlines():
                parts = line.split(None, 4)
                if len(parts) < 5:
                    continue
                rows.append(
                    {
                        "name": parts[0],
                        "state": parts[2],
                        "detail": parts[4].strip(),
                    }
                )
            rows.sort(key=lambda row: (row["state"] != "running", row["name"]))
            return rows[:12]

    if shutil.which("launchctl"):
        completed = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        if completed.returncode != 0:
            return []
        rows = []
        for line in completed.stdout.splitlines()[1:]:
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            pid, status, label = parts
            state = "running" if pid != "-" else "loaded"
            rows.append({"name": label.strip(), "state": state, "detail": f"pid={pid} status={status}"})
        return rows[:12]

    return []


def _discord_available() -> bool:
    return bool(
        str(os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
        or str(os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    )


def _telegram_available() -> bool:
    return bool(
        str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        and (
            str(os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
            or str(os.environ.get("TELEGRAM_CHAT_IDS_JSON") or "").strip()
        )
    )


def _parse_calendar_request(text: str) -> dict[str, Any] | None:
    title_match = _QUOTED_PATH_RE.search(text)
    title = title_match.group(1).strip() if title_match else "NULLA Meeting"
    now = datetime.now().astimezone()
    start_dt: datetime | None = None

    iso_match = _ISO_DATETIME_RE.search(text)
    if iso_match:
        date_part = iso_match.group(1)
        time_part = iso_match.group(2) or "09:00"
        start_dt = datetime.fromisoformat(f"{date_part}T{time_part}").replace(tzinfo=now.tzinfo)
    else:
        time_match = _TIME_RE.search(text)
        if time_match and ("today" in text.lower() or "tomorrow" in text.lower()):
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            meridiem = str(time_match.group(3) or "").lower()
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            target_day = now.date() + timedelta(days=1 if "tomorrow" in text.lower() else 0)
            start_dt = datetime.combine(target_day, datetime.min.time(), tzinfo=now.tzinfo).replace(
                hour=hour,
                minute=minute,
            )
    if start_dt is None:
        return None

    duration_match = _DURATION_RE.search(text)
    duration_value = int(duration_match.group(1)) if duration_match else 30
    duration_unit = str(duration_match.group(2) or "m").lower() if duration_match else "m"
    if duration_unit.startswith("h"):
        duration_minutes = duration_value * 60
    else:
        duration_minutes = duration_value
    end_dt = start_dt + timedelta(minutes=max(15, duration_minutes))
    outbox_dir = data_path("calendar_outbox")
    return {
        "title": title,
        "start_iso": start_dt.isoformat(),
        "end_iso": end_dt.isoformat(),
        "duration_minutes": max(15, duration_minutes),
        "outbox_dir": str(outbox_dir),
    }


def _render_ics(*, title: str, start_iso: str, end_iso: str) -> str:
    start_dt = datetime.fromisoformat(start_iso).astimezone(timezone.utc)
    end_dt = datetime.fromisoformat(end_iso).astimezone(timezone.utc)
    uid = f"{uuid.uuid4()}@nulla.local"
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start = start_dt.strftime("%Y%m%dT%H%M%SZ")
    end = end_dt.strftime("%Y%m%dT%H%M%SZ")
    safe_title = title.replace("\\", "\\\\").replace(",", r"\,").replace(";", r"\;").replace("\n", r"\n")
    return (
        "BEGIN:VCALENDAR\n"
        "VERSION:2.0\n"
        "PRODID:-//NULLA//Closed Test//EN\n"
        "BEGIN:VEVENT\n"
        f"UID:{uid}\n"
        f"DTSTAMP:{dtstamp}\n"
        f"DTSTART:{start}\n"
        f"DTEND:{end}\n"
        f"SUMMARY:{safe_title}\n"
        "END:VEVENT\n"
        "END:VCALENDAR\n"
    )


def _parse_move_request(
    text: str,
    *,
    fallback_source: str | None = None,
    fallback_destination: str | None = None,
) -> dict[str, str] | None:
    lowered = str(text or "").lower()
    source = str(fallback_source or "").strip()
    destination = str(fallback_destination or "").strip()
    quoted = _extract_quoted_values(text)
    if quoted:
        source = source or quoted[0]
        if len(quoted) >= 2:
            destination = destination or quoted[1]
    if not source:
        return None
    if not destination:
        if "archive" not in lowered:
            return None
        destination = str(data_path("archive_outbox"))
    return {
        "source_path": str(Path(os.path.expandvars(source)).expanduser()),
        "destination_dir": str(Path(os.path.expandvars(destination)).expanduser()),
    }


def _candidate_cleanup_roots(target_path: str | None) -> list[Path]:
    roots: list[Path] = []
    for env_name in ("TMPDIR", "TMP", "TEMP"):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            roots.append(Path(value).expanduser())
    roots.append(Path(tempfile.gettempdir()).expanduser())
    if target_path:
        target = Path(os.path.expandvars(target_path)).expanduser()
        if _is_temp_cleanup_path(target):
            roots.insert(0, target)

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        if not root.exists() or not root.is_dir():
            continue
        if not _is_temp_cleanup_path(root):
            continue
        seen.add(key)
        deduped.append(root)
    return deduped[:6]


def _is_temp_cleanup_path(path: Path) -> bool:
    if not path:
        return False
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    if _path_is_denied(resolved):
        return False

    name = resolved.name.lower()
    if name in _TEMPISH_NAMES:
        return True

    home = Path.home().resolve()
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
    except Exception:
        temp_root = resolved

    return _is_relative_to(resolved, temp_root) or (_is_relative_to(resolved, home) and name in _TEMPISH_NAMES)


def _operator_safe_path(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except Exception:
        resolved = path.expanduser()
    if _path_is_denied(resolved):
        return False
    allowed_roots = [Path.home().resolve()]
    try:
        allowed_roots.append(Path(tempfile.gettempdir()).resolve())
    except Exception:
        pass
    try:
        allowed_roots.append(data_path().parent.resolve())
    except Exception:
        pass
    return any(_is_relative_to(resolved, root) or resolved == root for root in allowed_roots)


def _validate_move_scope(source: Path, destination_dir: Path) -> str | None:
    if not source.exists():
        return f"I can't move this path because it does not exist: {source}"
    if not _operator_safe_path(source):
        return f"I won't move protected or out-of-scope paths: {source}"
    destination_probe = destination_dir if destination_dir.exists() else destination_dir.parent
    if not _operator_safe_path(destination_probe):
        return f"I won't move content into a protected or out-of-scope destination: {destination_dir}"
    final_path = _resolved_move_target(source, destination_dir)
    if final_path == source:
        return "The requested source and destination resolve to the same path."
    if _is_relative_to(final_path, source):
        return "I won't move a folder into itself."
    return None


def _resolved_move_target(source: Path, destination_dir: Path) -> Path:
    return destination_dir / source.name


def _path_is_denied(path: Path) -> bool:
    normalized = str(path).replace("\\", "/").lower()
    for pattern in policy_engine.get("filesystem.deny_paths", []) or []:
        probe = str(pattern or "").replace("\\", "/").lower()
        if not probe:
            continue
        if "*" in probe:
            if fnmatch.fnmatch(normalized, probe):
                return True
            continue
        if normalized == probe or normalized.startswith(probe.rstrip("/") + "/"):
            return True
    return False


def _delete_children(root: Path) -> dict[str, Any]:
    deleted_files = 0
    deleted_dirs = 0
    errors: list[str] = []
    for child in list(root.iterdir()):
        try:
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
                deleted_files += 1
            elif child.is_dir():
                shutil.rmtree(child)
                deleted_dirs += 1
        except Exception as exc:
            errors.append(f"{child}: {exc}")
    return {
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "errors": errors,
    }


def _create_pending_action(*, session_id: str, task_id: str, action_kind: str, scope: dict[str, Any]) -> str:
    now = _utcnow()
    action_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE operator_action_requests
            SET status = 'superseded', updated_at = ?
            WHERE session_id = ? AND action_kind = ? AND status = 'pending_approval'
            """,
            (now, session_id, action_kind),
        )
        conn.execute(
            """
            INSERT INTO operator_action_requests (
                action_id, session_id, task_id, action_kind, scope_json,
                result_json, status, created_at, updated_at, executed_at
            ) VALUES (?, ?, ?, ?, ?, '{}', 'pending_approval', ?, ?, NULL)
            """,
            (action_id, session_id, task_id, action_kind, json.dumps(scope, sort_keys=True), now, now),
        )
        conn.commit()
        return action_id
    finally:
        conn.close()


def _load_pending_action(*, session_id: str, action_kind: str, action_id: str | None = None) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        if action_id:
            row = conn.execute(
                """
                SELECT *
                FROM operator_action_requests
                WHERE action_id = ? AND session_id = ? AND action_kind = ? AND status = 'pending_approval'
                LIMIT 1
                """,
                (action_id, session_id, action_kind),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT *
                FROM operator_action_requests
                WHERE session_id = ? AND action_kind = ? AND status = 'pending_approval'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id, action_kind),
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _mark_action_executed(action_id: str, *, result: dict[str, Any]) -> None:
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE operator_action_requests
            SET status = 'executed',
                result_json = ?,
                updated_at = ?,
                executed_at = ?
            WHERE action_id = ?
            """,
            (json.dumps(result, sort_keys=True), now, now, action_id),
        )
        conn.commit()
    finally:
        conn.close()


def _extract_path(text: str) -> str | None:
    match = _QUOTED_PATH_RE.search(text)
    if match:
        return match.group(1).strip()
    match = _WINDOWS_PATH_RE.search(text)
    if match:
        return match.group(1).strip().rstrip(".,")
    match = _POSIX_PATH_RE.search(text)
    if match:
        return match.group(1).strip().rstrip(".,")
    return None


def _extract_quoted_values(text: str) -> list[str]:
    return [match.group(1).strip() for match in _QUOTED_PATH_RE.finditer(text or "") if match.group(1).strip()]


def _extract_action_id(text: str) -> str | None:
    match = _APPROVAL_ID_RE.search(text)
    if not match:
        return None
    return match.group(1)


def _fmt_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{int(value)} B"


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except Exception:
        return False


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
