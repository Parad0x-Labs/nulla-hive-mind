from __future__ import annotations

import json
import re
from typing import Any

_ORCHESTRATION_LEAK_MARKERS = (
    '"schema": "nulla.task_envelope.v1"',
    "task_envelope",
    "tool_permissions",
    "model_constraints",
    "latency_budget",
    "quality_target",
    "allowed_side_effects",
    "required_receipts",
    "merge_strategy",
    "cancellation_policy",
    "privacy_class",
    "routing_requirements",
    "rejected_candidates",
    "selection_notes",
    "provider_capability_truth",
    "queue_pressure_strategy",
    "required_locality",
    "preferred_locality",
    "preferred_provider_role",
    "effective_swarm_size",
    "capacity_backoff_applied",
    "capacity_backoff_notes",
    "capacity_blocked",
    "capacity_state",
    "scheduled_children",
    "merged_result",
    "step_results",
    "graph",
)
_ENVELOPE_ROLE_MARKERS = (
    "queen envelope",
    "coder envelope",
    "verifier envelope",
    "researcher envelope",
    "memory clerk envelope",
    "memory_clerk envelope",
    "narrator envelope",
)
_TRACEBACK_LINE_RE = re.compile(r'^\s*File\s+"[^"]+",\s+line\s+\d+', re.IGNORECASE | re.MULTILINE)


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
        started_research_match = re.match(
            r"^Started research on\s+`?([^`]+?)`?\.?$",
            text,
            flags=re.IGNORECASE,
        )
        if started_research_match:
            title = " ".join(str(started_research_match.group(1) or "").split()).strip()
            if title:
                text = f"Started Hive research on `{title}`."
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
        "tool_failed",
        '"mode": "tool_failed"',
        '"status": "missing_intent"',
    )
    if any(marker in lowered for marker in forbidden):
        if response_class == agent.ResponseClass.UTILITY_ANSWER:
            return "I couldn't answer that utility request cleanly."
        if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
            return "I couldn't map that cleanly to a real action."
        return "I couldn't resolve that cleanly."
    if looks_like_runtime_traceback(sanitized):
        if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
            return "I hit an internal failure while handling that request."
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
    orchestration_safe_text = humanize_orchestration_leak(
        agent,
        sanitized,
        response_class=response_class,
    )
    if orchestration_safe_text is not None:
        return orchestration_safe_text
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


def looks_like_runtime_traceback(text: str) -> bool:
    lowered = str(text or "").lower()
    if "traceback (most recent call last)" in lowered:
        return True
    if _TRACEBACK_LINE_RE.search(str(text or "")):
        return True
    return lowered.count("\n") >= 2 and "error:" in lowered and ("line " in lowered or "exception" in lowered)


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


def humanize_orchestration_leak(
    agent: Any,
    text: str,
    *,
    response_class: Any,
) -> str | None:
    clean = str(text or "").strip()
    if not clean:
        return None
    lowered = clean.lower()
    if lowered.startswith("search matches for ") or lowered.startswith("file `") or lowered.startswith("local file `"):
        return None
    if not any(marker in lowered for marker in _ORCHESTRATION_LEAK_MARKERS) and not any(
        marker in lowered for marker in _ENVELOPE_ROLE_MARKERS
    ):
        return None
    if "missing required receipts" in lowered:
        return "I finished part of that run, but I couldn't close it out because the required proof receipts were missing."
    if "not allowed to run" in lowered or "not allowed to trigger" in lowered:
        return "I couldn't complete that bounded worker step because its permissions did not allow the requested action."
    if (
        "capacity_blocked" in lowered
        or "provider-capacity policy" in lowered
        or ("requires_local_provider" in lowered and "capacity_state" in lowered)
    ):
        return "I couldn't run that bounded worker step because the available provider lane did not meet the task's local execution requirements."
    if "has no child envelopes" in lowered or "has no runtime tool steps" in lowered:
        return "I couldn't continue that bounded run because it did not contain executable steps."
    if "failed to merge child results" in lowered:
        return "I couldn't merge the worker results into a clean final answer."
    if (
        "routing_requirements" in lowered
        or "rejected_candidates" in lowered
        or "selection_notes" in lowered
        or "provider_capability_truth" in lowered
    ):
        if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
            return "I couldn't find a provider lane that satisfied the task's routing and execution requirements cleanly."
        return "I finished the work and stripped the internal routing details from the reply."
    if "capacity_backoff_applied" in lowered or "skipped_saturated_candidates" in lowered or "reduced_to_single_degraded_lane" in lowered:
        return "I finished the work using the least-busy available provider lane."
    if "completed merge" in lowered or (" envelope" in lowered and "completed" in lowered):
        return "I finished the bounded multi-step run."
    if response_class == agent.ResponseClass.UTILITY_ANSWER:
        return "I couldn't surface that utility result cleanly."
    if response_class in {agent.ResponseClass.TASK_FAILED_USER_SAFE, agent.ResponseClass.SYSTEM_ERROR_USER_SAFE}:
        return "I couldn't complete that bounded multi-step run cleanly."
    return "I finished the work, but I'm stripping internal orchestration details from the reply."


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
