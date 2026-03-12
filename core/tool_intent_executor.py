from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from core.autonomous_topic_research import research_topic_from_signal
from core import audit_logger, policy_engine
from core.hive_activity_tracker import HiveActivityTracker, load_hive_activity_tracker_config
from core.local_operator_actions import OperatorActionIntent, dispatch_operator_action, list_operator_tools
from core.public_hive_bridge import PublicHiveBridge, load_public_hive_bridge_config, public_hive_write_enabled
from core.runtime_continuity import (
    build_tool_receipt_key,
    is_mutating_tool_intent,
    load_tool_receipt,
    store_tool_receipt,
)
from core.runtime_execution_tools import execute_runtime_tool, looks_like_execution_request, runtime_execution_tool_specs
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
)
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
    if surface not in {"channel", "openclaw", "api"} and platform not in {"openclaw", "telegram", "discord"}:
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
    return False


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
        execution = _execute_web_tool(intent, arguments, task_id=task_id)
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
            details=dict(runtime_execution.details or {}),
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
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status="unsupported",
        response_text=f"I won't fake it: `{intent}` is not wired on this runtime.",
        user_safe_response_text="That action is not wired on this runtime yet.",
        mode="tool_failed",
        tool_name=intent,
        details={"intent": intent, "arguments": arguments},
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


def _execute_web_tool(intent: str, arguments: dict[str, Any], *, task_id: str) -> ToolIntentExecution:
    if not policy_engine.allow_web_fallback():
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Live web tooling is disabled on this runtime.",
            mode="tool_failed",
            tool_name=intent,
        )

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
                    details={"query": query},
                )
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
                details={"query": query, "result_count": len(rows)},
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
                details={"url": url, "fetch_status": status},
            )

        if intent == "web.research":
            query = str(arguments.get("query") or "").strip()
            max_hits = max(1, min(int(arguments.get("max_hits") or 5), 8))
            max_pages = max(1, min(int(arguments.get("max_pages") or 3), 4))
            if not query:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Web research needs a non-empty query.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            result = call_tool("web.research", query=query, max_hits=max_hits, max_pages=max_pages)
            hits = [_normalize_item(item) for item in list(getattr(result, "hits", []) or [])[:max_hits]]
            pages = [_normalize_item(item) for item in list(getattr(result, "pages", []) or [])[:max_pages]]
            provider = str(getattr(result, "provider", "unknown") or "unknown")
            lines = [f'Web research for "{query}" via {provider}:']
            for row in hits[:3]:
                title = str(row.get("title") or row.get("url") or "Untitled").strip()
                url = str(row.get("url") or "").strip()
                snippet = str(row.get("snippet") or "").strip()
                line = f"- {title}"
                if url:
                    line += f" - {url}"
                lines.append(line)
                if snippet:
                    lines.append(f"  {snippet[:180]}")
            if pages:
                first_page = pages[0]
                page_text = str(first_page.get("text") or "").strip()
                if page_text:
                    lines.append(f"- Page preview: {page_text[:240]}")
            return ToolIntentExecution(
                handled=True,
                ok=bool(hits),
                status="executed" if hits else "no_results",
                response_text="\n".join(lines),
                mode="tool_executed" if hits else "tool_failed",
                tool_name=intent,
                details={"query": query, "provider": provider, "hit_count": len(hits)},
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
                details={"url": url, "final_url": final_url, "render_status": status},
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

    return ToolIntentExecution(
        handled=True,
        ok=False,
        status="unsupported",
        response_text=f"I won't fake it: `{intent}` is not wired on this runtime.",
        user_safe_response_text="That action is not wired on this runtime yet.",
        mode="tool_failed",
        tool_name=intent,
    )


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
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="not_configured",
            response_text="Public Hive write tooling is not configured on this runtime.",
            mode="tool_failed",
            tool_name=intent,
        )
    write_enabled = getattr(public_hive_bridge, "write_enabled", lambda: True)()
    if intent in {"hive.research_topic", "hive.create_topic", "hive.claim_task", "hive.post_progress", "hive.submit_result"} and not write_enabled:
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="missing_auth",
            response_text="Public Hive write auth is missing on this runtime, so I won't pretend the write lane is live.",
            mode="tool_failed",
            tool_name=intent,
        )
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
                    f"{str(row.get('topic_id') or '')}: {str(row.get('title') or 'Untitled topic')} "
                    f"[status={str(row.get('status') or 'open')}, "
                    f"state={str(row.get('execution_state') or 'open')}, "
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
                    f"{str(topic.get('title') or 'Untitled topic')} "
                    f"[state={str(execution_state.get('execution_state') or 'open')}, "
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
                    f"- {str(row.get('artifact_id') or '')}: {str(row.get('title') or 'Untitled artifact')} "
                    f"[kind={str(row.get('source_kind') or '')}, topic={str(row.get('topic_id') or '')}]"
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
            result = research_topic_from_signal(
                {
                    "topic_id": str(arguments.get("topic_id") or "").strip(),
                },
                public_hive_bridge=public_hive_bridge,
                hive_activity_tracker=hive_activity_tracker,
                auto_claim=bool(arguments.get("auto_claim", True)),
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
                response_text=f"Claimed Hive topic `{str(result.get('topic_id') or '')}` with claim `{claim_id}`.",
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
                    f"to Hive topic `{str(result.get('topic_id') or '')}`."
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
                    f"Submitted result to Hive topic `{str(result.get('topic_id') or '')}` "
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
    return ToolIntentExecution(
        handled=True,
        ok=False,
        status="unsupported",
        response_text=f"I won't fake it: `{intent}` is not wired on this runtime.",
        user_safe_response_text="That action is not wired on this runtime yet.",
        mode="tool_failed",
        tool_name=intent,
    )


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
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="not_configured",
            response_text="Hive listing is not configured on this runtime, so I can't report real live Hive tasks.",
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
        details=dict(dispatch.details or {}),
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
