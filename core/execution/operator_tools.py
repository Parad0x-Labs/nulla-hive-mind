from __future__ import annotations

from typing import Any

from core.execution.models import ToolIntentExecution, _tool_observation
from core.local_operator_actions import OperatorActionIntent, dispatch_operator_action


def execute_operator_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str,
    session_id: str,
    dispatch_operator_action_fn=dispatch_operator_action,
) -> ToolIntentExecution:
    operator_kind = intent.split(".", 1)[1]
    operator_intent = build_operator_action_intent(operator_kind, arguments)
    dispatch = dispatch_operator_action_fn(
        operator_intent,
        task_id=task_id,
        session_id=session_id,
    )
    if dispatch.status == "executed":
        mode = "tool_executed"
    elif dispatch.status in {"reported", "approval_required"}:
        mode = "tool_preview"
    else:
        mode = "tool_failed"
    return ToolIntentExecution(
        handled=True,
        ok=bool(dispatch.ok),
        status=str(dispatch.status),
        response_text=str(dispatch.response_text or ""),
        mode=mode,
        tool_name=intent,
        details={
            **dict(dispatch.details or {}),
            "observation": _tool_observation(
                intent=intent,
                tool_surface="local_operator",
                ok=bool(dispatch.ok),
                status=str(dispatch.status),
                details=dict(dispatch.details or {}),
                response_preview=str(dispatch.response_text or "")[:280],
            ),
        },
        learned_plan=dispatch.learned_plan,
    )


def build_operator_action_intent(operator_kind: str, arguments: dict[str, Any]) -> OperatorActionIntent:
    target_path = str(arguments.get("target_path") or arguments.get("path") or "").strip() or None
    destination_path = str(arguments.get("destination_path") or arguments.get("destination_dir") or "").strip() or None
    raw_text = ""
    if operator_kind == "move_path":
        source = str(arguments.get("source_path") or target_path or "").strip()
        dest = str(destination_path or "").strip()
        raw_text = f'move "{source}" to "{dest}"'.strip()
        target_path = source or None
    elif operator_kind == "schedule_calendar_event":
        title = str(arguments.get("title") or "NULLA Meeting").strip()
        start_iso = str(arguments.get("start_iso") or "").strip()
        duration_minutes = max(15, int(arguments.get("duration_minutes") or 30))
        raw_text = f'schedule a meeting "{title}" on {start_iso} for {duration_minutes}m'.strip()
    elif operator_kind == "cleanup_temp_files" and target_path:
        raw_text = f'clean temp files in "{target_path}"'
    elif operator_kind == "inspect_disk_usage" and target_path:
        raw_text = f'find disk bloat in "{target_path}"'
    return OperatorActionIntent(
        kind=operator_kind,
        target_path=target_path,
        destination_path=destination_path,
        approval_requested=False,
        action_id=str(arguments.get("action_id") or "").strip() or None,
        raw_text=raw_text,
    )
