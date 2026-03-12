from __future__ import annotations

from typing import Any

from core import policy_engine
from core.internal_message_schema import InternalMessage, InternalModelRequest
from core.local_operator_actions import list_operator_tools
from core.tool_intent_executor import runtime_tool_specs
from core.user_preferences import load_preferences


_STRUCTURED_OUTPUT_MODES = {"json_object", "action_plan", "tool_intent", "summary_block"}
_TOOL_LABELS = {
    "inspect_disk_usage": "disk inspection",
    "cleanup_temp_files": "temp cleanup",
    "inspect_processes": "process inspection",
    "inspect_services": "service inspection",
    "move_path": "file move/archive",
    "schedule_calendar_event": "calendar outbox creation",
    "discord_post": "Discord posting",
    "telegram_send": "Telegram sending",
}


def normalize_prompt(
    *,
    task: Any,
    classification: dict[str, Any],
    interpretation: Any,
    context_result: Any,
    persona: Any,
    output_mode: str,
    task_kind: str,
    trace_id: str,
    surface: str = "cli",
    source_context: dict[str, Any] | None = None,
) -> InternalModelRequest:
    ambiguity = float(getattr(interpretation, "understanding_confidence", 0.0) or 0.0)
    user_text = getattr(interpretation, "reconstructed_text", "") or getattr(task, "task_summary", "")

    if surface in {"channel", "openclaw", "api"}:
        return _build_conversational_request(
            user_text=user_text,
            persona=persona,
            classification=classification,
            context_result=context_result,
            task_kind=task_kind,
            output_mode=output_mode,
            trace_id=trace_id,
            ambiguity=ambiguity,
            source_context=source_context,
        )

    constraints = [
        "You are a replaceable helper or teacher backend for NULLA.",
        "Do not claim canonical truth.",
        "Return only the requested output shape.",
        "Do not invent private history or hidden state.",
    ]
    if output_mode in _STRUCTURED_OUTPUT_MODES:
        constraints.append("Return valid JSON only.")

    system = InternalMessage(
        role="system",
        content=(
            "NULLA remains the system. You are a worker backend. "
            f"Persona tone target: {persona.tone}. "
            f"Task class: {classification.get('task_class', 'unknown')}. "
            f"Output mode: {output_mode}. "
            f"Constraints: {' '.join(constraints)}"
        ),
    )
    user = InternalMessage(
        role="user",
        content=(
            f"Normalized request: {user_text}\n"
            f"Understanding confidence: {ambiguity:.2f}\n"
            f"Topic hints: {', '.join(list(getattr(interpretation, 'topic_hints', []) or [])[:6]) or 'none'}\n"
            f"Risk flags: {', '.join(list(classification.get('risk_flags') or [])[:6]) or 'none'}"
        ),
    )
    context = InternalMessage(
        role="context",
        content=context_result.assembled_context() or "No additional context beyond bootstrap.",
        metadata={"retrieval_confidence": context_result.report.retrieval_confidence},
    )
    return InternalModelRequest(
        task_kind=task_kind,
        task_class=str(classification.get("task_class", "unknown")),
        output_mode=output_mode,
        messages=[system, user, context],
        trace_id=trace_id,
        max_output_tokens=_max_output_tokens(output_mode),
        temperature=_temperature_for_mode(output_mode),
        ambiguity_confidence=ambiguity,
        constraints=constraints,
        context_summary=context_result.report.to_dict(),
        metadata={
            "persona_id": getattr(persona, "persona_id", "default"),
            "task_id": getattr(task, "task_id", ""),
        },
        attachments=list((context_result.report.to_dict().get("external_evidence_attachments") or [])),
    )


