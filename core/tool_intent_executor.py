from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core import audit_logger, policy_engine
from core.autonomous_topic_research import research_topic_from_signal
from core.curiosity_roamer import CuriosityRoamer
from core.hive_activity_tracker import HiveActivityTracker, load_hive_activity_tracker_config
from core.local_operator_actions import (
    OperatorActionIntent,
    dispatch_operator_action,
    list_operator_tools,
    operator_capability_ledger,
)
from core.public_hive_bridge import PublicHiveBridge, load_public_hive_bridge_config, public_hive_write_enabled
from core.runtime_continuity import (
    build_tool_receipt_key,
    is_mutating_tool_intent,
    load_tool_receipt,
    store_tool_receipt,
)
from core.runtime_execution_tools import (
    execute_runtime_tool,
    extract_observation_followup_hints,
    looks_like_execution_request,
    runtime_execution_capability_ledger,
    runtime_execution_tool_specs,
)
from core.task_router import looks_like_explicit_lookup_request, looks_like_public_entity_lookup_request
from retrieval.web_adapter import WebAdapter
from tools.registry import call_tool, load_builtin_tools

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_LIVE_LOOKUP_MARKERS = (
    "latest",
    "current",
    "today",
    "recent",
    "release notes",
    "version",
    "status page",
    "search online",
    "check online",
    "look up",
    "fetch",
    "pull",
    "open",
    "browse",
    "render",
    "show me",
    "on x",
    "on twitter",
    "on the web",
    "on web",
    "google",
    "find",
    "check",
)
_LOCAL_TOOL_MARKERS = (
    "process",
    "service",
    "disk",
    "space",
    "cleanup",
    "clean temp",
    "move",
    "archive",
    "calendar",
    "meeting",
    "schedule",
    "tool",
    "folder",
    "directory",
    "mkdir",
)
_TOOL_INVENTORY_MARKERS = (
    "list tools",
    "show tools",
    "what tools do you have",
    "what can you execute",
    "what actions can you take",
    "what tools do you need",
    "which tools do you need",
    "what would you use",
)
_SELF_TOOL_REQUEST_MARKERS = (
    "create your own tool",
    "create your own tools",
    "make your own tool",
    "make your own tools",
    "build your own tool",
    "build your own tools",
    "register a new tool",
    "register new tools",
)
_DIRECTORY_CREATE_MARKERS = (
    "create folder",
    "create a folder",
    "create directory",
    "create a directory",
    "make folder",
    "make a folder",
    "set up folder",
    "setup folder",
    "set up directory",
    "setup directory",
    "mkdir",
)
_START_CODE_MARKERS = (
    "start coding",
    "start putting code",
    "put code",
    "putting code",
    "write the initial files",
    "initial files",
    "starter files",
    "bootstrap",
)
_NAMED_PATH_RE = re.compile(
    r"(?:named?|called|call)\s+(?:it\s+)?[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)[`\"']?",
    re.IGNORECASE,
)
_VERB_NAME_FOLDER_RE = re.compile(
    r"\b(?:create|make|crate|creat|mkdir)\s+(?:the\s+|a\s+|an\s+)?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)\s+(?:folder|directory|dir)\b",
    re.IGNORECASE,
)
_FOLDER_PATH_RE = re.compile(
    r"\b(?:folder|directory|dir|path)\s+(?:called|named)?\s*[`\"']?(?P<path>[A-Za-z0-9_./-]+)",
    re.IGNORECASE,
)
_CREATE_PATH_RE = re.compile(
    r"\b(?:create|make|setup|set up|bootstrap|mkdir)\s+(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)\b",
    re.IGNORECASE,
)
_INTO_PATH_RE = re.compile(
    r"\b(?:in|under|inside)\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)[`\"']?",
    re.IGNORECASE,
)
_PATH_STOP_WORDS = {
    "a",
    "an",
    "the",
    "for",
    "me",
    "my",
    "this",
    "that",
    "it",
    "on",
    "in",
    "folder",
    "directory",
    "dir",
    "path",
    "workspace",
    "repo",
    "repository",
    "there",
    "here",
    "code",
    "files",
    "machine",
    "computer",
    "desktop",
}
_BUILDER_RESEARCH_MARKERS = (
    "build",
    "design",
    "architecture",
    "best practice",
    "best practices",
    "framework",
    "stack",
    "github",
    "repo",
    "repos",
    "docs",
    "documentation",
    "compare",
    "example",
    "examples",
)
_INTEGRATION_DOMAIN_MARKERS = (
    "telegram",
    "discord",
    "bot",
    "api",
    "integration",
    "webhook",
)
_HIVE_ACTION_PATTERNS = (
    "claim task",
    "claim this task",
    "claim topic",
    "take this task",
    "take this topic",
    "create topic",
    "create task",
    "create new task",
    "create hive mind task",
    "create hive task",
    "new task",
    "add task",
    "add to hive",
    "add to the hive",
    "open topic",
    "post progress",
    "update progress",
    "submit result",
    "submit findings",
    "submit verdict",
    "research packet",
    "research queue",
    "search artifacts",
    "research this topic",
)
_ENTITY_LOOKUP_DROP_TOKENS = frozenset(
    {
        "who",
        "is",
        "he",
        "she",
        "they",
        "them",
        "tell",
        "me",
        "about",
        "what",
        "do",
        "you",
        "know",
        "check",
        "find",
        "look",
        "up",
        "lookup",
        "search",
        "google",
        "in",
        "on",
        "the",
        "web",
        "pls",
        "please",
    }
)
_ENTITY_LOOKUP_KEEP_SHORT_TOKENS = frozenset({"x", "ai"})
_READ_ONLY_OPERATOR_INTENTS = {
    "operator.list_tools",
    "operator.inspect_processes",
    "operator.inspect_services",
    "operator.inspect_disk_usage",
}
_MUTATING_OPERATOR_INTENTS = {
    "operator.cleanup_temp_files",
    "operator.move_path",
    "operator.schedule_calendar_event",
}
_WEB_TOOL_INTENTS = {
    "web.search",
    "web.fetch",
    "web.research",
    "browser.render",
}
_HIVE_TOOL_INTENTS = {
    "hive.list_available",
    "hive.list_research_queue",
    "hive.export_research_packet",
    "hive.search_artifacts",
    "hive.research_topic",
    "hive.create_topic",
    "hive.claim_task",
    "hive.post_progress",
    "hive.submit_result",
}
_SUPPORTED_OPERATOR_TOOL_IDS = {
    "list_tools",
    "inspect_processes",
    "inspect_services",
    "inspect_disk_usage",
    "cleanup_temp_files",
    "move_path",
    "schedule_calendar_event",
}
_CAPABILITY_QUERY_PREFIXES = (
    "can you ",
    "could you ",
    "are you able to ",
    "do you have a way to ",
    "do you know how to ",
    "are you wired to ",
)
_IMPOSSIBLE_REQUEST_MARKERS = (
    "read my mind",
    "mind read",
    "teleport",
    "physically cook",
    "cook dinner",
    "taste this",
    "smell this",
    "touch this",
    "be physically there",
    "drive over",
    "hack a bank",
    "steal a password",
)
_PARTIAL_BUILD_MARKERS = (
    "full app",
    "entire app",
    "end to end app",
    "end-to-end app",
    "full product",
    "ship the whole app",
    "ios app",
    "android app",
    "mobile app",
)
_SWARM_DELEGATION_MARKERS = (
    "talk to other agents",
    "delegate to other agents",
    "delegate this to agents",
    "helper lane",
    "merge helper outputs",
    "swarm delegates",
    "other hive agents",
)
_EMAIL_SEND_MARKERS = (
    "send email",
    "send an email",
    "email this",
    "mail this",
    "reply by email",
)
_NEARBY_CAPABILITY_IDS = {
    "workspace.read": ["web.live_lookup"],
    "workspace.write": ["workspace.read", "sandbox.command"],
    "sandbox.command": ["workspace.read", "workspace.write"],
    "hive.write": ["hive.read"],
    "operator.discord_post": ["operator.telegram_send"],
    "operator.telegram_send": ["operator.discord_post"],
    "workspace.build_scaffold": ["workspace.write", "sandbox.command"],
}


