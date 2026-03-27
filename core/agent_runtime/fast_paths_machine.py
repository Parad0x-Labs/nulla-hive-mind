from __future__ import annotations

import re
from pathlib import Path
from typing import Any

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
    if safe_machine_write_targets_workspace(
        user_input=user_input,
        source_context=source_context,
    ):
        return None
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=(
            "I can read safe local folders like Desktop, Downloads, and Documents on this machine, "
            "but I do not have a real non-workspace write lane there yet. I won't pretend I created or changed files outside the active workspace."
        ),
        confidence=0.95,
        source_context=source_context,
        reason="machine_write_guard",
    )


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
    if not looks_like_supported_machine_read_request(user_input):
        return None
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
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=str(execution.response_text or "").strip(),
        confidence=0.98 if execution.ok else 0.9,
        source_context=source_context,
        reason="machine_read_fast_path",
    )