def _build_conversational_request(
    *,
    user_text: str,
    persona: Any,
    classification: dict[str, Any],
    context_result: Any,
    task_kind: str,
    output_mode: str,
    trace_id: str,
    ambiguity: float,
    source_context: dict[str, Any] | None = None,
) -> InternalModelRequest:
    """Build a natural conversational prompt for chat surfaces."""
    persona_name = getattr(persona, "display_name", "NULLA")
    persona_tone = getattr(persona, "tone", "calm")

    tone_guide = {
        "calm": "You are warm, clear, and thoughtful.",
        "direct": "You are concise and to the point.",
        "teacher": "You explain things step by step, like a patient teacher.",
        "savage": "You are blunt and no-nonsense, but still helpful.",
    }.get(persona_tone, "You are helpful and conversational.")

    assembled_context = context_result.assembled_context() or ""
    context_block = ""
    if assembled_context and assembled_context != "No additional context beyond bootstrap.":
        context_block = f"\n\nRelevant context from your memory:\n{assembled_context[:2000]}"
    source_context = dict(source_context or {})
    source_platform = str(source_context.get("platform", "") or "").strip().lower()
    source_surface = str(source_context.get("surface", "") or "").strip().lower()
    has_openclaw_tools = source_platform in {"openclaw", "web_companion", "telegram", "discord"} or source_surface in {"channel", "openclaw", "api"}
    tooling_guidance = _tooling_guidance(has_openclaw_tools=has_openclaw_tools)
    format_guidance = _chat_output_guidance(output_mode)
    tool_catalog_guidance = _tool_intent_catalog_text() if output_mode == "tool_intent" else ""

    system = InternalMessage(
        role="system",
        content=(
            f"You are {persona_name}, a knowledgeable AI assistant. "
            f"{tone_guide} "
            "You can help with coding, debugging, system design, research, "
            "and general conversation. "
            "Keep responses concise but complete. "
            "If you have relevant context from memory, use it to give better answers. "
            f"{tooling_guidance} "
            f"{format_guidance} "
            f"{tool_catalog_guidance} "
            "Do not mention internal systems, confidence scores, or planning steps."
            f"{context_block}"
        ),
    )
    history_messages = _history_messages_from_source_context(source_context, current_user_text=user_text)
    user = InternalMessage(role="user", content=user_text)

    return InternalModelRequest(
        task_kind=task_kind,
        task_class=str(classification.get("task_class", "unknown")),
        output_mode=output_mode,
        messages=[system, *history_messages, user],
        trace_id=trace_id,
        max_output_tokens=_max_output_tokens(output_mode),
        temperature=_temperature_for_mode(output_mode),
        ambiguity_confidence=ambiguity,
        constraints=[],
        context_summary=context_result.report.to_dict(),
        metadata={"persona_id": getattr(persona, "persona_id", "default")},
        attachments=list((context_result.report.to_dict().get("external_evidence_attachments") or [])),
    )


def _history_messages_from_source_context(
    source_context: dict[str, Any] | None,
    *,
    current_user_text: str,
    max_messages: int = 10,
    max_chars: int = 5000,
) -> list[InternalMessage]:
    source_context = dict(source_context or {})
    raw_history = list(source_context.get("conversation_history") or [])
    normalized: list[dict[str, str]] = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = " ".join(str(item.get("content") or "").split()).strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content})
    if normalized and normalized[-1]["role"] == "user" and normalized[-1]["content"] == " ".join(current_user_text.split()).strip():
        normalized = normalized[:-1]
    selected_reversed: list[dict[str, str]] = []
    used_chars = 0
    for item in reversed(normalized):
        content = str(item.get("content") or "")
        if selected_reversed and (len(selected_reversed) >= max_messages or used_chars + len(content) > max_chars):
            break
        selected_reversed.append(item)
        used_chars += len(content)
    selected = list(reversed(selected_reversed))
    return [InternalMessage(role=item["role"], content=item["content"]) for item in selected]


def _max_output_tokens(output_mode: str) -> int:
    return {
        "plain_text": 240,
        "summary_block": 220,
        "json_object": 220,
        "action_plan": 320,
        "tool_intent": 700,
    }.get(output_mode, 240)


