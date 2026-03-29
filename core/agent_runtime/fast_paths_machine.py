from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.bootstrap_context import canonical_runtime_transcript
from core.onboarding import get_agent_display_name
from core.runtime_execution_tools import execute_runtime_tool

_MACHINE_DIRECTORY_MARKERS = (" desktop ", " downloads ", " documents ", " docs ")
_CAPABILITY_EXCLUSION_MARKERS = (
    " what can you do ",
    " what are your capabilities ",
    " what can you help with ",
    " help me ",
)
_OPERATOR_INTENT_EXCLUSION_MARKERS = (
    " what processes ",
    " top processes ",
    " process offenders ",
    " startup offenders ",
    " memory hogs ",
    " cpu hogs ",
    " what services ",
    " running services ",
    " service offenders ",
    " startup services ",
    " startup items ",
    " launch agents ",
)
_SAFE_MACHINE_WRITE_VERBS = (
    " create ",
    " make ",
    " mkdir",
    " write ",
    " save ",
    " append ",
    " put ",
    " edit ",
    " change ",
    " delete ",
    " remove ",
    " rename ",
    " move ",
)
_SAFE_MACHINE_WRITE_TARGETS = (
    " desktop ",
    " on my desktop ",
    " my desktop ",
    " downloads ",
    " documents ",
    " docs ",
    "~/desktop",
    "~/downloads",
    "~/documents",
    " this machine ",
    " my machine ",
    " home ",
)
_WORKSPACE_TARGET_MARKERS = (" workspace ", " repo ", " repository ", " project ", " current workspace ")
_MACHINE_SPEC_MARKERS = (
    " machine specs ",
    " machine spec ",
    " our machine ",
    " this machine ",
    " what machine ",
    " what is machine ",
    " system specs ",
    " hardware specs ",
    " ram ",
    " memory ",
    " gpu ",
    " vram ",
    " chip ",
    " cpu ",
    " cores ",
    " running on ",
    " screen size ",
    " display size ",
    " display resolution ",
    " screen resolution ",
    " monitor resolution ",
)
_TRANSCRIPT_EXPORT_VERBS = (" export ", " save ", " write ", " dump ")
_TRANSCRIPT_EXPORT_SUBJECTS = (" chat ", " conversation ", " transcript ", " session ")
_MACHINE_SPEC_HISTORY_MARKERS = (
    "machine specs for this host",
    "screen size:",
    "display:",
    "native display resolution:",
    "current display mode:",
    "recommended local model:",
)
_MACHINE_SPEC_CORRECTION_MARKERS = (
    " wrong ",
    " that's wrong ",
    " that is wrong ",
    " did not ",
    " didn't ",
    " forgot ",
    " missed ",
    " mix-up ",
    " mixed up ",
    " lost your head ",
)


def _contains_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def _contains_phrase(text: str, phrase: str) -> bool:
    candidate = " ".join(str(phrase or "").split()).strip().lower()
    if not candidate:
        return False
    if " " in candidate:
        pattern = r"\b" + r"\s+".join(re.escape(part) for part in candidate.split()) + r"\b"
        return re.search(pattern, text) is not None
    return _contains_word(text, candidate)


def looks_like_supported_machine_read_request(user_input: str) -> bool:
    normalized = " ".join(str(user_input or "").split()).strip().lower()
    normalized = re.sub(r"[\?\!\.,:;]+", " ", normalized)
    normalized = " ".join(normalized.split())
    padded = f" {normalized} "
    if not normalized:
        return False
    if any(marker in padded for marker in _CAPABILITY_EXCLUSION_MARKERS):
        return False
    if any(marker in padded for marker in _OPERATOR_INTENT_EXCLUSION_MARKERS):
        return False
    asks_for_directory = any(
        _contains_phrase(normalized, marker)
        for marker in ("desktop", "downloads", "documents", "docs")
    )
    asks_for_listing = any(
        _contains_phrase(normalized, marker)
        for marker in (" list ", " show ", " what are ", " what's on ", " what is on ", " contents of ", " what do we have on ", " tell me what ")
    )
    asks_for_listing = asks_for_listing or any(
        phrase in normalized
        for phrase in ("folders and files", "files and folders", "folder and file")
    )
    if asks_for_directory and asks_for_listing:
        return True
    return any(marker in padded for marker in _MACHINE_SPEC_MARKERS)


def looks_like_safe_machine_write_request(user_input: str) -> bool:
    lowered = " " + " ".join(str(user_input or "").split()).strip().lower() + " "
    if not lowered.strip():
        return False
    has_write_verb = any(marker in lowered for marker in _SAFE_MACHINE_WRITE_VERBS)
    has_safe_machine_target = any(marker in lowered for marker in _SAFE_MACHINE_WRITE_TARGETS)
    has_workspace_target = any(marker in lowered for marker in _WORKSPACE_TARGET_MARKERS)
    if has_safe_machine_target and has_write_verb:
        return not has_workspace_target
    return False


