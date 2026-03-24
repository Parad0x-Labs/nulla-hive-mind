from __future__ import annotations

import re
from typing import Any


def append_tool_result_to_source_context(
    agent: Any,
    source_context: dict[str, Any] | None,
    *,
    execution: Any,
    tool_name: str,
) -> dict[str, Any]:
    updated = dict(source_context or {})
    history = list(updated.get("conversation_history") or [])
    observation_message = tool_history_observation_message(
        agent,
        execution=execution,
        tool_name=tool_name,
    )
    if history and history[-1] == observation_message:
        updated["conversation_history"] = history[-12:]
        return updated
    history.append(observation_message)
    updated["conversation_history"] = history[-12:]
    return updated


def normalize_tool_history_message(agent: Any, item: dict[str, Any]) -> dict[str, str]:
    role = str(item.get("role") or "").strip().lower()
    content = str(item.get("content") or "").strip()
    if role != "assistant" or not content.startswith("Real tool result from `"):
        return {"role": role, "content": content}
    match = re.match(r"^Real tool result from `([^`]+)`:\s*(.*)$", content, re.DOTALL)
    if not match:
        return {"role": role, "content": content}
    tool_name = str(match.group(1) or "").strip() or "tool"
    response_text = str(match.group(2) or "").strip()
    observation = {
        "schema": "tool_observation_v1",
        "intent": tool_name,
        "tool_surface": tool_surface_for_history(tool_name),
        "ok": True,
        "status": "executed",
        "response_preview": response_text[:1800] if response_text else "No tool output returned.",
    }
    return {
        "role": "user",
        "content": agent._tool_history_observation_prompt(observation),
    }


def tool_surface_for_history(tool_name: str) -> str:
    lowered = str(tool_name or "").strip().lower()
    if lowered.startswith("web.") or lowered.startswith("browser."):
        return "web"
    if lowered.startswith("workspace."):
        return "workspace"
    if lowered.startswith("sandbox."):
        return "sandbox"
    if lowered.startswith("operator."):
        return "local_operator"
    if lowered.startswith("hive."):
        return "hive"
    return "runtime_tool"


def tool_history_observation_payload(
    *,
    execution: Any,
    tool_name: str,
) -> dict[str, Any]:
    details = dict(getattr(execution, "details", {}) or {})
    observation = details.get("observation")
    if isinstance(observation, dict) and observation:
        payload = dict(observation)
    else:
        response_text = str(getattr(execution, "response_text", "") or "").strip()
        payload = {
            "schema": "tool_observation_v1",
            "intent": str(tool_name or getattr(execution, "tool_name", "") or "tool").strip() or "tool",
            "tool_surface": tool_surface_for_history(str(tool_name or getattr(execution, "tool_name", "") or "tool")),
            "ok": bool(getattr(execution, "ok", False)),
            "status": str(getattr(execution, "status", "") or "executed").strip() or "executed",
            "response_preview": response_text[:1800] if response_text else "No tool output returned.",
        }
    payload.setdefault("mode", str(getattr(execution, "mode", "") or "").strip())
    if not payload.get("response_preview"):
        response_text = str(getattr(execution, "response_text", "") or "").strip()
        if response_text:
            payload["response_preview"] = response_text[:1800]
    return payload


def tool_history_observation_message(
    agent: Any,
    *,
    execution: Any,
    tool_name: str,
) -> dict[str, str]:
    observation = tool_history_observation_payload(
        execution=execution,
        tool_name=tool_name,
    )
    return {
        "role": "user",
        "content": agent._tool_history_observation_prompt(observation),
    }