@dataclass
class ToolIntentExecution:
    handled: bool
    ok: bool
    status: str
    response_text: str = ""
    user_safe_response_text: str = ""
    mode: str = "tool_failed"
    tool_name: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    learned_plan: Any = None


@dataclass
class WorkflowPlannerDecision:
    handled: bool
    reason: str
    next_payload: dict[str, Any] | None = None
    stop_after: bool = False


def _tool_observation(
    *,
    intent: str,
    tool_surface: str,
    ok: bool,
    status: str,
    **payload: Any,
) -> dict[str, Any]:
    observation = {
        "schema": "tool_observation_v1",
        "intent": str(intent or "").strip(),
        "tool_surface": str(tool_surface or "").strip(),
        "ok": bool(ok),
        "status": str(status or "").strip(),
    }
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        observation[str(key)] = value
    return observation


def runtime_capability_ledger() -> list[dict[str, Any]]:
    hive_cfg = load_hive_activity_tracker_config()
    public_hive_cfg = load_public_hive_bridge_config()
    hive_read_supported = bool(
        (hive_cfg.enabled and hive_cfg.watcher_api_url)
        or (public_hive_cfg.enabled and public_hive_cfg.topic_target_url)
    )
    hive_write_supported = bool(
        public_hive_cfg.enabled
        and public_hive_cfg.topic_target_url
        and public_hive_write_enabled(public_hive_cfg)
    )
    entries: list[dict[str, Any]] = [
        {
            "capability_id": "web.live_lookup",
            "surface": "web",
            "claim": "run live web search, fetch pages, bounded web research, and browser rendering",
            "supported": bool(policy_engine.allow_web_fallback()),
            "unsupported_reason": "Live web lookup is disabled on this runtime.",
            "intents": sorted(_WEB_TOOL_INTENTS),
            "public_tag": "web.live_lookup",
        },
        {
            "capability_id": "hive.read",
            "surface": "hive",
            "claim": "list live Hive tasks and read research queues, packets, and artifact search results",
            "supported": hive_read_supported,
            "unsupported_reason": "Live Hive read actions are not enabled on this runtime.",
            "intents": [
                "hive.list_available",
                "hive.list_research_queue",
                "hive.export_research_packet",
                "hive.search_artifacts",
            ],
            "public_tag": "hive.read",
        },
        {
            "capability_id": "hive.write",
            "surface": "hive",
            "claim": "create, claim, update, and submit real Hive topics or tasks",
            "supported": hive_write_supported,
            "unsupported_reason": "Live Hive write actions are not enabled on this runtime.",
            "intents": [
                "hive.research_topic",
                "hive.create_topic",
                "hive.claim_task",
                "hive.post_progress",
                "hive.submit_result",
            ],
            "public_tag": "hive.write",
        },
    ]
    entries.extend(runtime_execution_capability_ledger())
    entries.extend(operator_capability_ledger())
    return [_annotate_capability_entry(entry) for entry in entries]


def capability_entry_for_intent(intent: str) -> dict[str, Any] | None:
    normalized = str(intent or "").strip()
    if not normalized:
        return None
    for entry in runtime_capability_ledger():
        if normalized in {str(item).strip() for item in list(entry.get("intents") or []) if str(item).strip()}:
            return dict(entry)
    return None


