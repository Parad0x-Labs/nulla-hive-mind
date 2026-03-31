from __future__ import annotations

import re
from typing import Any

from core.reasoning_engine import explicit_planner_style_requested
from core.runtime_execution_tools import looks_like_execution_request
from core.task_router import (
    build_task_envelope_for_request,
    chat_surface_execution_task_class,
    looks_like_explicit_lookup_request,
    looks_like_public_entity_lookup_request,
    model_execution_profile,
)


def model_routing_profile(
    agent: Any,
    *,
    user_input: str,
    classification: dict[str, Any],
    interpretation: Any,
    source_context: dict[str, object] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    routed = dict(classification or {})
    is_chat_surface = agent._is_chat_truth_surface(source_context)
    planner_style_requested = bool(is_chat_surface and explicit_planner_style_requested(user_input))
    if is_chat_surface:
        routed["task_class"] = chat_surface_execution_task_class(
            str(classification.get("task_class") or "unknown"),
            user_input=user_input,
            context=getattr(interpretation, "as_context", lambda: {})(),
        )
        routed["routing_origin_task_class"] = str(classification.get("task_class") or "unknown")
        routed["planner_style_requested"] = planner_style_requested
    profile = model_execution_profile(
        str(routed.get("task_class") or "unknown"),
        chat_surface=is_chat_surface,
        planner_style_requested=planner_style_requested,
    )
    envelope = build_task_envelope_for_request(
        user_input,
        context={
            **getattr(interpretation, "as_context", lambda: {})(),
            **routed,
            "share_scope": str((source_context or {}).get("share_scope") or "local_only"),
        },
        task_id=str((source_context or {}).get("task_id") or ""),
        parent_task_id=str((source_context or {}).get("parent_task_id") or ""),
        chat_surface=is_chat_surface,
        planner_style_requested=planner_style_requested,
    )
    routed["task_role"] = envelope.role
    profile["task_envelope"] = envelope.to_dict()
    profile["task_role"] = envelope.role
    return routed, profile


def explicit_runtime_workflow_request(*, user_input: str, task_class: str) -> bool:
    text = " ".join(str(user_input or "").split()).strip()
    if not text:
        return False
    lowered = f" {text.lower()} "
    if looks_like_execution_request(text, task_class="unknown"):
        return True
    if any(
        marker in lowered
        for marker in (
            " what branch and commit ",
            " current branch ",
            " head commit ",
            " recent commits ",
            " git summary ",
            " git activity ",
            " git status ",
            " working tree ",
            " how many branches ",
            " how many commits ",
            " branch count ",
            " commit count ",
            " commits today ",
            " commits yesterday ",
        )
    ):
        return True
    if re.search(r"\b(?:last|recent)\s+\d+\s+commits?\b", lowered):
        return True
    if any(marker in lowered for marker in (" retry ", " rerun ", " rerun it ", " run tests ", " inspect logs ")):
        return True
    if any(marker in lowered for marker in (" find ", " inspect ", " trace ", " locate ", " search ", " read ", " open ")) and any(
        marker in lowered
        for marker in (
            " repo ",
            " repository ",
            " workspace ",
            " code ",
            " file ",
            " files ",
            " wiring ",
            " path ",
            " line ",
            " lines ",
            " function ",
            " symbol ",
            " import ",
        )
    ):
        return True
    if ("http://" in lowered or "https://" in lowered) and any(marker in lowered for marker in (" open ", " fetch ", " browse ", " render ")):
        return True
    return bool(
        str(task_class or "").strip().lower() == "integration_orchestration"
        and any(
            marker in lowered
            for marker in (" write the files ", " edit the files ", " patch the files ", " create the files ", " generate the files ")
        )
    )


def should_keep_ai_first_chat_lane(
    agent: Any,
    *,
    user_input: str,
    classification: dict[str, Any],
    interpretation: Any,
    source_context: dict[str, object] | None,
    checkpoint_state: dict[str, Any] | None,
) -> bool:
    if not agent._is_chat_truth_surface(source_context):
        return False
    checkpoint_state = dict(checkpoint_state or {})
    if checkpoint_state.get("executed_steps") or checkpoint_state.get("pending_tool_payload") or checkpoint_state.get(
        "last_tool_payload"
    ):
        return False
    if agent._looks_like_explicit_resume_request(user_input):
        return False
    if agent._live_info_mode(user_input, interpretation=interpretation):
        return True
    task_class = str(classification.get("task_class") or "unknown")
    routed_task_class = chat_surface_execution_task_class(
        task_class,
        user_input=user_input,
        context=getattr(interpretation, "as_context", lambda: {})(),
    )
    if explicit_runtime_workflow_request(
        user_input=user_input,
        task_class=task_class,
    ):
        return False
    lowered_input = " ".join(str(user_input or "").split()).strip().lower()
    if agent._looks_like_hive_topic_drafting_request(lowered_input):
        return True
    if looks_like_public_entity_lookup_request(lowered_input) or looks_like_explicit_lookup_request(lowered_input):
        return False
    if any(marker in lowered_input for marker in ("create task", "create new task", "new task for", "add task", "add to hive", "add to the hive")):
        return False
    if "create" in lowered_input and "task" in lowered_input and ("hive" in lowered_input or "topic" in lowered_input):
        return False
    if agent._looks_like_builder_request(user_input.lower()):
        return True
    return routed_task_class in {
        "chat_conversation",
        "chat_research",
        "general_advisory",
        "business_advisory",
        "food_nutrition",
        "relationship_advisory",
        "creative_ideation",
        "debugging",
        "dependency_resolution",
        "config",
        "system_design",
        "file_inspection",
        "shell_guidance",
        "integration_orchestration",
    }
