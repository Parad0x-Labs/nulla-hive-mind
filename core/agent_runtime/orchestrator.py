from __future__ import annotations

import json
from typing import Any


def apply_interaction_transition(
    agent: Any,
    session_id: str,
    result: Any,
    *,
    session_hive_state_fn: Any,
    set_hive_interaction_state_fn: Any,
) -> None:
    if not session_id:
        return
    state = session_hive_state_fn(session_id)
    payload = dict(state.get("interaction_payload") or {})
    preserve_task_context = bool(
        str(state.get("interaction_mode") or "")
        in {
            "hive_nudge_shown",
            "hive_task_selection_pending",
            "hive_task_active",
            "hive_task_status_pending",
        }
        and (
            payload.get("active_topic_id")
            or agent._interaction_pending_topic_ids(state)
            or list(state.get("pending_topic_ids") or [])
        )
    )
    if result.response_class == agent.ResponseClass.SMALLTALK:
        if preserve_task_context:
            return
        set_hive_interaction_state_fn(session_id, mode="smalltalk", payload={})
        return
    if result.response_class == agent.ResponseClass.UTILITY_ANSWER:
        if preserve_task_context:
            return
        if (
            str(state.get("interaction_mode") or "").strip().lower() == "utility"
            and str(payload.get("utility_kind") or "").strip().lower() == "time"
            and "current time" in str(result.text or "").lower()
        ):
            return
        set_hive_interaction_state_fn(session_id, mode="utility", payload={})
        return
    if result.response_class == agent.ResponseClass.GENERIC_CONVERSATION:
        if preserve_task_context:
            return
        set_hive_interaction_state_fn(session_id, mode="generic_conversation", payload={})
        return
    if result.response_class in {agent.ResponseClass.SYSTEM_ERROR_USER_SAFE, agent.ResponseClass.TASK_FAILED_USER_SAFE}:
        set_hive_interaction_state_fn(session_id, mode="error_recovery", payload={})
        return
    if result.response_class in {agent.ResponseClass.TASK_LIST, agent.ResponseClass.TASK_SELECTION_CLARIFICATION}:
        set_hive_interaction_state_fn(session_id, mode="hive_task_selection_pending", payload=payload)
        return
    if result.response_class == agent.ResponseClass.TASK_STARTED:
        set_hive_interaction_state_fn(session_id, mode="hive_task_active", payload=payload)
        return
    if result.response_class == agent.ResponseClass.TASK_STATUS:
        set_hive_interaction_state_fn(session_id, mode="hive_task_status_pending", payload=payload)


def task_workflow_summary(
    *,
    classification: dict[str, Any],
    context_result: Any,
    model_execution: dict[str, Any],
    media_analysis: dict[str, Any],
    curiosity_result: dict[str, Any],
    gate_mode: str,
) -> str:
    lines: list[str] = []
    task_class = str(classification.get("task_class") or "unknown")
    lines.append(f"- classified task as `{task_class}`")
    try:
        retrieval_conf = float(context_result.report.retrieval_confidence)
        lines.append(f"- loaded memory/context with retrieval confidence {retrieval_conf:.2f}")
    except Exception:
        pass
    provider = str((model_execution or {}).get("provider_id") or (model_execution or {}).get("source") or "none")
    used_model = bool((model_execution or {}).get("used_model", True))
    lines.append(f"- {'used' if used_model else 'skipped'} model path via `{provider}`")
    media_reason = str((media_analysis or {}).get("reason") or "").strip()
    if media_reason:
        lines.append(f"- media/web evidence status: `{media_reason}`")
    curiosity_mode = str((curiosity_result or {}).get("mode") or "").strip()
    if curiosity_mode:
        lines.append(f"- curiosity/research lane: `{curiosity_mode}`")
    lines.append(f"- execution posture: `{gate_mode}`")
    return "\n".join(lines)


def action_workflow_summary(
    *,
    operator_kind: str,
    dispatch_status: str,
    details: dict[str, Any] | None,
) -> str:
    lines = [f"- recognized operator action `{operator_kind}`", f"- action state: `{dispatch_status}`"]
    info = dict(details or {})
    action_id = str(info.get("action_id") or "").strip()
    if action_id:
        lines.append(f"- action id: `{action_id}`")
    target_path = str(info.get("target_path") or "").strip()
    if target_path:
        lines.append(f"- target: `{target_path}`")
    return "\n".join(lines)