def looks_like_supported_machine_directory_create_request(user_input: str) -> bool:
    lowered = " " + " ".join(str(user_input or "").split()).strip().lower() + " "
    if not lowered.strip():
        return False
    if not any(marker in lowered for marker in (" create ", " make ", " mkdir ")):
        return False
    if not any(marker in lowered for marker in (" folder ", " directory ", " dir ")):
        return False
    if any(marker in lowered for marker in (" write ", " file ", " append ", " edit ", " change ", " delete ", " remove ", " rename ", " move ")):
        return False
    return any(marker in lowered for marker in (" desktop ", " downloads ", " documents ", " docs ", " on my desktop ", " my desktop "))


def safe_machine_write_targets_workspace(
    *,
    user_input: str,
    source_context: dict[str, object] | None,
) -> bool:
    workspace_root = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    if not workspace_root:
        return False
    try:
        workspace_path = Path(workspace_root).expanduser().resolve()
    except Exception:
        return False
    for raw_path in re.findall(r"(?:(?:~|/)[^\s'\"`]+)", str(user_input or "")):
        try:
            candidate = Path(raw_path).expanduser().resolve()
        except Exception:
            continue
        if candidate == workspace_path or workspace_path in candidate.parents:
            return True
    return False


def maybe_handle_safe_machine_write_guard(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_surface: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    if not looks_like_safe_machine_write_request(user_input):
        return None
    if looks_like_supported_machine_directory_create_request(user_input):
        return None
    if safe_machine_write_targets_workspace(
        user_input=user_input,
        source_context=source_context,
    ):
        return None
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=(
            "I have a bounded local write lane for safe directory creation and specific text-file exports inside Desktop, Downloads, and Documents, "
            "but I do not support arbitrary non-workspace file mutations there yet. I won't pretend I created or changed files I did not really write."
        ),
        confidence=0.95,
        source_context=source_context,
        reason="machine_write_guard",
    )


def maybe_handle_direct_machine_write_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_surface: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    transcript_export_target = _extract_machine_transcript_export_target(user_input)
    if transcript_export_target:
        transcript, _source = canonical_runtime_transcript(
            session_id=session_id,
            source_context=source_context,
            current_user_text=user_input,
            max_messages=40,
            max_chars=20000,
        )
        if not transcript:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=(
                    "I can export a local `.txt` transcript when this session actually has chat history attached, "
                    "but I do not have enough conversation history on this turn to write a real file."
                ),
                confidence=0.94,
                source_context=source_context,
                reason="machine_write_fast_path",
            )
        execution = execute_runtime_tool(
            "machine.write_file",
            {
                "path": transcript_export_target,
                "content": _render_plaintext_transcript(
                    transcript,
                    agent_name=get_agent_display_name(),
                ),
            },
            source_context=dict(source_context or {}),
        )
        if execution is None:
            return None
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=str(execution.response_text or "").strip(),
            confidence=0.98 if execution.ok else 0.9,
            source_context=source_context,
            reason="machine_write_fast_path",
        )
    if not looks_like_supported_machine_directory_create_request(user_input):
        return None
    decision = agent._plan_tool_workflow(
        user_text=user_input,
        task_class="unknown",
        executed_steps=[],
        source_context=dict(source_context or {}),
    )
    payload = dict(decision.next_payload or {})
    intent = str(payload.get("intent") or "").strip()
    if intent != "machine.ensure_directory":
        return None
    execution = execute_runtime_tool(
        intent,
        dict(payload.get("arguments") or {}),
        source_context=dict(source_context or {}),
    )
    if execution is None:
        return None
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=str(execution.response_text or "").strip(),
        confidence=0.98 if execution.ok else 0.9,
        source_context=source_context,
        reason="machine_write_fast_path",
    )