def _temperature_for_mode(output_mode: str) -> float:
    if output_mode in _STRUCTURED_OUTPUT_MODES:
        return 0.1
    return 0.2


def _tooling_guidance(*, has_openclaw_tools: bool) -> str:
    if not has_openclaw_tools:
        return (
            "Use local context first and be explicit when live external data is needed. "
            "Never claim you performed live web lookup or any tool action unless the result is present in this run."
        )

    prefs = load_preferences()
    autonomy_mode = str(getattr(prefs, "autonomy_mode", "hands_off") or "hands_off").strip().lower()
    available_tools = [
        _TOOL_LABELS.get(str(tool.get("tool_id") or "").strip(), str(tool.get("tool_id") or "").strip())
        for tool in list_operator_tools()
        if tool.get("available")
    ]
    available_tools = [label for label in available_tools if label]
    if policy_engine.get("filesystem.allow_read_workspace", True):
        available_tools.insert(0, "workspace file listing, search, and read")
    if policy_engine.get("filesystem.allow_write_workspace", False):
        available_tools.insert(1, "workspace file edits")
    if policy_engine.get("execution.allow_sandbox_execution", False):
        available_tools.insert(2, "sandboxed local command execution with network blocked")
    if policy_engine.allow_web_fallback():
        available_tools.insert(0, "live web lookup when actual results return")

    if available_tools:
        tool_text = ", ".join(dict.fromkeys(available_tools))
        capability_line = f"Only assume these wired capabilities right now: {tool_text}."
    else:
        capability_line = "Do not assume any operational tools are wired unless a concrete result proves it."

    if autonomy_mode == "strict":
        approval_line = (
            "Ask before any side-effect action."
        )
    elif autonomy_mode == "balanced":
        approval_line = (
            "Ask before destructive or outward-facing side-effect actions."
        )
    else:
        approval_line = (
            "Do not ask for micro-confirmation on read-only or low-risk bounded steps. "
            "Only stop for destructive changes, leak risk, ambiguous side effects, or clearly outward-facing actions the user did not explicitly command."
        )

    return (
        f"{capability_line} "
        "Email and inbox tooling are not guaranteed; if a tool is not explicitly wired, say so instead of implying it exists. "
        "Never claim you searched the web, checked Hive, fetched live data, or used an external tool unless concrete evidence from that action is present in this run. "
        f"{approval_line}"
    )


def _chat_output_guidance(output_mode: str) -> str:
    if output_mode == "action_plan":
        return 'Return valid JSON only in the form {"summary": string, "steps": [string, ...]}.'
    if output_mode == "summary_block":
        return 'Return valid JSON only in the form {"summary": string, "bullets": [string, ...]}.'
    if output_mode == "tool_intent":
        return 'Return valid JSON only in the form {"intent": string, "arguments": object}.'
    if output_mode == "json_object":
        return "Return valid JSON only."
    return "Respond naturally in plain text."


def _tool_intent_catalog_text() -> str:
    specs = runtime_tool_specs()
    if not specs:
        return (
            'If no real runtime tool is available, return {"intent":"respond.direct","arguments":{}}. '
            "Never invent tool names."
        )
    lines = ["Choose exactly one intent name from this runtime tool catalog:"]
    for spec in specs:
        intent = str(spec.get("intent") or "").strip()
        description = str(spec.get("description") or "").strip()
        arguments = spec.get("arguments") or {}
        lines.append(
            f"- {intent}: {description} Arguments: {arguments if isinstance(arguments, dict) else {}}"
        )
    lines.append(
        'If no tool is needed or you are done after real tool work, return {"intent":"respond.direct","arguments":{"message":"final grounded reply"}}. '
        "Prefer another real tool call over guessing. Never invent intent names or unsupported arguments."
    )
    return " ".join(line for line in lines if line.strip())
