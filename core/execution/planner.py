from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.runtime_execution_tools import extract_observation_followup_hints, looks_like_execution_request
from core.task_router import (
    looks_like_explicit_lookup_request,
    looks_like_live_recency_lookup,
    looks_like_public_entity_lookup_request,
)

from .constants import (
    _APPEND_CONTENT_ONLY_RE,
    _APPEND_FILE_RE,
    _BUILDER_RESEARCH_MARKERS,
    _CREATE_EXACT_FILES_RE,
    _CREATE_NAMED_FILE_WITH_CONTENT_RE,
    _CREATE_PATH_RE,
    _DIRECTORY_CREATE_MARKERS,
    _ENTITY_LOOKUP_DROP_TOKENS,
    _ENTITY_LOOKUP_KEEP_SHORT_TOKENS,
    _EXACT_READBACK_RE,
    _FOLDER_PATH_RE,
    _GENERIC_HIVE_TITLE_MARKERS,
    _HIVE_ACTION_PATTERNS,
    _HIVE_CREATE_PREFIXES,
    _INLINE_CREATE_FILE_RE,
    _INTEGRATION_DOMAIN_MARKERS,
    _INTO_PATH_RE,
    _LIVE_LOOKUP_MARKERS,
    _LOCAL_TOOL_MARKERS,
    _NAMED_PATH_RE,
    _OVERWRITE_FILE_RE,
    _PATH_STOP_WORDS,
    _START_CODE_MARKERS,
    _TOOL_INVENTORY_MARKERS,
    _URL_RE,
    _VERB_NAME_FOLDER_RE,
)
from .models import WorkflowPlannerDecision


def should_attempt_tool_intent(
    user_text: str,
    *,
    task_class: str,
    source_context: dict[str, Any] | None = None,
) -> bool:
    source_context = dict(source_context or {})
    surface = str(source_context.get("surface") or "").strip().lower()
    platform = str(source_context.get("platform") or "").strip().lower()
    tool_capable_surfaces = {"channel", "openclaw", "api", "cli", "terminal", "local", ""}
    tool_capable_platforms = {"openclaw", "telegram", "discord", "local", "cli", ""}
    if surface not in tool_capable_surfaces and platform not in tool_capable_platforms:
        return False

    text = str(user_text or "").strip()
    lowered = text.lower()
    if not lowered:
        return False
    if (
        task_class in {"integration_orchestration", "system_design", "research"}
        and any(marker in lowered for marker in _BUILDER_RESEARCH_MARKERS)
        and any(marker in lowered for marker in _INTEGRATION_DOMAIN_MARKERS)
    ):
        return False
    if task_class == "integration_orchestration":
        return True
    if _URL_RE.search(text):
        return True
    if looks_like_explicit_lookup_request(text) or looks_like_public_entity_lookup_request(text):
        return True
    if looks_like_live_recency_lookup(text):
        return True
    if any(marker in lowered for marker in _LIVE_LOOKUP_MARKERS):
        return True
    if any(marker in lowered for marker in _LOCAL_TOOL_MARKERS):
        return True
    if looks_like_execution_request(text, task_class=task_class):
        return True
    if any(marker in lowered for marker in _HIVE_ACTION_PATTERNS):
        return True
    if "hive" in lowered and any(word in lowered for word in ("claim", "topic", "progress", "result", "task")):
        return True
    padded = f" {lowered} "
    if any(
        marker in padded
        for marker in (
            " proceed ", " do it ", " do all ", " go ahead ", " carry on ",
            " start working ", " continue ", " yes proceed ", " yes do it ",
            " yes go ahead ", " yes continue ", " deliver it ", " submit it ",
            " execute ", " run it ", " just do it ",
        )
    ):
        return True
    compact = lowered.strip(" \t\n\r?!.,")
    return compact in {
        "proceed",
        "do it",
        "do all",
        "go ahead",
        "carry on",
        "continue",
        "start working",
        "yes",
        "yes proceed",
        "yes do it",
        "ok do it",
        "ok proceed",
        "ok go ahead",
        "deliver it",
        "submit it",
        "execute",
        "run it",
        "just do it",
        "yes pls",
        "yes please",
        "all good carry on",
        "proceed with next steps",
        "proceed with that",
    }


def _looks_like_followup_resume_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    padded = f" {lowered} "
    if any(
        marker in padded
        for marker in (
            " proceed ",
            " do it ",
            " do all ",
            " go ahead ",
            " carry on ",
            " continue ",
            " start working ",
            " yes proceed ",
            " yes do it ",
            " yes continue ",
            " execute ",
            " run it ",
            " just do it ",
        )
    ):
        return True
    compact = lowered.strip(" \t\n\r?!.,")
    return compact in {
        "proceed",
        "do it",
        "do all",
        "go ahead",
        "carry on",
        "continue",
        "start working",
        "yes",
        "yes proceed",
        "yes do it",
        "ok do it",
        "ok proceed",
        "ok go ahead",
        "deliver it",
        "submit it",
        "execute",
        "run it",
        "just do it",
        "yes pls",
        "yes please",
        "all good carry on",
        "proceed with next steps",
        "proceed with that",
    }