def _git_summary_observation_message(step: dict[str, Any]) -> str:
    observation = step.get("observation")
    if not isinstance(observation, dict):
        return ""
    intent = str(observation.get("intent") or step.get("tool_name") or "").strip()
    if intent != "workspace.git_summary":
        return ""
    repo = str(observation.get("cwd") or "").strip()
    branch = str(observation.get("branch") or "").strip() or "(detached HEAD)"
    commit = str(observation.get("commit") or "").strip() or "unknown"
    dirty = "yes" if bool(observation.get("dirty")) else "no"
    local_branch_count = int(observation.get("local_branch_count") or 0)
    remote_branch_count = int(observation.get("remote_branch_count") or 0)
    total_branch_count = int(observation.get("total_branch_count") or (local_branch_count + remote_branch_count))
    today_date = str(observation.get("today_date") or "").strip()
    yesterday_date = str(observation.get("yesterday_date") or "").strip()
    timezone_label = str(observation.get("timezone_label") or "").strip()
    commit_count_scope = str(observation.get("commit_count_scope") or "").strip()
    lines = [f"Git repo summary for `{repo}`:" if repo else "Git repo summary:"]
    lines.append(f"- current branch: {branch}")
    lines.append(f"- head commit: {commit}")
    lines.append(f"- dirty: {dirty}")
    lines.append(f"- visible branches: {total_branch_count} total ({local_branch_count} local, {remote_branch_count} remote tracking)")
    if today_date:
        lines.append(f"- commits on {today_date}: {int(observation.get('today_commit_count') or 0)}")
    if yesterday_date:
        lines.append(f"- commits on {yesterday_date}: {int(observation.get('yesterday_commit_count') or 0)}")
    if commit_count_scope:
        rendered_scope = (
            "repo-wide unique commits across visible local and remote refs"
            if commit_count_scope == "all_visible_refs_unique"
            else commit_count_scope.replace("_", " ")
        )
        lines.append(f"- commit count scope: {rendered_scope}")
    if timezone_label:
        lines.append(f"- commit day boundary timezone: {timezone_label}")
    return "\n".join(lines)


def tool_loop_final_message(synthesis: Any, executed_steps: list[dict[str, Any]]) -> str:
    structured = getattr(synthesis, "structured_output", None)
    if isinstance(structured, dict):
        summary = str(structured.get("summary") or structured.get("message") or "").strip()
        bullet_source = structured.get("bullets") or structured.get("steps") or []
        bullets = [str(item).strip() for item in list(bullet_source) if str(item).strip()]
        if summary and bullets:
            return summary + "\n" + "\n".join(f"- {item}" for item in bullets[:6])
        if summary:
            return summary
    output_text = str(getattr(synthesis, "output_text", "") or "").strip()
    if output_text:
        return output_text
    if executed_steps:
        last_step = executed_steps[-1]
        observation_message = _git_summary_observation_message(last_step)
        if observation_message:
            return observation_message
        return (
            f"Completed {len(executed_steps)} real tool step{'s' if len(executed_steps) != 1 else ''}. "
            f"Last result: {str(last_step.get('summary') or 'tool execution finished').strip()}"
        )
    return "I ran the available tools, but I do not have a grounded final synthesis yet."


def render_tool_loop_response(
    *,
    final_message: str,
    executed_steps: list[dict[str, Any]],
    include_step_summary: bool = True,
) -> str:
    message = str(final_message or "").strip()
    if not executed_steps or not include_step_summary:
        return message
    lines = ["Real steps completed:"]
    for step in executed_steps:
        tool_name = str(step.get("tool_name") or "tool").strip()
        summary = str(step.get("summary") or step.get("status") or "completed").strip()
        lines.append(f"- {tool_name}: {summary}")
    if message:
        lines.extend(["", message])
    return "\n".join(lines).strip()


def tool_intent_loop_workflow_summary(
    *,
    executed_steps: list[dict[str, Any]],
    provider_id: str | None,
    validation_state: str,
) -> str:
    lines = [f"- model-driven tool loop executed {len(executed_steps)} real step{'s' if len(executed_steps) != 1 else ''}"]
    if executed_steps:
        step_chain = " -> ".join(str(step.get("tool_name") or "tool").strip() for step in executed_steps[:6])
        if step_chain:
            lines.append(f"- tool chain: `{step_chain}`")
    provider = str(provider_id or "").strip()
    if provider:
        lines.append(f"- tool intent provider: `{provider}`")
    validation = str(validation_state or "").strip()
    if validation:
        lines.append(f"- tool intent validation: `{validation}`")
    lines.append("- execution posture: `tool_executed`")
    return "\n".join(lines)


def tool_step_summary(response_text: str, *, fallback: str) -> str:
    for raw_line in str(response_text or "").splitlines():
        line = " ".join(raw_line.split()).strip(" -")
        if not line:
            continue
        return (line[:157] + "...") if len(line) > 160 else line
    clean_fallback = " ".join(str(fallback or "").split()).strip()
    return clean_fallback or "completed"


def runtime_preview(text: str, *, limit: int = 220) -> str:
    compact = " ".join(str(text or "").split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 3)].rstrip() + "..."


def emit_runtime_event(
    agent: Any,
    source_context: dict[str, Any] | None,
    *,
    event_type: str,
    message: str,
    emit_runtime_event_fn: Any,
    **details: Any,
) -> None:
    payload = dict(details)
    checkpoint_id = agent._runtime_checkpoint_id(source_context)
    if checkpoint_id and "checkpoint_id" not in payload:
        payload["checkpoint_id"] = checkpoint_id
    emit_runtime_event_fn(
        source_context,
        event_type=event_type,
        message=message,
        details=payload,
    )


def live_runtime_stream_enabled(source_context: dict[str, Any] | None) -> bool:
    return bool(str((source_context or {}).get("runtime_event_stream_id") or "").strip())


def tool_history_observation_prompt(observation: dict[str, Any]) -> str:
    return (
        "Grounding observations for this turn. Use them as evidence, not as a template:\n"
        f"{json.dumps(dict(observation or {}), indent=2, sort_keys=True, default=str)}"
    )