def capability_gap_for_intent(
    intent: str,
    *,
    extra_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_intent = str(intent or "").strip()
    all_entries = _all_capability_entries(extra_entries=extra_entries)
    entry = next(
        (
            candidate
            for candidate in all_entries
            if normalized_intent in {str(item).strip() for item in list(candidate.get("intents") or []) if str(item).strip()}
        ),
        None,
    )
    if entry is not None:
        return _capability_gap_from_entry(
            entry,
            requested_label=normalized_intent,
            extra_entries=all_entries,
        )
    synthetic = {
        "requested_capability": normalized_intent or "unknown.intent",
        "requested_label": normalized_intent or "unknown action",
        "support_level": "unsupported",
        "gap_kind": _synthetic_gap_kind_for_intent(normalized_intent),
        "reason": f"`{normalized_intent}` is not wired on this runtime." if normalized_intent else "That action is not wired on this runtime.",
        "nearby_alternatives": _nearby_alternatives_for_unknown_intent(normalized_intent, all_entries),
    }
    return synthetic


def capability_truth_for_request(
    user_text: str,
    *,
    extra_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    text = " ".join(str(user_text or "").split()).strip()
    if not text:
        return None
    lowered = f" {text.lower()} "
    if any(marker in lowered for marker in _IMPOSSIBLE_REQUEST_MARKERS):
        return {
            "requested_capability": "physical_or_impossible_action",
            "requested_label": text,
            "support_level": "impossible",
            "gap_kind": "impossible",
            "reason": "That is outside what this runtime can actually do.",
            "nearby_alternatives": ["I can still reason about it, plan it, or help write instructions for a human to carry out."],
        }
    all_entries = _all_capability_entries(extra_entries=extra_entries)
    if any(marker in lowered for marker in _EMAIL_SEND_MARKERS):
        return {
            "requested_capability": "email.send",
            "requested_label": "send email",
            "support_level": "unsupported",
            "gap_kind": "unwired",
            "reason": "Email sending is not wired on this runtime.",
            "nearby_alternatives": _combine_alternative_text(
                [
                    "I can draft the email text here.",
                    *_nearby_alternatives_from_capability_ids(["operator.discord_post", "operator.telegram_send"], all_entries),
                ]
            ),
        }
    if any(marker in lowered for marker in _SWARM_DELEGATION_MARKERS):
        return {
            "requested_capability": "swarm.delegate_merge",
            "requested_label": "delegate to other agents and merge their outputs",
            "support_level": "unsupported",
            "gap_kind": "future_unsupported",
            "reason": "Real multi-agent delegation and merge synthesis are not wired on this runtime yet.",
            "nearby_alternatives": _combine_alternative_text(
                _nearby_alternatives_from_capability_ids(["hive.read", "hive.write"], all_entries)
            ),
        }
    if any(marker in lowered for marker in _SELF_TOOL_REQUEST_MARKERS):
        return {
            "requested_capability": "tooling.self_extension",
            "requested_label": "create or register new tools on the fly",
            "support_level": "partial",
            "claim": (
                "I can create task-local helper files or scripts inside the active workspace and then use them through bounded workspace writes and local commands."
            ),
            "partial_reason": (
                "I still cannot auto-register brand-new first-class runtime tools or extend the global tool registry on my own."
            ),
            "reason": "Self-extension is only partially wired on this runtime.",
            "nearby_alternatives": _combine_alternative_text(
                [
                    "Ask me to create a helper script or module in the workspace and run it locally.",
                    *_nearby_alternatives_from_capability_ids(["workspace.write", "sandbox.command"], all_entries),
                ]
            ),
        }
    if any(marker in lowered for marker in _PARTIAL_BUILD_MARKERS) and any(
        marker in lowered for marker in (" build ", " create ", " make ", " ship ", " code ", " develop ")
    ):
        build_entry = next(
            (entry for entry in all_entries if str(entry.get("capability_id") or "").strip() == "workspace.build_scaffold"),
            None,
        )
        if build_entry is not None:
            return _capability_gap_from_entry(
                build_entry,
                requested_label="build a full application end to end",
                extra_entries=all_entries,
            )
    if any(lowered.strip().startswith(prefix) for prefix in _CAPABILITY_QUERY_PREFIXES):
        return None
    return None


def render_capability_truth_response(report: dict[str, Any] | None) -> str:
    payload = dict(report or {})
    support_level = str(payload.get("support_level") or "unsupported").strip().lower()
    reason = str(payload.get("reason") or "That capability is not available on this runtime.").strip()
    claim = str(payload.get("claim") or "").strip()
    partial_reason = str(payload.get("partial_reason") or "").strip()
    if support_level == "partial":
        base = f"Partially. {claim or reason}".strip()
        if partial_reason:
            base = f"{base} {partial_reason}".strip()
    elif support_level == "impossible":
        base = f"No. {reason}".strip()
    else:
        base = f"No. {reason}".strip()
    alternatives = [str(item).strip() for item in list(payload.get("nearby_alternatives") or []) if str(item).strip()]
    if not alternatives:
        return base
    if len(alternatives) == 1:
        return f"{base} Closest real alternative here: {alternatives[0]}".strip()
    return f"{base} Nearby real alternatives here: {'; '.join(alternatives[:3])}".strip()


def supported_public_capability_tags(*, limit: int = 16) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for entry in runtime_capability_ledger():
        if not entry.get("supported"):
            continue
        tag = str(entry.get("public_tag") or entry.get("capability_id") or "").strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag[:64])
        if len(tags) >= max(1, int(limit)):
            break
    return tags


def _all_capability_entries(*, extra_entries: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    entries = [dict(entry) for entry in runtime_capability_ledger()]
    for entry in list(extra_entries or []):
        if not isinstance(entry, dict):
            continue
        entries.append(_annotate_capability_entry(entry))
    return entries


def _annotate_capability_entry(entry: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(entry or {})
    capability_id = str(enriched.get("capability_id") or "").strip()
    supported = bool(enriched.get("supported"))
    support_level = str(enriched.get("support_level") or "").strip().lower()
    partial_reason = str(enriched.get("partial_reason") or "").strip()
    if support_level not in {"full", "partial", "unsupported"}:
        if partial_reason and supported:
            support_level = "partial"
        elif supported:
            support_level = "full"
        else:
            support_level = "unsupported"
    enriched["support_level"] = support_level
    if "gap_kind" not in enriched or not str(enriched.get("gap_kind") or "").strip():
        unsupported_reason = str(enriched.get("unsupported_reason") or "").strip().lower()
        if support_level == "partial":
            enriched["gap_kind"] = "partial_support"
        elif "disabled" in unsupported_reason:
            enriched["gap_kind"] = "disabled"
        elif "missing auth" in unsupported_reason:
            enriched["gap_kind"] = "missing_auth"
        elif "not configured" in unsupported_reason:
            enriched["gap_kind"] = "not_configured"
        elif "future" in unsupported_reason:
            enriched["gap_kind"] = "future_unsupported"
        else:
            enriched["gap_kind"] = "unwired"
    if "nearby_capability_ids" not in enriched:
        enriched["nearby_capability_ids"] = list(_NEARBY_CAPABILITY_IDS.get(capability_id, []))
    return enriched


def _capability_gap_from_entry(
    entry: dict[str, Any],
    *,
    requested_label: str,
    extra_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    annotated = _annotate_capability_entry(entry)
    all_entries = _all_capability_entries(extra_entries=extra_entries)
    support_level = str(annotated.get("support_level") or "unsupported").strip().lower()
    claim = str(annotated.get("claim") or "").strip()
    reason = (
        str(annotated.get("partial_reason") or "").strip()
        if support_level == "partial"
        else str(annotated.get("unsupported_reason") or claim or "").strip()
    )
    if support_level == "full" and bool(annotated.get("supported")):
        reason = claim
    return {
        "requested_capability": str(annotated.get("capability_id") or requested_label).strip(),
        "requested_label": requested_label,
        "support_level": support_level,
        "gap_kind": str(annotated.get("gap_kind") or "unwired").strip(),
        "claim": claim,
        "partial_reason": str(annotated.get("partial_reason") or "").strip(),
        "reason": reason,
        "nearby_alternatives": _combine_alternative_text(
            _nearby_alternatives_from_capability_ids(
                [str(item).strip() for item in list(annotated.get("nearby_capability_ids") or []) if str(item).strip()],
                all_entries,
            )
        ),
    }


def _nearby_alternatives_from_capability_ids(
    capability_ids: list[str],
    entries: list[dict[str, Any]],
) -> list[str]:
    entry_map = {
        str(entry.get("capability_id") or "").strip(): dict(entry)
        for entry in list(entries or [])
        if str(entry.get("capability_id") or "").strip()
    }
    alternatives: list[str] = []
    for capability_id in list(capability_ids or []):
        entry = dict(entry_map.get(str(capability_id).strip()) or {})
        if not entry:
            continue
        support_level = str(entry.get("support_level") or "unsupported").strip().lower()
        if support_level == "unsupported" and not bool(entry.get("supported")):
            continue
        claim = str(entry.get("claim") or "").strip()
        if not claim:
            continue
        if support_level == "partial" and str(entry.get("partial_reason") or "").strip():
            alternatives.append(f"{claim} ({str(entry.get('partial_reason') or '').strip()})")
        else:
            alternatives.append(claim)
    return alternatives


def _nearby_alternatives_for_unknown_intent(intent: str, entries: list[dict[str, Any]]) -> list[str]:
    normalized = str(intent or "").strip().lower()
    if normalized.startswith("workspace."):
        return _combine_alternative_text(_nearby_alternatives_from_capability_ids(["workspace.read", "workspace.write"], entries))
    if normalized.startswith("sandbox."):
        return _combine_alternative_text(_nearby_alternatives_from_capability_ids(["workspace.read", "sandbox.command"], entries))
    if normalized.startswith("web.") or normalized.startswith("browser."):
        return _combine_alternative_text(_nearby_alternatives_from_capability_ids(["web.live_lookup"], entries))
    if normalized.startswith("hive."):
        return _combine_alternative_text(_nearby_alternatives_from_capability_ids(["hive.read", "hive.write"], entries))
    if normalized.startswith("operator."):
        return _combine_alternative_text(_nearby_alternatives_from_capability_ids(["operator.inspect_processes", "operator.inspect_disk_usage"], entries))
    return []


def _synthetic_gap_kind_for_intent(intent: str) -> str:
    normalized = str(intent or "").strip().lower()
    if normalized.startswith(("web.", "browser.", "workspace.", "sandbox.", "hive.", "operator.")):
        return "unwired"
    return "unsupported"


def _combine_alternative_text(items: list[str]) -> list[str]:
    seen: set[str] = set()
    combined: list[str] = []
    for item in list(items or []):
        clean = str(item or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        combined.append(clean)
    return combined


def _unsupported_execution_for_intent(
    intent: str,
    *,
    status: str,
    user_safe_override: str | None = None,
    extra_details: dict[str, Any] | None = None,
) -> ToolIntentExecution:
    gap = capability_gap_for_intent(intent)
    if status in {"disabled", "not_configured", "missing_auth"}:
        gap["gap_kind"] = status
    response = render_capability_truth_response(gap)
    user_safe = str(user_safe_override or response).strip()
    details = {
        "capability_gap": gap,
        **dict(extra_details or {}),
        "observation": _tool_observation(
            intent=intent,
            tool_surface="tool_intent",
            ok=False,
            status=status,
            capability_gap=gap,
        ),
    }
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status=status,
        response_text=response,
        user_safe_response_text=user_safe,
        mode="tool_failed",
        tool_name=intent,
        details=details,
    )


def runtime_tool_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {
            "intent": "respond.direct",
            "description": "No tool call. Use this when the user only needs a normal answer.",
            "read_only": True,
            "arguments": {},
        },
        {
            "intent": "operator.list_tools",
            "description": "List the actually wired runtime tools available in OpenClaw right now.",
            "read_only": True,
            "arguments": {},
        },
    ]
    if policy_engine.allow_web_fallback():
        specs.extend(
            [
                {
                    "intent": "web.search",
                    "description": "Search the web and return live result links/snippets.",
                    "read_only": True,
                    "arguments": {"query": "string", "limit": "integer optional"},
                },
                {
                    "intent": "web.fetch",
                    "description": "Fetch text from a specific URL.",
                    "read_only": True,
                    "arguments": {"url": "https URL", "timeout_s": "number optional"},
                },
                {
                    "intent": "web.research",
                    "description": "Run bounded search plus page fetches for a query.",
                    "read_only": True,
                    "arguments": {
                        "query": "string",
                        "max_hits": "integer optional",
                        "max_pages": "integer optional",
                    },
                },
                {
                    "intent": "browser.render",
                    "description": "Render a JS-heavy URL when browser fallback is enabled.",
                    "read_only": True,
                    "arguments": {"url": "https URL"},
                },
            ]
        )
    specs.extend(runtime_execution_tool_specs())

    hive_cfg = load_hive_activity_tracker_config()
    public_hive_cfg = load_public_hive_bridge_config()
    if (hive_cfg.enabled and hive_cfg.watcher_api_url) or (public_hive_cfg.enabled and public_hive_cfg.topic_target_url):
        specs.append(
            {
                "intent": "hive.list_available",
                "description": "List real currently available Hive research topics from the watcher or public bridge.",
                "read_only": True,
                "arguments": {"limit": "integer optional"},
            }
        )
    if public_hive_cfg.enabled and public_hive_cfg.topic_target_url:
        specs.extend(
            [
                {
                    "intent": "hive.list_research_queue",
                    "description": "List machine-readable Hive research queue entries with execution state and suggested questions.",
                    "read_only": True,
                    "arguments": {"limit": "integer optional"},
                },
                {
                    "intent": "hive.export_research_packet",
                    "description": "Fetch the machine-readable research packet for a concrete Hive topic.",
                    "read_only": True,
                    "arguments": {"topic_id": "string"},
                },
                {
                    "intent": "hive.search_artifacts",
                    "description": "Search compressed research artifacts already packed into the local/public Hive research lane.",
                    "read_only": True,
                    "arguments": {"query": "string", "topic_id": "string optional", "limit": "integer optional"},
                },
            ]
        )
        if public_hive_write_enabled(public_hive_cfg):
            specs.extend(
                [
                    {
                        "intent": "hive.research_topic",
                        "description": "Auto-claim, research, mine heuristics, gate promotion, and publish a real autonomous Hive research bundle.",
                        "read_only": False,
                        "arguments": {"topic_id": "string", "auto_claim": "boolean optional"},
                    },
                    {
                        "intent": "hive.create_topic",
                        "description": "Create a real public Hive topic with a concrete title, summary, and technical tags.",
                        "read_only": False,
                        "arguments": {
                            "title": "string",
                            "summary": "string",
                            "topic_tags": "string[] optional",
                            "status": "open|researching|disputed optional",
                        },
                    },
                    {
                        "intent": "hive.claim_task",
                        "description": "Claim a real public Hive topic/task so progress is visible instead of implied.",
                        "read_only": False,
                        "arguments": {
                            "topic_id": "string",
                            "note": "string optional",
                            "capability_tags": "string[] optional",
                        },
                    },
                    {
                        "intent": "hive.post_progress",
                        "description": "Post a real progress update into a Hive topic.",
                        "read_only": False,
                        "arguments": {
                            "topic_id": "string",
                            "body": "string",
                            "progress_state": "started|working|blocked|done optional",
                            "claim_id": "string optional",
                        },
                    },
                    {
                        "intent": "hive.submit_result",
                        "description": "Submit a real final result into a Hive topic and close or solve it.",
                        "read_only": False,
                        "arguments": {
                            "topic_id": "string",
                            "body": "string",
                            "result_status": "solved|closed|disputed optional",
                            "claim_id": "string optional",
                        },
                    },
                ]
            )

    for tool in list_operator_tools():
        tool_id = str(tool.get("tool_id") or "").strip()
        if not tool_id or tool_id not in _SUPPORTED_OPERATOR_TOOL_IDS or not tool.get("available"):
            continue
        intent = f"operator.{tool_id}"
        specs.append(
            {
                "intent": intent,
                "description": str(tool.get("description") or "").strip(),
                "read_only": not bool(tool.get("destructive")),
                "arguments": _operator_argument_schema(tool_id),
            }
        )
    return specs


def should_attempt_tool_intent(
    user_text: str,
    *,
    task_class: str,
    source_context: dict[str, Any] | None = None,
) -> bool:
    source_context = dict(source_context or {})
    surface = str(source_context.get("surface") or "").strip().lower()
    platform = str(source_context.get("platform") or "").strip().lower()
    _TOOL_CAPABLE_SURFACES = {"channel", "openclaw", "api", "cli", "terminal", "local", ""}
    _TOOL_CAPABLE_PLATFORMS = {"openclaw", "telegram", "discord", "local", "cli", ""}
    if surface not in _TOOL_CAPABLE_SURFACES and platform not in _TOOL_CAPABLE_PLATFORMS:
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
    if any(marker in padded for marker in (
        " proceed ", " do it ", " do all ", " go ahead ", " carry on ",
        " start working ", " continue ", " yes proceed ", " yes do it ",
        " yes go ahead ", " yes continue ", " deliver it ", " submit it ",
        " execute ", " run it ", " just do it ",
    )):
        return True
    compact = lowered.strip(" \t\n\r?!.,")
    return compact in {"proceed", "do it", "do all", "go ahead", "carry on", "continue", "start working", "yes", "yes proceed", "yes do it", "ok do it", "ok proceed", "ok go ahead", "deliver it", "submit it", "execute", "run it", "just do it", "yes pls", "yes please", "all good carry on", "proceed with next steps", "proceed with that"}


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


def plan_tool_workflow(
    *,
    user_text: str,
    task_class: str,
    executed_steps: list[dict[str, Any]],
    source_context: dict[str, Any] | None,
) -> WorkflowPlannerDecision:
    text = " ".join(str(user_text or "").split()).strip()
    lowered = f" {text.lower()} "
    steps = [dict(step) for step in list(executed_steps or []) if isinstance(step, dict)]
    replacement = _explicit_replace_request(text)
    explicit_command = _explicit_command_request(text)
    compare_or_verify = any(marker in lowered for marker in (" compare ", " versus ", " vs ", " verify ", " confirm ", " is it true "))
    public_entity_lookup = looks_like_public_entity_lookup_request(text)
    explicit_lookup = looks_like_explicit_lookup_request(text) or public_entity_lookup
    tool_inventory_request = _looks_like_tool_inventory_request(text)
    workspace_bootstrap_path = _extract_workspace_bootstrap_path(text)
    workspace_bootstrap_request = _looks_like_workspace_bootstrap_request(text)
    entity_query, entity_retry_query = _entity_lookup_query_variants(text)
    research_flow = bool(
        explicit_lookup
        or compare_or_verify
        or task_class in {"research", "chat_research", "system_design"}
        or any(marker in lowered for marker in (" latest ", " current ", " docs ", " documentation ", " research ", " source "))
    )

    _create_task_markers = (
        " create task ", " create new task ", " new task for ", " add task ", " add to hive ", " add to the hive ",
        " create these tasks ", " create them ", " create these ", " yes create ", " yes create them ",
    )
    def _create_task_fuzzy(lo):
        return ("create" in lo and "task" in lo) or ("create" in lo and ("hive" in lo or "topic" in lo))
    def _proceed_with_task(lo):
        return any(m in lo for m in (" proceed ", " do it ", " do all ", " start working ", " go ahead ", " carry on ")) and ("task" in lo or "hive" in lo or "create" in lo)

    if not steps:
        if any(marker in lowered for marker in _create_task_markers) or _create_task_fuzzy(lowered) or _proceed_with_task(lowered):
            raw_title = text.strip()
            for prefix in ("create hive mind task", "create hive task", "create new task for research", "create new task for", "create new task", "create task for research", "create task for", "create task", "new task for research", "new task for", "new task", "add to the hive a new task", "add to hive a new task", "add to the hive", "add to hive", "add task", "create these tasks", "create them", "create these", "yes create", "yes create them", "do all and start working", "proceed with", "do it", "do all", "start working", "go ahead", "carry on"):
                if raw_title.lower().startswith(prefix):
                    raw_title = raw_title[len(prefix):].strip().lstrip("-:–").strip()
                    break
            title = raw_title[:180].strip()
            _generic = title.lower() in ("these tasks", "these", "them", "it", "on hive", "") or len(title) < 4
            title = title if not _generic else "User-requested Hive task"
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
                next_payload={"intent": "web.search", "arguments": {"query": text, "limit": 4}},
            )
        if explicit_command:
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_command_run",
                next_payload={"intent": "sandbox.run_command", "arguments": {"command": explicit_command}},
            )
        return WorkflowPlannerDecision(handled=False, reason="no_workflow_plan")

    last_step = dict(steps[-1] or {})
    last_intent = str(last_step.get("tool_name") or "").strip()
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
                        next_payload={"intent": "web.research", "arguments": {"query": entity_retry_query or entity_query or text}},
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
                    next_payload={"intent": "web.research", "arguments": {"query": text}},
                )
            if public_entity_lookup and not _workflow_step_exists(steps, "web.research") and result_count < 2:
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_entity_lookup_verify",
                    next_payload={"intent": "web.research", "arguments": {"query": entity_retry_query or entity_query or text}},
                )
            return WorkflowPlannerDecision(handled=True, reason="research_stop_after_search", stop_after=True)
        if last_intent == "web.fetch":
            if compare_or_verify and not _workflow_step_exists(steps, "web.research"):
                return WorkflowPlannerDecision(
                    handled=True,
                    reason="planned_research_after_fetch",
                    next_payload={"intent": "web.research", "arguments": {"query": text}},
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
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_directory_bootstrap", stop_after=True)

    if last_intent == "workspace.write_file":
        if explicit_command and not _workflow_step_exists(steps, "sandbox.run_command", key="command", value=explicit_command):
            return WorkflowPlannerDecision(
                handled=True,
                reason="planned_command_after_write",
                next_payload={"intent": "sandbox.run_command", "arguments": {"command": explicit_command}},
            )
        return WorkflowPlannerDecision(handled=True, reason="workspace_stop_after_write", stop_after=True)

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


def execute_tool_intent(
    payload: Any,
    *,
    task_id: str,
    session_id: str,
    source_context: dict[str, Any] | None,
    hive_activity_tracker: HiveActivityTracker,
    public_hive_bridge: PublicHiveBridge | None = None,
    checkpoint_id: str | None = None,
    step_index: int = 0,
) -> ToolIntentExecution:
    normalized = _normalize_payload(payload)
    intent = str(normalized.get("intent") or "").strip()
    arguments = dict(normalized.get("arguments") or {})
    if not intent:
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="missing_intent",
            response_text="I won't fake it: the model returned an invalid tool payload with no intent name.",
            user_safe_response_text="I couldn't map that cleanly to a real action.",
            mode="tool_failed",
            tool_name="unknown",
            details={"payload": normalized},
        )
    if intent in {"respond.direct", "none", "no_tool"}:
        return ToolIntentExecution(handled=False, ok=True, status="direct_response")

    receipt_key = ""
    idempotency_key = ""
    if checkpoint_id and is_mutating_tool_intent(intent):
        receipt_key = build_tool_receipt_key(
            checkpoint_id=str(checkpoint_id),
            step_index=max(0, int(step_index)),
            intent=intent,
            arguments=arguments,
        )
        cached = load_tool_receipt(receipt_key)
        if cached:
            cached_execution = _execution_from_receipt(cached)
            if cached_execution is not None:
                return cached_execution
        idempotency_key = receipt_key
        arguments = _inject_idempotency_key(intent, arguments, idempotency_key=idempotency_key)

    if intent in _WEB_TOOL_INTENTS:
        execution = _execute_web_tool(intent, arguments, task_id=task_id, source_context=source_context)
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        return execution
    runtime_execution = execute_runtime_tool(intent, arguments, source_context=source_context)
    if runtime_execution is not None:
        runtime_details = dict(runtime_execution.details or {})
        runtime_details.setdefault(
            "observation",
            _tool_observation(
                intent=intent,
                tool_surface="runtime_execution",
                ok=runtime_execution.ok,
                status=runtime_execution.status,
                response_preview=str(runtime_execution.response_text or "")[:280],
            ),
        )
        runtime_mode = (
            "tool_preview"
            if runtime_execution.status in {"user_action_required", "simulate_only"}
            else "tool_executed"
            if runtime_execution.ok
            else "tool_failed"
        )
        execution = ToolIntentExecution(
            handled=runtime_execution.handled,
            ok=runtime_execution.ok,
            status=runtime_execution.status,
            response_text=runtime_execution.response_text,
            mode=runtime_mode,
            tool_name=intent,
            details=runtime_details,
        )
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        return execution
    if intent in _HIVE_TOOL_INTENTS:
        execution = _execute_hive_tool(
            intent,
            arguments,
            hive_activity_tracker=hive_activity_tracker,
            public_hive_bridge=public_hive_bridge,
        )
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        return execution
    if intent in _READ_ONLY_OPERATOR_INTENTS | _MUTATING_OPERATOR_INTENTS:
        execution = _execute_operator_tool(
            intent,
            arguments,
            task_id=task_id,
            session_id=session_id,
        )
        _maybe_store_tool_receipt(
            execution,
            receipt_key=receipt_key,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            intent=intent,
            arguments=arguments,
            idempotency_key=idempotency_key,
        )
        return execution

    audit_logger.log(
        "tool_intent_unsupported",
        target_id=task_id,
        target_type="task",
        details={"intent": intent, "arguments": arguments, "source_context": dict(source_context or {})},
    )
    return _unsupported_execution_for_intent(
        intent,
        status="unsupported",
        extra_details={
            "intent": intent,
            "arguments": arguments,
        },
        user_safe_override="That action is not wired on this runtime yet.",
    )


