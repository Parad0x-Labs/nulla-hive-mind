from __future__ import annotations

from typing import Any


def fast_path_response_class(agent: Any, *, reason: str, response: str) -> Any:
    response_class = agent.ResponseClass
    if reason in {"smalltalk_fast_path", "startup_sequence_fast_path"}:
        return response_class.SMALLTALK
    if reason in {
        "date_time_fast_path",
        "direct_math_fast_path",
        "ui_command_fast_path",
        "credit_status_fast_path",
        "memory_command",
        "user_preference_command",
        "live_info_fast_path",
        "capability_truth_query",
        "builder_capability_gap",
        "builder_controller_direct_response",
    }:
        return response_class.UTILITY_ANSWER
    if reason == "help_fast_path":
        return response_class.TASK_SELECTION_CLARIFICATION
    if reason == "evaluative_conversation_fast_path":
        return response_class.GENERIC_CONVERSATION
    if reason == "runtime_resume_missing":
        return response_class.SYSTEM_ERROR_USER_SAFE
    if reason == "hive_activity_command":
        return classify_hive_text_response(agent, response)
    if reason == "hive_research_followup":
        lowered = str(response or "").lower()
        if lowered.startswith("started hive research on") or lowered.startswith("autonomous research on"):
            return response_class.TASK_STARTED
        if lowered.startswith("research follow-up:") or lowered.startswith("research result:"):
            return response_class.RESEARCH_PROGRESS
        if "multiple real hive tasks open" in lowered or "pick one by name" in lowered:
            return response_class.TASK_SELECTION_CLARIFICATION
        if "couldn't map that follow-up" in lowered or "couldn't find an open hive task" in lowered:
            return response_class.TASK_SELECTION_CLARIFICATION
        return response_class.TASK_FAILED_USER_SAFE
    if reason == "hive_status_followup":
        return response_class.TASK_STATUS
    return response_class.GENERIC_CONVERSATION


def classify_hive_text_response(agent: Any, response: str) -> Any:
    lowered = str(response or "").strip().lower()
    response_class = agent.ResponseClass
    if (
        lowered.startswith("hive watcher is not configured")
        or lowered.startswith("i couldn't reach the hive watcher")
        or lowered.startswith("i couldn't reach hive")
        or lowered.startswith("public hive is not enabled")
    ):
        return response_class.TASK_FAILED_USER_SAFE
    if lowered.startswith("available hive tasks right now"):
        return response_class.TASK_LIST
    if lowered.startswith("i couldn't reach the live hive watcher just now, but these are the real hive tasks i already had in session"):
        return response_class.TASK_LIST
    if lowered.startswith("i couldn't reach the live hive watcher, but i can still pull public hive tasks"):
        return response_class.TASK_LIST
    if lowered.startswith("live hive watcher is not configured here, but i can still pull public hive tasks"):
        return response_class.TASK_LIST
    if lowered.startswith("online now:"):
        return response_class.TASK_LIST
    if "pick one by name" in lowered or "point at the task name" in lowered:
        return response_class.TASK_SELECTION_CLARIFICATION
    if lowered.startswith("no open hive tasks"):
        return response_class.TASK_STATUS
    return response_class.TASK_STATUS


def action_response_class(
    agent: Any,
    *,
    reason: str,
    success: bool,
    task_outcome: str | None,
    response: str,
) -> Any:
    lowered = str(response or "").lower()
    response_class = agent.ResponseClass
    if task_outcome == "pending_approval":
        return response_class.APPROVAL_REQUIRED
    if not success:
        return response_class.TASK_FAILED_USER_SAFE
    if "started hive research on" in lowered or lowered.startswith("autonomous research on"):
        return response_class.TASK_STARTED
    if reason.startswith("model_tool_intent_"):
        return response_class.RESEARCH_PROGRESS
    if reason.startswith("hive_topic_create_"):
        return response_class.TASK_STATUS
    return response_class.GENERIC_CONVERSATION


def grounded_response_class(agent: Any, *, gate: Any) -> Any:
    if bool(getattr(gate, "requires_user_approval", False)) or str(getattr(gate, "mode", "") or "").lower() in {
        "approval_required",
        "tool_preview",
    }:
        return agent.ResponseClass.APPROVAL_REQUIRED
    return agent.ResponseClass.GENERIC_CONVERSATION


def tool_intent_direct_message(structured_output: Any) -> str | None:
    if not isinstance(structured_output, dict):
        return None
    intent = str(structured_output.get("intent") or "").strip().lower()
    if intent not in {"respond.direct", "none", "no_tool"}:
        return None
    arguments = structured_output.get("arguments") or {}
    if not isinstance(arguments, dict):
        return None
    message = str(arguments.get("message") or arguments.get("response") or "").strip()
    return message or None
