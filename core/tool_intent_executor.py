from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from types import SimpleNamespace
from typing import Any

from core import audit_logger, policy_engine
from core.autonomous_topic_research import research_topic_from_signal
from core.curiosity_roamer import CuriosityRoamer
from core.execution import capabilities as execution_capabilities
from core.execution.constants import (
    _HIVE_TOOL_INTENTS,
    _MUTATING_OPERATOR_INTENTS,
    _READ_ONLY_OPERATOR_INTENTS,
    _WEB_TOOL_INTENTS,
)
from core.execution.models import ToolIntentExecution, _tool_observation
from core.execution.operator_tools import (
    build_operator_action_intent as _build_operator_action_intent_impl,
)
from core.execution.operator_tools import execute_operator_tool as _execute_operator_tool_impl
from core.execution.planner import (
    _looks_like_workspace_bootstrap_request,
    plan_tool_workflow,
    should_attempt_tool_intent,
)
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
    runtime_execution_capability_ledger,
    runtime_execution_tool_specs,
)
from retrieval.web_adapter import WebAdapter
from tools.registry import call_tool, load_builtin_tools

__all__ = [
    "ToolIntentExecution",
    "_looks_like_workspace_bootstrap_request",
    "execute_tool_intent",
    "plan_tool_workflow",
    "runtime_capability_ledger",
    "runtime_tool_specs",
    "should_attempt_tool_intent",
]


def runtime_capability_ledger() -> list[dict[str, Any]]:
    return execution_capabilities.runtime_capability_ledger(
        allow_web_fallback_fn=policy_engine.allow_web_fallback,
        load_hive_activity_tracker_config_fn=load_hive_activity_tracker_config,
        load_public_hive_bridge_config_fn=load_public_hive_bridge_config,
        public_hive_write_enabled_fn=public_hive_write_enabled,
        runtime_execution_capability_ledger_fn=runtime_execution_capability_ledger,
        operator_capability_ledger_fn=operator_capability_ledger,
    )


def capability_entry_for_intent(intent: str) -> dict[str, Any] | None:
    return execution_capabilities.capability_entry_for_intent(
        intent,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


def capability_gap_for_intent(
    intent: str,
    *,
    extra_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return execution_capabilities.capability_gap_for_intent(
        intent,
        extra_entries=extra_entries,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


def capability_truth_for_request(
    user_text: str,
    *,
    extra_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    return execution_capabilities.capability_truth_for_request(
        user_text,
        extra_entries=extra_entries,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


def render_capability_truth_response(report: dict[str, Any] | None) -> str:
    return execution_capabilities.render_capability_truth_response(report)


def supported_public_capability_tags(*, limit: int = 16) -> list[str]:
    return execution_capabilities.supported_public_capability_tags(
        limit=limit,
        runtime_capability_ledger_fn=runtime_capability_ledger,
    )


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
    return execution_capabilities.runtime_tool_specs(
        allow_web_fallback_fn=policy_engine.allow_web_fallback,
        runtime_execution_tool_specs_fn=runtime_execution_tool_specs,
        load_hive_activity_tracker_config_fn=load_hive_activity_tracker_config,
        load_public_hive_bridge_config_fn=load_public_hive_bridge_config,
        public_hive_write_enabled_fn=public_hive_write_enabled,
        list_operator_tools_fn=list_operator_tools,
    )


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
        if intent == "nullabook.get_profile":
            from core.nullabook_identity import get_profile
            from network.signer import get_local_peer_id as _local_pid
            profile = get_profile(_local_pid())
            if not profile:
                return ToolIntentExecution(
                    handled=True, ok=False, status="no_profile",
                    response_text="I don't have a NullaBook account yet.",
                    mode="tool_executed", tool_name=intent, details={},
                )
            return ToolIntentExecution(
                handled=True, ok=True, status="profile_loaded",
                response_text=(
                    f"NullaBook handle: {profile.handle}. "
                    f"Display name: {profile.display_name}. "
                    f"Bio: {profile.bio or '(not set)'}. "
                    f"Posts: {profile.post_count}, Claims: {profile.claim_count}, "
                    f"Glory: {profile.glory_score:.1f}. Status: {profile.status}."
                ),
                mode="tool_executed", tool_name=intent,
                details={"handle": profile.handle, "display_name": profile.display_name,
                         "bio": profile.bio, "post_count": profile.post_count,
                         "claim_count": profile.claim_count, "glory_score": profile.glory_score},
            )
        if intent == "nullabook.update_profile":
            from core.nullabook_identity import update_profile
            from network.signer import get_local_peer_id as _local_pid
            bio = str(arguments.get("bio") or "").strip() or None
            display_name = str(arguments.get("display_name") or "").strip() or None
            profile_url = str(arguments.get("profile_url") or "").strip() or None
            updated = update_profile(_local_pid(), bio=bio, display_name=display_name, profile_url=profile_url)
            if not updated:
                return ToolIntentExecution(
                    handled=True, ok=False, status="no_profile",
                    response_text="No NullaBook profile to update. Register first.",
                    mode="tool_executed", tool_name=intent, details={},
                )
            changed = [k for k, v in {"bio": bio, "display_name": display_name, "profile_url": profile_url}.items() if v is not None]
            return ToolIntentExecution(
                handled=True, ok=True, status="profile_updated",
                response_text=f"Updated NullaBook profile: {', '.join(changed)}.",
                mode="tool_executed", tool_name=intent,
                details={"updated_fields": changed, "handle": updated.handle},
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
    return _execute_operator_tool_impl(
        intent,
        arguments,
        task_id=task_id,
        session_id=session_id,
        dispatch_operator_action_fn=dispatch_operator_action,
    )


def _build_operator_action_intent(operator_kind: str, arguments: dict[str, Any]) -> OperatorActionIntent:
    return _build_operator_action_intent_impl(operator_kind, arguments)


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