def _extract_machine_transcript_export_target(user_input: str) -> str:
    raw = " ".join(str(user_input or "").split()).strip()
    lowered = f" {raw.lower()} "
    if not raw:
        return ""
    if not any(marker in lowered for marker in _TRANSCRIPT_EXPORT_VERBS):
        return ""
    if not any(marker in lowered for marker in _TRANSCRIPT_EXPORT_SUBJECTS):
        return ""
    root = ""
    if " desktop " in lowered or " on desktop" in lowered or "on my desktop" in lowered:
        root = "~/Desktop"
    elif " downloads " in lowered or " on downloads" in lowered or "on my downloads" in lowered:
        root = "~/Downloads"
    elif " documents " in lowered or " docs " in lowered or "on my documents" in lowered:
        root = "~/Documents"
    if not root:
        return ""
    file_match = re.search(r"(?P<file>[A-Za-z0-9_.-]+\.txt)\b", raw, re.IGNORECASE)
    filename = str(file_match.group("file") or "").strip() if file_match else "chat_session.txt"
    folder_match = re.search(
        r"\bto\s+(?:the\s+)?(?P<folder>[A-Za-z0-9 _.-]+?)\s+folder(?:\s+that\s+is)?\s+on\s+(?:my\s+)?(?:desktop|downloads|documents|docs)\b",
        raw,
        re.IGNORECASE,
    )
    folder = _sanitize_safe_machine_segment(str(folder_match.group("folder") or "").strip()) if folder_match else ""
    if folder:
        return f"{root}/{folder}/{filename}".replace("//", "/")
    return f"{root}/{filename}"


def _sanitize_safe_machine_segment(value: str) -> str:
    cleaned = str(value or "").strip().strip("`\"'")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned or cleaned in {".", ".."}:
        return ""
    if "/" in cleaned or "\\" in cleaned:
        return ""
    return cleaned


def _render_plaintext_transcript(history: list[dict[str, str]], *, agent_name: str) -> str:
    blocks: list[str] = []
    for message in list(history or []):
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        speaker = "You" if role == "user" else str(agent_name or "NULLA").strip() or "NULLA"
        blocks.append(f"{speaker}\n{content}")
    return "\n\n".join(blocks).strip()


def _recent_machine_specs_context(source_context: dict[str, object] | None) -> bool:
    history = [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]
    for message in reversed(history[-8:]):
        content = " ".join(str(message.get("content") or "").split()).strip().lower()
        if not content:
            continue
        if any(marker in content for marker in _MACHINE_SPEC_HISTORY_MARKERS):
            return True
    return False


def _looks_like_machine_specs_correction_followup(
    user_input: str,
    *,
    source_context: dict[str, object] | None,
) -> bool:
    normalized = " ".join(str(user_input or "").split()).strip().lower()
    if not normalized or not _recent_machine_specs_context(source_context):
        return False
    padded = f" {normalized} "
    if any(marker in padded for marker in _MACHINE_SPEC_MARKERS):
        return True
    if any(marker in padded for marker in _MACHINE_SPEC_CORRECTION_MARKERS):
        return True
    return bool(re.search(r"\b(?:it|that)\s+is\s+not\s+\d+\b", normalized))


def _render_machine_specs_correction_response(execution: Any) -> str:
    response_text = str(getattr(execution, "response_text", "") or "").strip()
    details = dict(getattr(execution, "details", {}) or {})
    observation = dict(details.get("observation") or {})
    display_name = str(observation.get("display_name") or "").strip()
    native_resolution = str(observation.get("display_native_resolution") or "").strip()
    current_resolution = str(observation.get("display_current_resolution") or "").strip()
    screen_size = str(observation.get("screen_size") or "").strip()
    if not any((display_name, native_resolution, current_resolution, screen_size)):
        return response_text
    lines = ["Grounded display data for this host:"]
    if display_name:
        lines.append(f"- Display: {display_name}")
    if native_resolution:
        lines.append(f"- Native display resolution: {native_resolution}")
    if current_resolution:
        lines.append(f"- Current display mode: {current_resolution}")
    if screen_size:
        lines.append(f"- Screen size: {screen_size}")
    return "\n".join(lines)


def maybe_handle_direct_machine_read_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_surface: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    correction_followup = _looks_like_machine_specs_correction_followup(
        user_input,
        source_context=source_context,
    )
    if not looks_like_supported_machine_read_request(user_input) and not correction_followup:
        return None
    if correction_followup:
        intent = "machine.inspect_specs"
        execution = execute_runtime_tool(
            intent,
            {},
            source_context=dict(source_context or {}),
        )
    else:
        decision = agent._plan_tool_workflow(
            user_text=user_input,
            task_class="unknown",
            executed_steps=[],
            source_context=dict(source_context or {}),
        )
        payload = dict(decision.next_payload or {})
        intent = str(payload.get("intent") or "").strip()
        if intent not in {"machine.list_directory", "machine.inspect_specs"}:
            return None
        execution = execute_runtime_tool(
            intent,
            dict(payload.get("arguments") or {}),
            source_context=dict(source_context or {}),
        )
    if execution is None:
        return None
    response_text = (
        _render_machine_specs_correction_response(execution)
        if correction_followup and getattr(execution, "ok", False)
        else str(execution.response_text or "").strip()
    )
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response_text,
        confidence=0.98 if execution.ok else 0.9,
        source_context=source_context,
        reason="machine_read_fast_path",
    )