def _normalize_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        result = dict(payload)
    elif is_dataclass(payload):
        result = asdict(payload)
    elif isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception:
            return {}
        result = dict(parsed) if isinstance(parsed, dict) else {}
    else:
        return {}
    arguments = result.get("arguments")
    if not isinstance(arguments, dict):
        result["arguments"] = {}
    return result


def _inject_idempotency_key(intent: str, arguments: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
    if not idempotency_key:
        return dict(arguments)
    updated = dict(arguments)
    if intent in _HIVE_TOOL_INTENTS:
        updated["idempotency_key"] = idempotency_key
    if intent in _MUTATING_OPERATOR_INTENTS:
        updated.setdefault("action_id", idempotency_key)
    return updated


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


def _maybe_store_tool_receipt(
    execution: ToolIntentExecution,
    *,
    receipt_key: str,
    session_id: str,
    checkpoint_id: str | None,
    intent: str,
    arguments: dict[str, Any],
    idempotency_key: str,
) -> None:
    if not receipt_key or not checkpoint_id:
        return
    store_tool_receipt(
        receipt_key=receipt_key,
        session_id=session_id,
        checkpoint_id=str(checkpoint_id),
        tool_name=intent,
        idempotency_key=idempotency_key,
        arguments=arguments,
        execution=_execution_to_receipt(execution),
    )


def _execution_to_receipt(execution: ToolIntentExecution) -> dict[str, Any]:
    return {
        "handled": bool(execution.handled),
        "ok": bool(execution.ok),
        "status": str(execution.status or ""),
        "response_text": str(execution.response_text or ""),
        "user_safe_response_text": str(execution.user_safe_response_text or ""),
        "mode": str(execution.mode or ""),
        "tool_name": str(execution.tool_name or ""),
        "details": dict(execution.details or {}),
        "learned_plan": None,
    }


def _execution_from_receipt(receipt: dict[str, Any]) -> ToolIntentExecution | None:
    payload = dict(receipt.get("execution") or {})
    if not payload:
        return None
    details = dict(payload.get("details") or {})
    details["from_receipt"] = True
    if receipt.get("idempotency_key"):
        details["idempotency_key"] = str(receipt.get("idempotency_key"))
    return ToolIntentExecution(
        handled=bool(payload.get("handled")),
        ok=bool(payload.get("ok")),
        status=str(payload.get("status") or ""),
        response_text=str(payload.get("response_text") or ""),
        user_safe_response_text=str(payload.get("user_safe_response_text") or ""),
        mode=str(payload.get("mode") or "tool_executed"),
        tool_name=str(payload.get("tool_name") or ""),
        details=details,
        learned_plan=None,
    )


def _execute_web_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str,
    source_context: dict[str, Any] | None,
) -> ToolIntentExecution:
    if not policy_engine.allow_web_fallback():
        return _unsupported_execution_for_intent(intent, status="disabled")

    load_builtin_tools()
    try:
        if intent == "web.search":
            query = str(arguments.get("query") or "").strip()
            limit = max(1, min(int(arguments.get("limit") or 3), 5))
            if not query:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Web search needs a non-empty query.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            rows = WebAdapter.planned_search_query(
                query,
                task_id=task_id,
                limit=limit,
                task_class="research",
                source_label="web.search",
            )
            if not rows:
                results = call_tool("web.search", query=query, max_results=limit)
                rows = [_normalize_item(item) for item in list(results or [])[:limit]]
            if not rows:
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="no_results",
                    response_text=f'No live search results came back for "{query}".',
                    mode="tool_executed",
                    tool_name=intent,
                    details={
                        "query": query,
                        "result_count": 0,
                        "results": [],
                        "observation": _tool_observation(
                            intent=intent,
                            tool_surface="web",
                            ok=True,
                            status="no_results",
                            query=query,
                            result_count=0,
                            results=[],
                        ),
                    },
                )
            observation_results = [
                {
                    "title": str(row.get("result_title") or row.get("title") or row.get("url") or "Untitled").strip(),
                    "url": str(row.get("result_url") or row.get("url") or "").strip(),
                    "snippet": str(row.get("summary") or row.get("snippet") or "").strip()[:180],
                    "source_profile_label": str(row.get("source_profile_label") or "").strip(),
                    "origin_domain": str(row.get("origin_domain") or "").strip(),
                }
                for row in rows[:limit]
            ]
            lines = [f'Search results for "{query}":']
            for row in rows:
                title = str(row.get("result_title") or row.get("title") or row.get("url") or "Untitled").strip()
                url = str(row.get("result_url") or row.get("url") or "").strip()
                snippet = str(row.get("summary") or row.get("snippet") or "").strip()
                profile_label = str(row.get("source_profile_label") or "").strip()
                line = f"- {title}"
                if url:
                    line += f" - {url}"
                if profile_label:
                    line += f" [{profile_label}]"
                lines.append(line)
                if snippet:
                    lines.append(f"  {snippet[:180]}")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text="\n".join(lines),
                mode="tool_executed",
                tool_name=intent,
                details={
                    "query": query,
                    "result_count": len(rows),
                    "results": observation_results,
                    "observation": _tool_observation(
                        intent=intent,
                        tool_surface="web",
                        ok=True,
                        status="executed",
                        query=query,
                        result_count=len(rows),
                        results=observation_results,
                    ),
                },
            )

        if intent == "web.fetch":
            url = str(arguments.get("url") or "").strip()
            timeout_s = float(arguments.get("timeout_s") or 15.0)
            if not url:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Web fetch needs a URL.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            result = call_tool("web.fetch", url=url, timeout_s=timeout_s)
            status = str(result.get("status") or "unknown").strip()
            text = str(result.get("text") or "").strip()
            preview = text[:500] if text else ""
            lines = [f"Fetched {url}", f"- Status: {status}"]
            if preview:
                lines.append(f"- Preview: {preview}")
            return ToolIntentExecution(
                handled=True,
                ok=status == "ok",
                status="executed" if status == "ok" else status,
                response_text="\n".join(lines),
                mode="tool_executed" if status == "ok" else "tool_failed",
                tool_name=intent,
                details={
                    "url": url,
                    "fetch_status": status,
                    "text_preview": preview,
                    "observation": _tool_observation(
                        intent=intent,
                        tool_surface="web",
                        ok=status == "ok",
                        status="executed" if status == "ok" else status,
                        url=url,
                        fetch_status=status,
                        text_preview=preview,
                    ),
                },
            )

        if intent == "web.research":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Web research needs a non-empty query.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            research_result = CuriosityRoamer().adaptive_research(
                task_id=task_id,
                user_input=query,
                classification={"task_class": "research"},
                interpretation=SimpleNamespace(topic_hints=[], understanding_confidence=0.82),
                source_context=dict(source_context or {"surface": "openclaw", "platform": "openclaw"}),
            )
            observation_hits = [
                {
                    "title": str(row.get("result_title") or row.get("title") or row.get("result_url") or "Untitled").strip(),
                    "url": str(row.get("result_url") or row.get("url") or "").strip(),
                    "snippet": str(row.get("summary") or row.get("snippet") or "").strip()[:180],
                    "domain": str(row.get("origin_domain") or "").strip(),
                }
                for row in list(research_result.notes or [])[:5]
            ]
            lines = [f'Adaptive web research for "{query}":']
            if research_result.actions_taken:
                lines.append("- Actions: " + ", ".join(research_result.actions_taken))
            if research_result.queries_run:
                lines.append("- Queries: " + " | ".join(research_result.queries_run[:3]))
            for row in observation_hits[:3]:
                line = f"- {row['title']}"
                if row["url"]:
                    line += f" - {row['url']}"
                if row["domain"]:
                    line += f" [{row['domain']}]"
                lines.append(line)
                if row["snippet"]:
                    lines.append(f"  {row['snippet']}")
            if research_result.admitted_uncertainty:
                lines.append(f"- Uncertainty: {research_result.uncertainty_reason}")
            elif research_result.stop_reason:
                lines.append(f"- Stop reason: {research_result.stop_reason}")
            return ToolIntentExecution(
                handled=True,
                ok=bool(observation_hits),
                status="executed" if observation_hits else "no_results",
                response_text="\n".join(lines),
                user_safe_response_text="\n".join(lines),
                mode="tool_executed" if observation_hits else "tool_failed",
                tool_name=intent,
                details={
                    "query": query,
                    "strategy": research_result.strategy,
                    "actions_taken": list(research_result.actions_taken),
                    "queries_run": list(research_result.queries_run),
                    "evidence_strength": research_result.evidence_strength,
                    "uncertainty_reason": research_result.uncertainty_reason,
                    "hit_count": len(observation_hits),
                    "hits": observation_hits,
                    "observation": _tool_observation(
                        intent=intent,
                        tool_surface="web",
                        ok=bool(observation_hits),
                        status="executed" if observation_hits else "no_results",
                        query=query,
                        strategy=research_result.strategy,
                        actions_taken=list(research_result.actions_taken),
                        queries_run=list(research_result.queries_run),
                        evidence_strength=research_result.evidence_strength,
                        admitted_uncertainty=research_result.admitted_uncertainty,
                        uncertainty_reason=research_result.uncertainty_reason,
                        stop_reason=research_result.stop_reason,
                        hit_count=len(observation_hits),
                        hits=observation_hits,
                    ),
                },
            )

        if intent == "browser.render":
            url = str(arguments.get("url") or "").strip()
            if not url:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Browser render needs a URL.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            result = call_tool("browser.render", url=url)
            status = str(result.get("status") or "unknown").strip()
            final_url = str(result.get("final_url") or url).strip()
            title = str(result.get("title") or "").strip()
            text = str(result.get("text") or "").strip()
            lines = [f"Rendered {final_url}", f"- Status: {status}"]
            if title:
                lines.append(f"- Title: {title}")
            if text:
                lines.append(f"- Preview: {text[:240]}")
            return ToolIntentExecution(
                handled=True,
                ok=status == "ok",
                status="executed" if status == "ok" else status,
                response_text="\n".join(lines),
                mode="tool_executed" if status == "ok" else "tool_failed",
                tool_name=intent,
                details={
                    "url": url,
                    "final_url": final_url,
                    "render_status": status,
                    "title": title,
                    "text_preview": text[:240] if text else "",
                    "observation": _tool_observation(
                        intent=intent,
                        tool_surface="web",
                        ok=status == "ok",
                        status="executed" if status == "ok" else status,
                        url=url,
                        final_url=final_url,
                        render_status=status,
                        title=title,
                        text_preview=text[:240] if text else "",
                    ),
                },
            )
    except Exception as exc:
        audit_logger.log(
            "tool_intent_execution_error",
            target_id=task_id,
            target_type="task",
            details={"intent": intent, "arguments": arguments, "error": str(exc)},
        )
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="execution_failed",
            response_text=f"I tried `{intent}` but the tool failed: {exc}",
            mode="tool_failed",
            tool_name=intent,
            details={"error": str(exc)},
        )

    return _unsupported_execution_for_intent(intent, status="unsupported")


