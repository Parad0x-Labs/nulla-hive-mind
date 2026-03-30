from __future__ import annotations

import re

from .models import OperatorActionIntent

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
_SPACE_WORD_RE = re.compile(r"\bspace\b")


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
        _SPACE_WORD_RE.search(lowered) and any(re.search(rf"\b{token}\b", lowered) for token in ("disk", "drive", "folder", "storage"))
    ):
        return OperatorActionIntent(kind="inspect_disk_usage", target_path=target_path, raw_text=text)

    return None


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
