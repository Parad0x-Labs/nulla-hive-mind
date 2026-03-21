from __future__ import annotations

import json
import re
from typing import Any


def turn_result(
    chat_turn_result_cls: type[Any],
    text: str,
    response_class: Any,
    *,
    workflow_summary: str = "",
    debug_origin: str | None = None,
    allow_planner_style: bool = False,
) -> Any:
    return chat_turn_result_cls(
        text=str(text or "").strip(),
        response_class=response_class,
        workflow_summary=str(workflow_summary or "").strip(),
        debug_origin=debug_origin,
        allow_planner_style=bool(allow_planner_style),
    )


def decorate_chat_response(
    agent: Any,
    response: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    workflow_summary: str = "",
    include_hive_footer: bool | None = None,
) -> str:
    result = response if hasattr(response, "response_class") else agent._turn_result(
        str(response or ""),
        agent.ResponseClass.GENERIC_CONVERSATION,
        workflow_summary=workflow_summary,
    )
    clean_text = agent._shape_user_facing_text(result)
    if agent._should_show_workflow_for_result(result, source_context=source_context):
        decorated = agent._maybe_attach_workflow(
            clean_text,
            result.workflow_summary,
            source_context=source_context,
        )
    else:
        decorated = clean_text
    footer_allowed = (
        agent._should_attach_hive_footer(result, source_context=source_context)
        if include_hive_footer is None
        else bool(include_hive_footer)
    )
    hive_footer = agent._maybe_hive_footer(session_id=session_id, source_context=source_context) if footer_allowed else ""
    if hive_footer:
        decorated = agent._append_footer(decorated, prefix="Hive", footer=hive_footer)
    return decorated


def shape_user_facing_text(agent: Any, result: Any) -> str:
    text = agent._sanitize_user_chat_text(
        result.text,
        response_class=result.response_class,
        allow_planner_style=result.allow_planner_style,
    )
    if result.response_class == agent.ResponseClass.TASK_STARTED:
        text = re.sub(
            r"^Autonomous research on\s+`?([^`]+)`?\s+packed\s+\d+\s+research queries,\s*\d+\s+candidate notes,\s*and\s*\d+\s+gate decisions\.?",
            r"Started Hive research on `\1`. First bounded pass is underway.",
            text,
            flags=re.IGNORECASE,
        )
        text = text.replace(
            "The first bounded research pass already ran and posted its result.",
            "The first bounded pass already landed.",
        )
        text = text.replace(
            "This fast reply only means the first bounded research pass finished.",
            "The first bounded pass finished.",
        )
        text = text.replace(
            "Topic stays `researching` because NULLA still needs more evidence before it can honestly mark the task solved.",
            "It is still open because the solve threshold was not met yet.",
        )
        text = text.replace(
            "The research lane is active.",
            "First bounded pass is underway.",
        )
        text = re.sub(r"\bBounded queries run:\s*\d+\.\s*", "", text)
        text = re.sub(r"\bArtifacts packed:\s*\d+\.\s*", "", text)
        text = re.sub(r"\bCandidate notes:\s*\d+\.\s*", "", text)
        return " ".join(text.split()).strip()
    if result.response_class == agent.ResponseClass.RESEARCH_PROGRESS:
        text = re.sub(r"^Research follow-up:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^Research result:\s*", "Here’s what I found: ", text, flags=re.IGNORECASE)
        return " ".join(text.split()).strip()
    return text


def should_show_workflow_for_result(
    agent: Any,
    result: Any,
    *,
    source_context: dict[str, object] | None,
) -> bool:
    if result.response_class in {
        agent.ResponseClass.SMALLTALK,
        agent.ResponseClass.UTILITY_ANSWER,
        agent.ResponseClass.GENERIC_CONVERSATION,
        agent.ResponseClass.TASK_FAILED_USER_SAFE,
        agent.ResponseClass.SYSTEM_ERROR_USER_SAFE,
        agent.ResponseClass.TASK_STARTED,
        agent.ResponseClass.RESEARCH_PROGRESS,
    }:
        return False
    return agent._should_show_workflow_summary(
        response=result.text,
        workflow_summary=result.workflow_summary,
        source_context=source_context,
    )