def _execute_hive_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    hive_activity_tracker: HiveActivityTracker,
    public_hive_bridge: PublicHiveBridge | None,
) -> ToolIntentExecution:
    if intent == "hive.list_available":
        return _execute_hive_list_available(
            hive_activity_tracker,
            arguments,
            public_hive_bridge=public_hive_bridge,
        )
    if public_hive_bridge is None:
        return _unsupported_execution_for_intent(intent, status="not_configured")
    write_enabled = getattr(public_hive_bridge, "write_enabled", lambda: True)()
    if intent in {"hive.research_topic", "hive.create_topic", "hive.claim_task", "hive.post_progress", "hive.submit_result"} and not write_enabled:
        return _unsupported_execution_for_intent(intent, status="missing_auth")
    try:
        if intent == "hive.list_research_queue":
            rows = public_hive_bridge.list_public_research_queue(limit=max(1, min(int(arguments.get("limit") or 12), 50)))
            if not rows:
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="empty",
                    response_text="The Hive research queue is currently empty or unavailable.",
                    mode="tool_executed",
                    tool_name=intent,
                    details={"topics": []},
                )
            preview_lines = ["Hive research queue:"]
            for row in rows[:8]:
                preview_lines.append(
                    "- "
                    f"{row.get('topic_id') or ''!s}: {row.get('title') or 'Untitled topic'!s} "
                    f"[status={row.get('status') or 'open'!s}, "
                    f"state={row.get('execution_state') or 'open'!s}, "
                    f"claims={int(row.get('active_claim_count') or 0)}, "
                    f"priority={float(row.get('research_priority') or 0.0):.2f}]"
                )
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="listed",
                response_text="\n".join(preview_lines),
                mode="tool_executed",
                tool_name=intent,
                details={"topics": rows},
            )
        if intent == "hive.export_research_packet":
            topic_id = str(arguments.get("topic_id") or "").strip()
            packet = public_hive_bridge.get_public_research_packet(topic_id)
            if not packet:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="missing_packet",
                    response_text=f"I couldn't fetch a research packet for Hive topic `{topic_id}`.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            topic = dict(packet.get("topic") or {})
            execution_state = dict(packet.get("execution_state") or {})
            counts = dict(packet.get("counts") or {})
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="exported",
                response_text=(
                    f"Exported machine-readable research packet for `{topic_id}`: "
                    f"{topic.get('title') or 'Untitled topic'!s} "
                    f"[state={execution_state.get('execution_state') or 'open'!s}, "
                    f"posts={int(counts.get('post_count') or 0)}, "
                    f"evidence={int(counts.get('evidence_count') or 0)}]"
                ),
                mode="tool_executed",
                tool_name=intent,
                details={"packet": packet, "topic_id": topic_id},
            )
        if intent == "hive.search_artifacts":
            query_text = " ".join(str(arguments.get("query") or "").split()).strip()
            if not query_text:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="missing_query",
                    response_text="hive.search_artifacts needs a non-empty `query`.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            rows = public_hive_bridge.search_public_artifacts(
                query_text=query_text,
                topic_id=str(arguments.get("topic_id") or "").strip() or None,
                limit=max(1, min(int(arguments.get("limit") or 8), 20)),
            )
            if not rows:
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="empty",
                    response_text=f'No research artifacts matched "{query_text}".',
                    mode="tool_executed",
                    tool_name=intent,
                    details={"artifacts": []},
                )
            lines = [f'Research artifacts for "{query_text}":']
            for row in rows[:8]:
                lines.append(
                    f"- {row.get('artifact_id') or ''!s}: {row.get('title') or 'Untitled artifact'!s} "
                    f"[kind={row.get('source_kind') or ''!s}, topic={row.get('topic_id') or ''!s}]"
                )
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="searched",
                response_text="\n".join(lines),
                mode="tool_executed",
                tool_name=intent,
                details={"artifacts": rows},
            )
        if intent == "hive.research_topic":
            run_in_background = bool(arguments.get("run_in_background", False))
            topic_id_arg = str(arguments.get("topic_id") or "").strip()
            auto_claim_arg = bool(arguments.get("auto_claim", True))

            if run_in_background:
                import threading as _threading

                def _background_research() -> None:
                    try:
                        research_topic_from_signal(
                            {"topic_id": topic_id_arg},
                            public_hive_bridge=public_hive_bridge,
                            hive_activity_tracker=hive_activity_tracker,
                            auto_claim=auto_claim_arg,
                        )
                    except Exception as exc:
                        audit_logger.log(
                            "background_research_error",
                            target_id=topic_id_arg,
                            target_type="topic",
                            details={"error": str(exc)},
                        )

                _threading.Thread(
                    target=_background_research,
                    name=f"nulla-bg-research-{topic_id_arg[:12]}",
                    daemon=True,
                ).start()
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="started_background",
                    response_text=f"Started Hive research on `{topic_id_arg}` in the background. You can keep chatting — I'll work on it.",
                    mode="tool_executed",
                    tool_name=intent,
                    details={"topic_id": topic_id_arg, "background": True},
                )

            result = research_topic_from_signal(
                {"topic_id": topic_id_arg},
                public_hive_bridge=public_hive_bridge,
                hive_activity_tracker=hive_activity_tracker,
                auto_claim=auto_claim_arg,
            ).to_dict()
            if not result.get("ok"):
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status=str(result.get("status") or "failed"),
                    response_text=str(result.get("response_text") or "Autonomous research failed."),
                    mode="tool_failed",
                    tool_name=intent,
                    details=result,
                )
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="completed",
                response_text=str(result.get("response_text") or "Autonomous research completed."),
                mode="tool_executed",
                tool_name=intent,
                details=result,
            )
        if intent == "hive.create_topic":
            result = public_hive_bridge.create_public_topic(
                title=str(arguments.get("title") or "").strip(),
                summary=str(arguments.get("summary") or "").strip(),
                topic_tags=[str(item).strip() for item in list(arguments.get("topic_tags") or []) if str(item).strip()],
                status=str(arguments.get("status") or "open").strip() or "open",
                idempotency_key=str(arguments.get("idempotency_key") or "").strip() or None,
            )
            topic_id = str(result.get("topic_id") or "")
            if not result.get("ok") or not topic_id:
                return _failed_hive_execution(intent, result, "I couldn't create that Hive topic.")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="created",
                response_text=f"Created Hive topic `{topic_id}`: {str(arguments.get('title') or '').strip()}",
                mode="tool_executed",
                tool_name=intent,
                details={"topic_id": topic_id, **dict(result)},
            )
        if intent == "hive.claim_task":
            result = public_hive_bridge.claim_public_topic(
                topic_id=str(arguments.get("topic_id") or "").strip(),
                note=str(arguments.get("note") or "").strip() or None,
                capability_tags=[str(item).strip() for item in list(arguments.get("capability_tags") or []) if str(item).strip()],
                idempotency_key=str(arguments.get("idempotency_key") or "").strip() or None,
            )
            claim_id = str(result.get("claim_id") or "")
            if not result.get("ok") or not claim_id:
                return _failed_hive_execution(intent, result, "I couldn't claim that Hive topic.")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="claimed",
                response_text=f"Claimed Hive topic `{result.get('topic_id') or ''!s}` with claim `{claim_id}`.",
                mode="tool_executed",
                tool_name=intent,
                details={"claim_id": claim_id, "topic_id": str(result.get("topic_id") or ""), **dict(result)},
            )
        if intent == "hive.post_progress":
            result = public_hive_bridge.post_public_topic_progress(
                topic_id=str(arguments.get("topic_id") or "").strip(),
                body=str(arguments.get("body") or "").strip(),
                progress_state=str(arguments.get("progress_state") or "working").strip() or "working",
                claim_id=str(arguments.get("claim_id") or "").strip() or None,
                idempotency_key=str(arguments.get("idempotency_key") or "").strip() or None,
            )
            post_id = str(result.get("post_id") or "")
            if not result.get("ok") or not post_id:
                return _failed_hive_execution(intent, result, "I couldn't post progress to that Hive topic.")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="progress_posted",
                response_text=(
                    f"Posted {str(arguments.get('progress_state') or 'working').strip() or 'working'} progress "
                    f"to Hive topic `{result.get('topic_id') or ''!s}`."
                ),
                mode="tool_executed",
                tool_name=intent,
                details={"post_id": post_id, "topic_id": str(result.get("topic_id") or ""), **dict(result)},
            )
        if intent == "hive.submit_result":
            result = public_hive_bridge.submit_public_topic_result(
                topic_id=str(arguments.get("topic_id") or "").strip(),
                body=str(arguments.get("body") or "").strip(),
                result_status=str(arguments.get("result_status") or "solved").strip() or "solved",
                claim_id=str(arguments.get("claim_id") or "").strip() or None,
                idempotency_key=str(arguments.get("idempotency_key") or "").strip() or None,
            )
            post_id = str(result.get("post_id") or "")
            if not result.get("ok") or not post_id:
                return _failed_hive_execution(intent, result, "I couldn't submit the Hive result.")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="result_submitted",
                response_text=(
                    f"Submitted result to Hive topic `{result.get('topic_id') or ''!s}` "
                    f"and marked it `{str(arguments.get('result_status') or 'solved').strip() or 'solved'}`."
                ),
                mode="tool_executed",
                tool_name=intent,
                details={"post_id": post_id, "topic_id": str(result.get("topic_id") or ""), **dict(result)},
            )
    except Exception as exc:
        audit_logger.log(
            "tool_intent_hive_execution_error",
            target_id=str(arguments.get("topic_id") or intent),
            target_type="task",
            details={"intent": intent, "arguments": dict(arguments), "error": str(exc)},
        )
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="error",
            response_text=f"Hive action `{intent}` failed: {exc}",
            mode="tool_failed",
            tool_name=intent,
            details={"error": str(exc)},
        )
    return _unsupported_execution_for_intent(intent, status="unsupported")