def _entity_lookup_query_variants(text: str) -> tuple[str, str]:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return "", ""

    tokens: list[str] = []
    for token in re.findall(r"[a-z0-9\.]+", normalized):
        clean = token.strip(".")
        if not clean or clean in _ENTITY_LOOKUP_DROP_TOKENS:
            continue
        if clean == "x.com":
            clean = "x"
        if len(clean) == 1 and clean not in _ENTITY_LOOKUP_KEEP_SHORT_TOKENS:
            continue
        tokens.append(clean)

    if not tokens:
        return normalized, normalized

    primary_tokens = list(dict.fromkeys(tokens))[:6]
    retry_tokens = [
        re.sub(r"(.)\1+", r"\1", token) if len(token) >= 3 and token not in {"solana", "twitter"} else token
        for token in primary_tokens
    ]
    retry_tokens = list(dict.fromkeys(token for token in retry_tokens if token))
    if retry_tokens == primary_tokens:
        if "x" in retry_tokens and "twitter" not in retry_tokens:
            retry_tokens.append("twitter")
        elif "profile" not in retry_tokens:
            retry_tokens.append("profile")

    primary_query = " ".join(primary_tokens).strip() or normalized
    retry_query = " ".join(retry_tokens).strip() or primary_query
    return primary_query, retry_query


def _looks_like_tool_inventory_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    return any(marker in lowered for marker in _TOOL_INVENTORY_MARKERS)


def _clean_workspace_path(candidate: str) -> str:
    clean = str(candidate or "").strip().strip("`\"'").strip().rstrip(".,!?")
    if not clean:
        return ""
    if clean.lower() in _PATH_STOP_WORDS:
        return ""
    if clean.startswith("/"):
        clean = clean.lstrip("/")
    clean = clean.lstrip("./")
    if not clean or clean.lower() in _PATH_STOP_WORDS:
        return ""
    if ".." in clean.split("/"):
        return ""
    return clean