def sanitize_user_chat_text(
    agent: Any,
    text: str,
    *,
    response_class: Any,
    allow_planner_style: bool = False,
) -> str:
    base_text = str(text or "").strip()
    sanitized = agent._strip_runtime_preamble(base_text, allow_planner_style=False)
    sanitized = agent._strip_planner_leakage(sanitized)
    if agent._contains_generic_planner_scaffold(sanitized):
        if response_class == agent.ResponseClass.UTILITY_ANSWER:
            return "I couldn't answer that utility request cleanly."
        if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
            return "I couldn't map that cleanly to a real action."
        return "I'm here and ready to help. What do you want to do?"
    lowered = sanitized.lower()
    forbidden = (
        "invalid tool payload",
        "missing_intent",
        "i won't fake it",
    )
    if any(marker in lowered for marker in forbidden):
        if response_class == agent.ResponseClass.UTILITY_ANSWER:
            return "I couldn't answer that utility request cleanly."
        if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
            return "I couldn't map that cleanly to a real action."
        return "I couldn't resolve that cleanly."
    degraded_fallback_markers = (
        "couldn't produce a clean final synthesis in this run",
        "couldn't produce a grounded conversational reply in this run",
        "couldn't produce a grounded help reply in this run",
        "couldn't produce a clean final summary",
    )
    if any(marker in lowered for marker in degraded_fallback_markers):
        if response_class == agent.ResponseClass.UTILITY_ANSWER:
            return "I checked, but I couldn't ground a confident answer from the evidence I found."
        if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
            return "I got part of the work done, but I couldn't close it out cleanly."
        return "I couldn't answer that cleanly. Ask it another way."
    return sanitized


def strip_runtime_preamble(text: str, *, allow_planner_style: bool = False) -> str:
    clean = str(text or "").strip()
    if allow_planner_style:
        return clean
    if not clean.startswith("Real steps completed:"):
        return clean
    parts = clean.split("\n\n", 1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip()
    return "I couldn't resolve that cleanly."


def strip_planner_leakage(agent: Any, text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""

    clean = agent._unwrap_summary_or_action_payload(clean)

    lowered = clean.lower()
    if lowered.startswith("workflow:"):
        parts = clean.split("\n\n", 1)
        if len(parts) == 2 and parts[1].strip():
            clean = parts[1].strip()
        else:
            clean = re.sub(r"^workflow:\s*", "", clean, flags=re.IGNORECASE).strip()

    clean = re.sub(r"^here(?:'|’)s what i(?:'|’)d suggest:\s*", "", clean, flags=re.IGNORECASE).strip()
    clean = re.sub(r"^(summary_block|action_plan)\s*:\s*", "", clean, flags=re.IGNORECASE).strip()
    return clean


def contains_generic_planner_scaffold(agent: Any, text: str) -> bool:
    clean = agent._unwrap_summary_or_action_payload(str(text or "").strip())
    if not clean:
        return False
    generic_lines = {"review problem", "choose safe next step", "validate result"}
    normalized_lines: list[str] = []
    for raw_line in clean.splitlines():
        line = re.sub(r"^[\-\*\d\.\)\s]+", "", raw_line).strip().lower()
        line = re.sub(r"[.!?]+$", "", line).strip()
        if line:
            normalized_lines.append(line)
    if not normalized_lines:
        return False
    unique_lines = set(normalized_lines)
    return len(unique_lines) >= 2 and unique_lines.issubset(generic_lines)


def unwrap_summary_or_action_payload(text: str) -> str:
    raw = str(text or "").strip()
    if not (raw.startswith("{") and raw.endswith("}")):
        return raw
    try:
        payload = json.loads(raw)
    except Exception:
        return raw
    if not isinstance(payload, dict):
        return raw

    summary = str(payload.get("summary") or payload.get("message") or "").strip()
    bullet_source = payload.get("bullets") or payload.get("steps") or []
    bullets = [str(item).strip() for item in list(bullet_source) if str(item).strip()]
    lines: list[str] = []
    if summary:
        lines.append(summary)
    lines.extend(f"- {item}" for item in bullets[:6])
    return "\n".join(line for line in lines if line.strip()) or raw