def _execute_hive_list_available(
    hive_activity_tracker: HiveActivityTracker,
    arguments: dict[str, Any],
    *,
    public_hive_bridge: PublicHiveBridge | None,
) -> ToolIntentExecution:
    limit = max(1, min(int(arguments.get("limit") or 5), 8))
    topics: list[dict[str, Any]] = []
    error_text: str | None = None
    if hive_activity_tracker.config.enabled and hive_activity_tracker.config.watcher_api_url:
        try:
            dashboard = hive_activity_tracker.fetch_dashboard()
            topics = list(hive_activity_tracker._available_topics(dashboard))[:limit]
        except Exception:
            error_text = "I couldn't reach the Hive watcher right now."
    elif public_hive_bridge is not None and public_hive_bridge.enabled() and public_hive_bridge.config.topic_target_url:
        try:
            topics = public_hive_bridge.list_public_research_queue(limit=limit) or public_hive_bridge.list_public_topics(limit=limit)
        except Exception:
            error_text = "I couldn't reach the public Hive bridge right now."
    else:
        capability_entry_for_intent("hive.list_available")
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="not_configured",
            response_text=render_capability_truth_response(capability_gap_for_intent("hive.list_available")),
            user_safe_response_text=render_capability_truth_response(capability_gap_for_intent("hive.list_available")),
            mode="tool_failed",
            tool_name="hive.list_available",
        )

    if error_text and not topics:
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="unreachable",
            response_text=error_text,
            mode="tool_failed",
            tool_name="hive.list_available",
        )
    if not topics:
        return ToolIntentExecution(
            handled=True,
            ok=True,
            status="no_results",
            response_text="No open hive research requests are visible right now.",
            mode="tool_executed",
            tool_name="hive.list_available",
        )
    lines = ["Available Hive research right now:"]
    for topic in topics[:limit]:
        title = str(topic.get("title") or "Untitled topic").strip()
        status = str(topic.get("status") or "open").strip()
        topic_id = str(topic.get("topic_id") or "").strip()
        if topic_id:
            lines.append(f"- [{status}] {title} ({topic_id})")
        else:
            lines.append(f"- [{status}] {title}")
    return ToolIntentExecution(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(lines),
        mode="tool_executed",
        tool_name="hive.list_available",
        details={"topic_count": len(topics[:limit])},
    )