def _extract_workspace_bootstrap_path(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    for pattern in (_NAMED_PATH_RE, _VERB_NAME_FOLDER_RE, _FOLDER_PATH_RE, _CREATE_PATH_RE, _INTO_PATH_RE):
        match = pattern.search(raw)
        if not match:
            continue
        clean = _clean_workspace_path(match.group("path"))
        if clean:
            return clean
    return ""


def _extract_safe_machine_directory_listing(text: str) -> dict[str, Any] | None:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return None
    if any(marker in lowered for marker in ("create ", "make ", "mkdir", "write ", "edit ", "change ", "delete ")):
        return None
    wants_listing = any(
        marker in lowered
        for marker in (
            "list ",
            "show ",
            "what are ",
            "what's on",
            "what is on",
            "contents of",
            "what do we have on",
            "tell me what",
        )
    )
    if not wants_listing:
        return None
    path = ""
    if "desktop" in lowered:
        path = "~/Desktop"
    elif "downloads" in lowered:
        path = "~/Downloads"
    elif "documents" in lowered or "docs" in lowered:
        path = "~/Documents"
    if not path:
        return None
    directories_only = any(token in lowered for token in (" folder", " folders", " directory", " directories"))
    return {"path": path, "directories_only": directories_only, "limit": 200}


def _looks_like_workspace_bootstrap_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    creates_folder = any(marker in lowered for marker in _DIRECTORY_CREATE_MARKERS)
    starts_code = any(marker in lowered for marker in _START_CODE_MARKERS)
    mentions_workspace_target = any(marker in lowered for marker in ("folder", "directory", "dir", "workspace", "repo", "repository"))
    has_path = bool(_extract_workspace_bootstrap_path(text))
    fuzzy_create_verb = any(v in lowered for v in ("create", "make", "mkdir", "crate", "creat"))
    return bool(
        (creates_folder and (starts_code or has_path))
        or (starts_code and mentions_workspace_target)
        or (starts_code and has_path)
        or (fuzzy_create_verb and mentions_workspace_target and has_path)
    )


def _clean_workspace_file_path(candidate: str, *, base_dir: str = "") -> str:
    clean = _clean_workspace_path(candidate)
    if not clean or "." not in Path(clean).name:
        return ""
    if base_dir and "/" not in clean:
        clean = f"{base_dir.rstrip('/')}/{clean}"
    return clean


def _history_messages(source_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]


def _extract_history_observation_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    content = str(message.get("content") or "").strip()
    if not content.startswith("Grounding observations for this turn."):
        return None
    start = content.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(content[start:])
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _recover_last_workspace_path_from_history(source_context: dict[str, Any] | None) -> str:
    history = _history_messages(source_context)
    for message in reversed(history[-12:]):
        observation = _extract_history_observation_payload(message)
        if observation is not None:
            intent = str(observation.get("intent") or "").strip()
            if intent in {"workspace.write_file", "workspace.read_file", "workspace.replace_in_file"}:
                path = _clean_workspace_file_path(str(observation.get("path") or "").strip())
                if path:
                    return path
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = str(message.get("content") or "")
        for pattern in (_APPEND_FILE_RE, _OVERWRITE_FILE_RE, _CREATE_NAMED_FILE_WITH_CONTENT_RE, _INLINE_CREATE_FILE_RE):
            match = pattern.search(content)
            if not match:
                continue
            path = _clean_workspace_file_path(str(match.group("path") or "").strip())
            if path:
                return path
    return ""


def _extract_workspace_file_plan(
    text: str,
    *,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    raw = " ".join(str(text or "").split()).strip()
    if not raw:
        return None
    raw = re.sub(
        r"(?P<stem>[A-Za-z0-9_./-]+)\.\s+(?P<ext>py|js|ts|tsx|jsx|txt|md|json|yaml|yml|toml)\b",
        r"\g<stem>.\g<ext>",
        raw,
    )
    base_dir = ""
    if any(marker in raw.lower() for marker in _DIRECTORY_CREATE_MARKERS):
        base_dir = _extract_workspace_bootstrap_path(raw)
    list_requested = any(
        marker in raw.lower()
        for marker in (
            "list the folder contents",
            "list the directory contents",
            "list folder contents",
            "list directory contents",
            "list the contents",
        )
    )

    exact_multi = _CREATE_EXACT_FILES_RE.search(raw)
    if exact_multi is not None:
        paths = [_clean_workspace_file_path(item.strip(), base_dir=base_dir) for item in str(exact_multi.group("paths") or "").split(",")]
        contents = [item.strip() for item in str(exact_multi.group("contents") or "").split(",")]
        writes = [
            {"path": path, "content": contents[index], "mode": "write"}
            for index, path in enumerate(paths)
            if path and index < len(contents) and contents[index]
        ]
        if writes:
            list_path = base_dir
            if list_requested and not list_path:
                parent = str(Path(str(writes[0].get("path") or "")).parent)
                list_path = "" if parent in {"", "."} else parent
            return {"directory": "", "writes": writes, "read_path": "", "verbatim_read": False, "list_path": list_path}

    overwrite_match = _OVERWRITE_FILE_RE.search(raw)
    if overwrite_match is not None:
        path = _clean_workspace_file_path(str(overwrite_match.group("path") or "").strip(), base_dir=base_dir)
        content = str(overwrite_match.group("content") or "").strip()
        if path and content:
            list_path = base_dir
            if list_requested and not list_path:
                parent = str(Path(path).parent)
                list_path = "" if parent in {"", "."} else parent
            return {"directory": "", "writes": [{"path": path, "content": content, "mode": "write"}], "read_path": "", "verbatim_read": False, "list_path": list_path}

    append_match = _APPEND_FILE_RE.search(raw)
    if append_match is not None:
        path = _clean_workspace_file_path(
            str(append_match.group("path") or "").strip() or _recover_last_workspace_path_from_history(source_context),
            base_dir=base_dir,
        )
        content = str(append_match.group("content") or "").strip()
        if path and content:
            list_path = base_dir
            if list_requested and not list_path:
                parent = str(Path(path).parent)
                list_path = "" if parent in {"", "."} else parent
            return {"directory": "", "writes": [{"path": path, "content": content, "mode": "append"}], "read_path": "", "verbatim_read": False, "list_path": list_path}
    append_content_only_match = _APPEND_CONTENT_ONLY_RE.search(raw)
    if append_content_only_match is not None:
        path = _clean_workspace_file_path(
            _recover_last_workspace_path_from_history(source_context),
            base_dir=base_dir,
        )
        content = str(append_content_only_match.group("content") or "").strip()
        if path and content:
            list_path = base_dir
            if list_requested and not list_path:
                parent = str(Path(path).parent)
                list_path = "" if parent in {"", "."} else parent
            return {"directory": "", "writes": [{"path": path, "content": content, "mode": "append"}], "read_path": "", "verbatim_read": False, "list_path": list_path}

    writes: list[dict[str, Any]] = []
    for pattern in (_CREATE_NAMED_FILE_WITH_CONTENT_RE, _INLINE_CREATE_FILE_RE):
        for match in pattern.finditer(raw):
            path = _clean_workspace_file_path(str(match.group("path") or "").strip(), base_dir=base_dir)
            content = str(match.group("content") or "").strip()
            if path and content:
                writes.append({"path": path, "content": content, "mode": "write"})
    if writes:
        list_path = base_dir
        if list_requested and not list_path:
            parent = str(Path(str(writes[0].get("path") or "")).parent)
            list_path = "" if parent in {"", "."} else parent
        return {"directory": base_dir, "writes": writes, "read_path": "", "verbatim_read": False, "list_path": list_path}

    if _EXACT_READBACK_RE.search(raw):
        path = _recover_last_workspace_path_from_history(source_context)
        if path:
            return {"directory": "", "writes": [], "read_path": path, "verbatim_read": True, "list_path": ""}
    return None


def _pending_workspace_writes(plan: dict[str, Any], steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    writes = [dict(item) for item in list(plan.get("writes") or []) if isinstance(item, dict)]
    if not writes:
        return []
    completed: list[tuple[str, str]] = []
    for step in list(steps or []):
        tool_name = str(step.get("tool_name") or "").strip()
        if tool_name != "workspace.write_file":
            continue
        args = dict(step.get("arguments") or {})
        completed.append((str(args.get("path") or "").strip(), "write"))
        completed.append((str(args.get("path") or "").strip(), "append"))
    return [item for item in writes if (str(item.get("path") or "").strip(), str(item.get("mode") or "write").strip()) not in completed]


def _normalize_hive_title_candidate(text: str) -> str:
    normalized = " ".join(str(text or "").split()).strip().strip("`\"'").strip().strip(".!?")
    normalized = normalized.lstrip("-:–—/ ").strip()
    return normalized


def _is_generic_hive_title_candidate(text: str) -> bool:
    normalized = _normalize_hive_title_candidate(text).lower()
    if normalized in _GENERIC_HIVE_TITLE_MARKERS or len(normalized) < 4:
        return True
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    if not tokens:
        return True
    generic_tokens = {"create", "creating", "new", "task", "tasks", "topic", "topics", "hive", "mind", "the", "this", "these"}
    return all(token in generic_tokens for token in tokens)


def _recover_hive_create_from_history(source_context: dict[str, Any] | None) -> tuple[str, str] | None:
    history = [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]
    for message in reversed(history[-8:]):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = " ".join(str(message.get("content") or "").split()).strip()
        lowered = content.lower()
        if not any(marker in lowered for marker in _HIVE_ACTION_PATTERNS) and " task:" not in lowered:
            continue
        sections = {
            "task": re.search(r"\btask\b\s*[:=-]\s*(.+?)(?=(?:\b(?:goal|summary)\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
            "title": re.search(r"\b(?:name it|title|call it|called)\b\s*[:=-]?\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
            "goal": re.search(r"\bgoal\b\s*[:=-]\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
            "summary": re.search(r"\bsummary\b\s*[:=-]\s*(.+?)(?=(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", content, re.IGNORECASE),
        }
        raw_title = ""
        if sections["title"] is not None:
            raw_title = str(sections["title"].group(1) or "")
        elif sections["task"] is not None:
            raw_title = str(sections["task"].group(1) or "")
        else:
            raw_title = content
            for prefix in _HIVE_CREATE_PREFIXES:
                if raw_title.lower().startswith(prefix):
                    raw_title = raw_title[len(prefix):].strip().lstrip("-:–").strip()
                    break
            if ":" in raw_title and raw_title.count(":") == 1:
                raw_title = raw_title.split(":", 1)[-1].strip()
        title = _normalize_hive_title_candidate(raw_title[:180])
        if _is_generic_hive_title_candidate(title):
            continue
        summary = ""
        if sections["summary"] is not None:
            summary = _normalize_hive_title_candidate(str(sections["summary"].group(1) or "")[:4000])
        elif sections["goal"] is not None:
            summary = _normalize_hive_title_candidate(str(sections["goal"].group(1) or "")[:4000])
        summary = summary or title
        return title, summary
    return None


def _recover_lookup_followup_from_history(source_context: dict[str, Any] | None) -> str:
    history = [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]
    for message in reversed(history[-8:]):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        content = " ".join(str(message.get("content") or "").split()).strip()
        if not content:
            continue
        if _looks_like_followup_resume_request(content):
            continue
        if looks_like_explicit_lookup_request(content) or looks_like_public_entity_lookup_request(content):
            return content
        break
    return ""


def _workflow_step_exists(steps: list[dict[str, Any]], intent: str, *, key: str | None = None, value: str | None = None) -> bool:
    normalized_intent = str(intent or "").strip()
    normalized_value = str(value or "").strip()
    for step in list(steps or []):
        if str(step.get("tool_name") or "").strip() != normalized_intent:
            continue
        if key is None:
            return True
        arguments = dict(step.get("arguments") or {})
        step_value = str(arguments.get(key) or "").strip()
        if step_value == normalized_value:
            return True
    return False


def _last_command_from_steps(steps: list[dict[str, Any]]) -> str:
    for step in reversed(list(steps or [])):
        if str(step.get("tool_name") or "").strip() != "sandbox.run_command":
            continue
        command = str(dict(step.get("arguments") or {}).get("command") or "").strip()
        if command:
            return command
    return ""


def _workflow_retry_already_happened(steps: list[dict[str, Any]], command: str) -> bool:
    normalized = str(command or "").strip()
    if not normalized:
        return False
    count = 0
    for step in list(steps or []):
        if str(step.get("tool_name") or "").strip() != "sandbox.run_command":
            continue
        step_command = str(dict(step.get("arguments") or {}).get("command") or "").strip()
        if step_command == normalized:
            count += 1
    return count >= 2


def _explicit_replace_request(user_text: str) -> dict[str, str] | None:
    text = str(user_text or "").strip()
    if not text:
        return None
    fenced = re.search(
        r"replace\s+`(?P<old>[^`]+)`\s+with\s+`(?P<new>[^`]+)`(?:\s+in\s+(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+))?",
        text,
        re.IGNORECASE,
    )
    if fenced:
        return {
            "old_text": str(fenced.group("old") or "").strip(),
            "new_text": str(fenced.group("new") or "").strip(),
            "path": _normalize_inline_path(str(fenced.group("path") or "").strip()),
        }
    plain = re.search(
        r"replace\s+(?P<old>[A-Za-z0-9_.:/-]+)\s+with\s+(?P<new>[A-Za-z0-9_.:/-]+)(?:\s+in\s+(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+))?",
        text,
        re.IGNORECASE,
    )
    if plain:
        return {
            "old_text": str(plain.group("old") or "").strip(),
            "new_text": str(plain.group("new") or "").strip(),
            "path": _normalize_inline_path(str(plain.group("path") or "").strip()),
        }
    return None


def _explicit_command_request(user_text: str) -> str:
    text = str(user_text or "").strip()
    if not text:
        return ""
    fenced = re.search(r"(?:run|execute|retry|rerun)\s+`(?P<command>[^`]+)`", text, re.IGNORECASE)
    if fenced:
        return _normalize_inline_command(str(fenced.group("command") or "").strip())
    common = re.search(
        r"\b(?:run|execute|retry|rerun)\s+(?P<command>(?:pytest(?:\s+-[A-Za-z0-9-]+)*(?:\s+[A-Za-z0-9_./:-]+)*)|(?:python3?\s+[A-Za-z0-9_./:-]+(?:\s+[A-Za-z0-9_./:=+-]+)*)|(?:npm\s+(?:test|run\s+[A-Za-z0-9:_-]+))|(?:cargo\s+test(?:\s+[A-Za-z0-9_./:-]+)*))",
        text,
        re.IGNORECASE,
    )
    if common:
        return _normalize_inline_command(str(common.group("command") or "").strip())
    return ""


def _normalize_inline_command(command: str) -> str:
    clean = " ".join(str(command or "").split()).strip()
    if not clean:
        return ""
    clean = re.sub(r"(?P<stem>[A-Za-z0-9_/-]+)\.\s+(?P<ext>py|js|ts|json|yaml|yml|toml|sh|md)\b", r"\g<stem>.\g<ext>", clean)
    return clean


def _normalize_inline_path(path: str) -> str:
    return _normalize_inline_command(path)


def plan_tool_workflow(
    *,
    user_text: str,
    task_class: str,
    executed_steps: list[dict[str, Any]],
    source_context: dict[str, Any] | None,
) -> WorkflowPlannerDecision:
    text = " ".join(str(user_text or "").split()).strip()
    lowered = f" {text.lower()} "
    followup_resume = _looks_like_followup_resume_request(text)
    steps = [dict(step) for step in list(executed_steps or []) if isinstance(step, dict)]
    replacement = _explicit_replace_request(text)
    explicit_command = _explicit_command_request(text)
    compare_or_verify = any(marker in lowered for marker in (" compare ", " versus ", " vs ", " verify ", " confirm ", " is it true "))
    lookup_followup_text = ""
    if followup_resume and not (looks_like_explicit_lookup_request(text) or looks_like_public_entity_lookup_request(text)):
        lookup_followup_text = _recover_lookup_followup_from_history(source_context)
    research_text = lookup_followup_text or text
    public_entity_lookup = looks_like_public_entity_lookup_request(research_text)
    explicit_lookup = looks_like_explicit_lookup_request(research_text) or public_entity_lookup
    tool_inventory_request = _looks_like_tool_inventory_request(text)
    machine_directory_list = _extract_safe_machine_directory_listing(text)
    workspace_bootstrap_path = _extract_workspace_bootstrap_path(text)
    workspace_bootstrap_request = _looks_like_workspace_bootstrap_request(text)
    workspace_file_plan = _extract_workspace_file_plan(text, source_context=source_context)
    entity_query, entity_retry_query = _entity_lookup_query_variants(research_text)
    last_step = dict(steps[-1] or {}) if steps else {}
    last_intent = str(last_step.get("tool_name") or "").strip()
    research_flow = bool(
        explicit_lookup
        or compare_or_verify
        or task_class in {"research", "chat_research", "system_design"}
        or any(marker in lowered for marker in (" latest ", " current ", " docs ", " documentation ", " research ", " source "))
        or (followup_resume and last_intent in {"web.search", "web.fetch", "web.research"})
    )

    create_task_markers = (
        " create task ", " create new task ", " new task for ", " add task ", " add to hive ", " add to the hive ",
        " create these tasks ", " create them ", " create these ", " yes create ", " yes create them ",
    )

    def _create_task_fuzzy(lo: str) -> bool:
        return ("create" in lo and "task" in lo) or ("create" in lo and ("hive" in lo or "topic" in lo))

    def _proceed_with_task(lo: str) -> bool:
        return any(m in lo for m in (" proceed ", " do it ", " do all ", " start working ", " go ahead ", " carry on ")) and (
            "task" in lo or "hive" in lo or "create" in lo
        )

    if not steps:
        if lookup_followup_text and explicit_lookup:
            if public_entity_lookup:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_entity_lookup_search",
                    next_payload={"intent": "web.search", "arguments": {"query": entity_query or research_text, "limit": 4}},
                )
            if research_flow:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_research_search",
                    next_payload={"intent": "web.search", "arguments": {"query": research_text, "limit": 4}},
                )
        if any(marker in lowered for marker in create_task_markers) or _create_task_fuzzy(lowered) or _proceed_with_task(lowered):
            raw_title = text.strip()
            for prefix in _HIVE_CREATE_PREFIXES:
                if raw_title.lower().startswith(prefix):
                    raw_title = raw_title[len(prefix):].strip().lstrip("-:–").strip()
                    break
            if "task:" in lowered:
                task_match = re.search(r"\btask\b\s*[:=-]\s*(.+?)(?=(?:\b(?:goal|summary)\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", text, re.IGNORECASE)
                if task_match is not None:
                    raw_title = str(task_match.group(1) or "").strip()
            title = _normalize_hive_title_candidate(raw_title[:180])
            if _is_generic_hive_title_candidate(title):
                recovered = _recover_hive_create_from_history(source_context)
                if recovered is None:
                    return WorkflowPlannerDecision(handled=False, reason="no_workflow_plan")
                title, recovered_summary = recovered
                summary = recovered_summary[:4000] or title
            else:
                summary = text.strip()[:4000] or title
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_hive_create_topic",
                next_payload={
                    "intent": "hive.create_topic",
                    "arguments": {"title": title, "summary": summary, "topic_tags": ["research"]},
                },
            )
        if tool_inventory_request:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_operator_tool_inventory",
                next_payload={"intent": "operator.list_tools", "arguments": {}},
            )
        if machine_directory_list:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_safe_machine_directory_list",
                next_payload={"intent": "machine.list_directory", "arguments": machine_directory_list},
            )
        if workspace_file_plan is not None:
            pending_writes = _pending_workspace_writes(workspace_file_plan, steps)
            planned_directory = str(workspace_file_plan.get("directory") or "").strip()
            if planned_directory:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_workspace_directory_bootstrap",
                    next_payload={"intent": "workspace.ensure_directory", "arguments": {"path": planned_directory}},
                )
            if pending_writes:
                first_write = dict(pending_writes[0] or {})
                if str(first_write.get("mode") or "").strip() == "append":
                    return WorkflowPlannerDecision(
                        handled=True,
                        reason="planned_read_before_append",
                        next_payload={"intent": "workspace.read_file", "arguments": {"path": first_write["path"], "start_line": 1, "max_lines": 400, "verbatim": True}},
                    )
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_workspace_write_file",
                    next_payload={"intent": "workspace.write_file", "arguments": {"path": first_write["path"], "content": first_write["content"]}},
                )
            read_path = str(workspace_file_plan.get("read_path") or "").strip()
            if read_path:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_workspace_readback",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": read_path, "start_line": 1, "max_lines": 400, "verbatim": bool(workspace_file_plan.get("verbatim_read", False))}},
                )
        if workspace_bootstrap_request and workspace_bootstrap_path:
            wants_desktop = any(m in lowered for m in (" desktop ", " on my desktop", " my desktop", " on desktop", "~/desktop"))
            wants_home = any(m in lowered for m in (" home ", " home/", " my machine", " this machine", "~/", "folder in my machine"))
            home_dir = str(Path.home())
            if wants_desktop:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_desktop_directory_create",
                    next_payload={"intent": "sandbox.run_command", "arguments": {"command": f"mkdir -p {home_dir}/Desktop/{workspace_bootstrap_path}"}},
                )
            if wants_home:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_home_directory_create",
                    next_payload={"intent": "sandbox.run_command", "arguments": {"command": f"mkdir -p {home_dir}/{workspace_bootstrap_path}"}},
                )
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_directory_bootstrap",
                next_payload={"intent": "workspace.ensure_directory", "arguments": {"path": workspace_bootstrap_path}},
            )
        if explicit_command and any(marker in lowered for marker in (" retry ", " then retry", " then rerun", " rerun ")):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_diagnose_run",
                next_payload={"intent": "sandbox.run_command", "arguments": {"command": explicit_command}},
            )
        if replacement is not None:
            path = str(replacement.get("path") or "").strip()
            if path:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_read_before_edit",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": path, "start_line": 1, "max_lines": 120}},
                )
            query = str(replacement.get("old_text") or "").strip()
            if query:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_search_before_edit",
                    next_payload={"intent": "workspace.search_text", "arguments": {"query": query, "limit": 10}},
                )
        if public_entity_lookup:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_entity_lookup_search",
                next_payload={"intent": "web.search", "arguments": {"query": entity_query or text, "limit": 4}},
            )
        if research_flow:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_research_search",
                next_payload={"intent": "web.search", "arguments": {"query": research_text, "limit": 4}},
            )
        if explicit_command:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_command_run",
                next_payload={"intent": "sandbox.run_command", "arguments": {"command": explicit_command}},
            )
        return WorkflowPlannerDecision(handled=False, reason="no_workflow_plan")

    last_observation = dict(last_step.get("observation") or {})
    hints = extract_observation_followup_hints(last_observation)

    if research_flow:
        if last_intent == "web.search":
            current_query = str(dict(last_step.get("arguments") or {}).get("query") or "").strip()
            result_count = int(hints.get("result_count") or 0)
            if public_entity_lookup and result_count <= 0:
                if entity_retry_query and entity_retry_query != current_query and not _workflow_step_exists(steps, "web.search", key="query", value=entity_retry_query):
                    return WorkflowPlannerDecision(
                        handled=True,
                        reason="planned_entity_lookup_retry",
                        next_payload={"intent": "web.search", "arguments": {"query": entity_retry_query, "limit": 4}},
                    )
                if not _workflow_step_exists(steps, "web.research"):
                    return WorkflowPlannerDecision(
                        handled=True,
                        reason="planned_entity_lookup_research",
                        next_payload={"intent": "web.research", "arguments": {"query": entity_retry_query or entity_query or research_text}},
                    )
            if not compare_or_verify and int(hints.get("result_count") or 0) >= 2:
                return WorkflowPlannerDecision(handled=True, reason="research_enough_after_search", stop_after=True)
            primary_url = str(hints.get("primary_url") or "").strip()
            if primary_url and not _workflow_step_exists(steps, "web.fetch", key="url", value=primary_url):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_fetch_after_search",
                    next_payload={"intent": "web.fetch", "arguments": {"url": primary_url}},
                )
            if compare_or_verify and not _workflow_step_exists(steps, "web.research"):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_verify_after_search",
                    next_payload={"intent": "web.research", "arguments": {"query": research_text}},
                )
            if public_entity_lookup and not _workflow_step_exists(steps, "web.research") and result_count < 2:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_entity_lookup_verify",
                    next_payload={"intent": "web.research", "arguments": {"query": entity_retry_query or entity_query or research_text}},
                )
            return WorkflowPlannerDecision(handled=True, reason="research_stop_after_search", stop_after=True)
        if last_intent == "web.fetch":
            if compare_or_verify and not _workflow_step_exists(steps, "web.research"):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_research_after_fetch",
                    next_payload={"intent": "web.research", "arguments": {"query": research_text}},
                )
            return WorkflowPlannerDecision(handled=True, reason="research_stop_after_fetch", stop_after=True)
        if last_intent == "web.research":
            return WorkflowPlannerDecision(handled=True, reason="research_stop_after_verify", stop_after=True)

    if last_intent == "workspace.search_text":
        path = str(hints.get("primary_path") or "").strip()
        line = int(hints.get("primary_line") or 0)
        if path and not _workflow_step_exists(steps, "workspace.read_file", key="path", value=path):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_read_after_search",
                next_payload={
                    "intent": "workspace.read_file",
                    "arguments": {
                        "path": path,
                        "start_line": max(1, line - 8) if line else 1,
                        "max_lines": 60,
                    },
                },
            )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_search", stop_after=True)

    if last_intent == "workspace.read_file":
        read_path = str(hints.get("path") or "").strip()
        pending_writes = _pending_workspace_writes(workspace_file_plan or {}, steps)
        if workspace_file_plan is not None and pending_writes:
            next_write = dict(pending_writes[0] or {})
            if str(next_write.get("mode") or "").strip() == "append" and read_path == str(next_write.get("path") or "").strip():
                existing_content = str(hints.get("content") or "")
                if existing_content:
                    content = existing_content + ("\n" if not existing_content.endswith("\n") else "") + str(next_write.get("content") or "")
                else:
                    content = str(next_write.get("content") or "")
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_append_after_read",
                    next_payload={"intent": "workspace.write_file", "arguments": {"path": read_path, "content": content}},
                )
        if replacement is not None:
            target_path = str(replacement.get("path") or read_path).strip()
            old_text = str(replacement.get("old_text") or "").strip()
            new_text = str(replacement.get("new_text") or "").strip()
            if target_path and old_text and new_text and not _workflow_step_exists(steps, "workspace.replace_in_file", key="path", value=target_path):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_edit_after_read",
                    next_payload={
                        "intent": "workspace.replace_in_file",
                        "arguments": {
                            "path": target_path,
                            "old_text": old_text,
                            "new_text": new_text,
                            "replace_all": True,
                        },
                    },
                )
        if explicit_command and not _workflow_step_exists(steps, "sandbox.run_command", key="command", value=explicit_command):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_command_after_read",
                next_payload={"intent": "sandbox.run_command", "arguments": {"command": explicit_command}},
            )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_read", stop_after=True)

    if last_intent == "workspace.ensure_directory":
        pending_writes = _pending_workspace_writes(workspace_file_plan or {}, steps)
        if workspace_file_plan is not None and pending_writes:
            next_write = dict(pending_writes[0] or {})
            if str(next_write.get("mode") or "").strip() == "append":
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_read_before_append",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": next_write["path"], "start_line": 1, "max_lines": 400, "verbatim": True}},
                )
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_write_after_bootstrap",
                next_payload={"intent": "workspace.write_file", "arguments": {"path": next_write["path"], "content": next_write["content"]}},
            )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_directory_bootstrap", stop_after=True)

    if last_intent == "workspace.write_file":
        pending_writes = _pending_workspace_writes(workspace_file_plan or {}, steps)
        if workspace_file_plan is not None and pending_writes:
            next_write = dict(pending_writes[0] or {})
            if str(next_write.get("mode") or "").strip() == "append":
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_read_before_append",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": next_write["path"], "start_line": 1, "max_lines": 400, "verbatim": True}},
                )
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_next_write",
                next_payload={"intent": "workspace.write_file", "arguments": {"path": next_write["path"], "content": next_write["content"]}},
            )
        list_path = str((workspace_file_plan or {}).get("list_path") or "").strip()
        if list_path and not _workflow_step_exists(steps, "workspace.list_files", key="path", value=list_path):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_workspace_list_after_write",
                next_payload={"intent": "workspace.list_files", "arguments": {"path": list_path, "limit": 200}},
            )
        if explicit_command and not _workflow_step_exists(steps, "sandbox.run_command", key="command", value=explicit_command):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_command_after_write",
                next_payload={"intent": "sandbox.run_command", "arguments": {"command": explicit_command}},
            )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_write", stop_after=True)

    if last_intent == "workspace.list_files":
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_list", stop_after=True)

    if last_intent == "machine.list_directory":
        return WorkflowPlannerDecision(handled=True, reason="machine_stop_after_list", stop_after=True)

    if last_intent == "workspace.replace_in_file":
        retry_command = _last_command_from_steps(steps) or explicit_command
        if retry_command and not _workflow_retry_already_happened(steps, retry_command):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_retry_after_edit",
                next_payload={"intent": "sandbox.run_command", "arguments": {"command": retry_command}},
            )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_edit", stop_after=True)

    if last_intent == "sandbox.run_command":
        returncode = int(hints.get("returncode") or 0)
        if returncode == 0:
            return WorkflowPlannerDecision(handled=True, reason="command_stop_after_success", stop_after=True)
        error_path = str(hints.get("error_path") or "").strip()
        error_line = int(hints.get("error_line") or 0)
        if error_path and not _workflow_step_exists(steps, "workspace.read_file", key="path", value=error_path):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_inspect_after_command_failure",
                next_payload={
                    "intent": "workspace.read_file",
                    "arguments": {
                        "path": error_path,
                        "start_line": max(1, error_line - 8) if error_line else 1,
                        "max_lines": 60,
                    },
                },
            )
        if replacement is not None:
            target_path = str(replacement.get("path") or "").strip()
            if target_path and not _workflow_step_exists(steps, "workspace.read_file", key="path", value=target_path):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_explicit_inspect_after_command_failure",
                    next_payload={"intent": "workspace.read_file", "arguments": {"path": target_path, "start_line": 1, "max_lines": 120}},
                )
        diagnostic_query = str(hints.get("diagnostic_query") or "").strip()
        if diagnostic_query and not _workflow_step_exists(steps, "workspace.search_text", key="query", value=diagnostic_query):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_search_after_command_failure",
                next_payload={"intent": "workspace.search_text", "arguments": {"query": diagnostic_query, "limit": 10}},
            )
        return WorkflowPlannerDecision(handled=True, reason="command_stop_after_failure", stop_after=True)

    return WorkflowPlannerDecision(handled=False, reason="no_followup_plan")