def _failed_hive_execution(intent: str, result: dict[str, Any], fallback: str) -> ToolIntentExecution:
    status = str(result.get("status") or "failed")
    reason = str(result.get("error") or result.get("status") or "").strip()
    response = fallback if not reason else f"{fallback} Status: {reason}."
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status=status,
        response_text=response,
        mode="tool_failed",
        tool_name=intent,
        details=dict(result or {}),
    )


def _execute_operator_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str,
    session_id: str,
) -> ToolIntentExecution:
    operator_kind = intent.split(".", 1)[1]
    operator_intent = _build_operator_action_intent(operator_kind, arguments)
    dispatch = dispatch_operator_action(
        operator_intent,
        task_id=task_id,
        session_id=session_id,
    )
    if dispatch.status == "executed":
        mode = "tool_executed"
    elif dispatch.status in {"reported", "approval_required"}:
        mode = "tool_preview"
    else:
        mode = "tool_failed"
    return ToolIntentExecution(
        handled=True,
        ok=bool(dispatch.ok),
        status=str(dispatch.status),
        response_text=str(dispatch.response_text or ""),
        mode=mode,
        tool_name=intent,
        details={
            **dict(dispatch.details or {}),
            "observation": _tool_observation(
                intent=intent,
                tool_surface="local_operator",
                ok=bool(dispatch.ok),
                status=str(dispatch.status),
                details=dict(dispatch.details or {}),
                response_preview=str(dispatch.response_text or "")[:280],
            ),
        },
        learned_plan=dispatch.learned_plan,
    )


def _build_operator_action_intent(operator_kind: str, arguments: dict[str, Any]) -> OperatorActionIntent:
    target_path = str(arguments.get("target_path") or arguments.get("path") or "").strip() or None
    destination_path = str(arguments.get("destination_path") or arguments.get("destination_dir") or "").strip() or None
    raw_text = ""
    if operator_kind == "move_path":
        source = str(arguments.get("source_path") or target_path or "").strip()
        dest = str(destination_path or "").strip()
        raw_text = f'move "{source}" to "{dest}"'.strip()
        target_path = source or None
    elif operator_kind == "schedule_calendar_event":
        title = str(arguments.get("title") or "NULLA Meeting").strip()
        start_iso = str(arguments.get("start_iso") or "").strip()
        duration_minutes = max(15, int(arguments.get("duration_minutes") or 30))
        raw_text = f'schedule a meeting "{title}" on {start_iso} for {duration_minutes}m'.strip()
    elif operator_kind == "cleanup_temp_files" and target_path:
        raw_text = f'clean temp files in "{target_path}"'
    elif operator_kind == "inspect_disk_usage" and target_path:
        raw_text = f'find disk bloat in "{target_path}"'
    return OperatorActionIntent(
        kind=operator_kind,
        target_path=target_path,
        destination_path=destination_path,
        approval_requested=False,
        action_id=str(arguments.get("action_id") or "").strip() or None,
        raw_text=raw_text,
    )


def _normalize_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        return asdict(item)
    if hasattr(item, "__dict__"):
        return {
            str(key): value
            for key, value in vars(item).items()
            if not str(key).startswith("_")
        }
    return {"value": str(item)}


def _operator_argument_schema(tool_id: str) -> dict[str, str]:
    if tool_id == "inspect_disk_usage":
        return {"target_path": "path optional"}
    if tool_id == "cleanup_temp_files":
        return {"target_path": "path optional"}
    if tool_id == "move_path":
        return {"source_path": "path", "destination_path": "directory path"}
    if tool_id == "schedule_calendar_event":
        return {
            "title": "string",
            "start_iso": "ISO datetime",
            "duration_minutes": "integer optional",
        }
    return {}
