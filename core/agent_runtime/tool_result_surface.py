from __future__ import annotations

import re
from importlib import import_module
from typing import Any

from core import audit_logger
from core.agent_runtime import fast_command_surface as agent_fast_command_surface
from core.agent_runtime import orchestrator as agent_orchestrator_runtime
from core.agent_runtime import response as agent_response_runtime
from core.agent_runtime import response_policy as agent_response_policy
from core.reasoning_engine import inspect_user_response_shape


def _agent_module() -> Any:
    return import_module("apps.nulla_agent")


class ToolResultSurfaceMixin:
    def _is_chat_truth_surface(self, source_context: dict[str, object] | None) -> bool:
        surface = str((source_context or {}).get("surface", "") or "").strip().lower()
        return surface in {"channel", "openclaw", "api"}

    def _chat_truth_fast_path_backing_sources(self, reason: str) -> list[str]:
        mapping = {
            "live_info_fast_path": ["web_lookup"],
            "hive_activity_command": ["hive"],
            "hive_research_followup": ["hive"],
            "hive_status_followup": ["hive"],
        }
        return list(mapping.get(str(reason or "").strip(), []))

    def _chat_truth_action_backing_sources(
        self,
        *,
        reason: str,
        success: bool,
        task_outcome: str | None,
    ) -> list[str]:
        if not success and str(task_outcome or "").strip().lower() != "pending_approval":
            return []
        normalized = str(reason or "").strip().lower()
        sources: list[str] = []
        if normalized.startswith("hive_topic_create_"):
            sources.append("hive")
        if normalized.startswith("channel_post_"):
            sources.append("channel_action")
        if normalized.startswith("operator_action_"):
            sources.append("operator_action")
        if normalized.startswith("model_tool_intent_"):
            sources.append("tool_intent")
        return sources or (["tool_action"] if success else [])

    def _chat_truth_claim_metrics(
        self,
        response_text: str,
        *,
        tool_backing_sources: list[str],
    ) -> dict[str, object]:
        normalized = " ".join(str(response_text or "").split()).strip().lower()
        claim_patterns = (
            r"\b(i|we)\s+(checked|searched|looked up|looked|fetched|pulled|read|wrote|edited|updated|created|posted|sent|ran|executed|claimed)\b",
            r"^started hive research on\b",
            r"^created hive task\b",
            r"\blive weather results\b",
        )
        claim_present = any(re.search(pattern, normalized) for pattern in claim_patterns)
        claim_count = 1 if claim_present else 0
        backed_sources = [str(item).strip() for item in list(tool_backing_sources or []) if str(item).strip()]
        backed_claim_count = claim_count if backed_sources else 0
        return {
            "tool_claim_present": claim_present,
            "tool_claim_count": claim_count,
            "tool_backed_claim_present": bool(backed_claim_count),
            "tool_backed_claim_count": backed_claim_count,
            "tool_unbacked_claim_count": max(0, claim_count - backed_claim_count),
            "tool_backing_sources": backed_sources,
        }

    def _emit_chat_truth_metrics(
        self,
        *,
        task_id: str,
        reason: str,
        response_text: str,
        response_class: str,
        source_context: dict[str, object] | None,
        rendered_via: str,
        fast_path_hit: bool,
        model_inference_used: bool,
        model_final_answer_hit: bool,
        model_execution_source: str,
        tool_backing_sources: list[str] | None = None,
    ) -> None:
        if not self._is_chat_truth_surface(source_context):
            return
        surface = str((source_context or {}).get("surface", "") or "").strip().lower()
        render_metrics = inspect_user_response_shape(
            response_text,
            surface=surface,
            rendered_via=rendered_via,
        )
        claim_metrics = self._chat_truth_claim_metrics(
            response_text,
            tool_backing_sources=list(tool_backing_sources or []),
        )
        audit_logger.log(
            "agent_chat_truth_metrics",
            target_id=task_id,
            target_type="task",
            details={
                "version": "m1-r01",
                "reason": reason,
                "response_class": response_class,
                "source_surface": (source_context or {}).get("surface"),
                "source_platform": (source_context or {}).get("platform"),
                "rendered_via": rendered_via,
                "fast_path_hit": bool(fast_path_hit),
                "model_inference_used": bool(model_inference_used),
                "model_final_answer_hit": bool(model_final_answer_hit),
                "model_execution_source": model_execution_source,
                "planner_leakage": bool(render_metrics["planner_leakage"]),
                "template_renderer_hit": bool(render_metrics["template_renderer_hit"]),
                "template_fallback_hit": bool(render_metrics["template_fallback_hit"]),
                **claim_metrics,
            },
        )

    def _render_credit_status(self, normalized_input: str) -> str:
        return agent_fast_command_surface.render_credit_status(normalized_input)

    def _maybe_attach_workflow(
        self,
        response: str,
        workflow_summary: str,
        *,
        source_context: dict[str, object] | None = None,
    ) -> str:
        return agent_response_policy.maybe_attach_workflow(
            self,
            response,
            workflow_summary,
            source_context=source_context,
        )

    def _turn_result(
        self,
        text: str,
        response_class: Any,
        *,
        workflow_summary: str = "",
        debug_origin: str | None = None,
        allow_planner_style: bool = False,
    ) -> Any:
        return agent_response_runtime.turn_result(
            type(self).ChatTurnResult,
            text,
            response_class,
            workflow_summary=workflow_summary,
            debug_origin=debug_origin,
            allow_planner_style=allow_planner_style,
        )

    def _decorate_chat_response(
        self,
        response: Any,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        workflow_summary: str = "",
        include_hive_footer: bool | None = None,
    ) -> str:
        return agent_response_runtime.decorate_chat_response(
            self,
            response,
            session_id=session_id,
            source_context=source_context,
            workflow_summary=workflow_summary,
            include_hive_footer=include_hive_footer,
        )

    def _shape_user_facing_text(self, result: Any) -> str:
        return agent_response_runtime.shape_user_facing_text(self, result)

    def _should_show_workflow_for_result(
        self,
        result: Any,
        *,
        source_context: dict[str, object] | None,
    ) -> bool:
        return agent_response_runtime.should_show_workflow_for_result(
            self,
            result,
            source_context=source_context,
        )

    def _sanitize_user_chat_text(
        self,
        text: str,
        *,
        response_class: Any,
        allow_planner_style: bool = False,
    ) -> str:
        return agent_response_runtime.sanitize_user_chat_text(
            self,
            text,
            response_class=response_class,
            allow_planner_style=allow_planner_style,
        )

    def _strip_runtime_preamble(self, text: str, *, allow_planner_style: bool = False) -> str:
        return agent_response_runtime.strip_runtime_preamble(text, allow_planner_style=allow_planner_style)

    def _strip_planner_leakage(self, text: str) -> str:
        return agent_response_runtime.strip_planner_leakage(self, text)

    def _contains_generic_planner_scaffold(self, text: str) -> bool:
        return agent_response_runtime.contains_generic_planner_scaffold(self, text)

    def _unwrap_summary_or_action_payload(self, text: str) -> str:
        return agent_response_runtime.unwrap_summary_or_action_payload(text)

    def _fast_path_response_class(self, *, reason: str, response: str) -> Any:
        return agent_response_policy.fast_path_response_class(self, reason=reason, response=response)

    def _classify_hive_text_response(self, response: str) -> Any:
        return agent_response_policy.classify_hive_text_response(self, response)

    def _action_response_class(
        self,
        *,
        reason: str,
        success: bool,
        task_outcome: str | None,
        response: str,
    ) -> Any:
        return agent_response_policy.action_response_class(
            self,
            reason=reason,
            success=success,
            task_outcome=task_outcome,
            response=response,
        )

    def _grounded_response_class(self, *, gate: Any, classification: dict[str, Any]) -> Any:
        return agent_response_policy.grounded_response_class(self, gate=gate)

    def _should_show_workflow_summary(
        self,
        *,
        response: str,
        workflow_summary: str,
        source_context: dict[str, object] | None,
    ) -> bool:
        return agent_response_policy.should_show_workflow_summary(
            response=response,
            workflow_summary=workflow_summary,
            source_context=source_context,
        )

    def _task_workflow_summary(
        self,
        *,
        classification: dict[str, Any],
        context_result: Any,
        model_execution: dict[str, Any],
        media_analysis: dict[str, Any],
        curiosity_result: dict[str, Any],
        gate_mode: str,
    ) -> str:
        return agent_orchestrator_runtime.task_workflow_summary(
            classification=classification,
            context_result=context_result,
            model_execution=model_execution,
            media_analysis=media_analysis,
            curiosity_result=curiosity_result,
            gate_mode=gate_mode,
        )

    def _action_workflow_summary(
        self,
        *,
        operator_kind: str,
        dispatch_status: str,
        details: dict[str, Any] | None,
    ) -> str:
        return agent_orchestrator_runtime.action_workflow_summary(
            operator_kind=operator_kind,
            dispatch_status=dispatch_status,
            details=details,
        )

    def _tool_intent_workflow_summary(
        self,
        *,
        tool_name: str,
        dispatch_status: str,
        provider_id: str | None,
        validation_state: str,
    ) -> str:
        lines = [
            f"- model selected tool intent `{tool_name or 'unknown'}`",
            f"- tool state: `{dispatch_status}`",
        ]
        provider = str(provider_id or "").strip()
        if provider:
            lines.append(f"- tool intent provider: `{provider}`")
        validation = str(validation_state or "").strip()
        if validation:
            lines.append(f"- tool intent validation: `{validation}`")
        return "\n".join(lines)

    def _tool_intent_direct_message(self, structured_output: Any) -> str | None:
        return agent_response_policy.tool_intent_direct_message(structured_output)

    def _append_tool_result_to_source_context(
        self,
        source_context: dict[str, Any] | None,
        *,
        execution: Any,
        tool_name: str,
    ) -> dict[str, Any]:
        return agent_response_policy.append_tool_result_to_source_context(
            self,
            source_context,
            execution=execution,
            tool_name=tool_name,
        )

    def _normalize_tool_history_message(self, item: dict[str, Any]) -> dict[str, str]:
        return agent_response_policy.normalize_tool_history_message(self, item)

    def _tool_surface_for_history(self, tool_name: str) -> str:
        return agent_response_policy.tool_surface_for_history(tool_name)

    def _tool_history_observation_payload(
        self,
        *,
        execution: Any,
        tool_name: str,
    ) -> dict[str, Any]:
        return agent_response_policy.tool_history_observation_payload(
            execution=execution,
            tool_name=tool_name,
        )

    def _tool_history_observation_prompt(self, observation: dict[str, Any]) -> str:
        return agent_orchestrator_runtime.tool_history_observation_prompt(observation)

    def _tool_history_observation_message(
        self,
        *,
        execution: Any,
        tool_name: str,
    ) -> dict[str, str]:
        return agent_response_policy.tool_history_observation_message(
            self,
            execution=execution,
            tool_name=tool_name,
        )

    def _tool_loop_final_message(self, synthesis: Any, executed_steps: list[dict[str, Any]]) -> str:
        return agent_orchestrator_runtime.tool_loop_final_message(synthesis, executed_steps)

    def _render_tool_loop_response(
        self,
        *,
        final_message: str,
        executed_steps: list[dict[str, Any]],
        include_step_summary: bool = True,
    ) -> str:
        return agent_orchestrator_runtime.render_tool_loop_response(
            final_message=final_message,
            executed_steps=executed_steps,
            include_step_summary=include_step_summary,
        )

    def _tool_intent_loop_workflow_summary(
        self,
        *,
        executed_steps: list[dict[str, Any]],
        provider_id: str | None,
        validation_state: str,
    ) -> str:
        return agent_orchestrator_runtime.tool_intent_loop_workflow_summary(
            executed_steps=executed_steps,
            provider_id=provider_id,
            validation_state=validation_state,
        )

    def _tool_step_summary(self, response_text: str, *, fallback: str) -> str:
        return agent_orchestrator_runtime.tool_step_summary(response_text, fallback=fallback)

    def _runtime_preview(self, text: str, *, limit: int = 220) -> str:
        return agent_orchestrator_runtime.runtime_preview(text, limit=limit)

    def _emit_runtime_event(
        self,
        source_context: dict[str, Any] | None,
        *,
        event_type: str,
        message: str,
        **details: Any,
    ) -> None:
        agent_orchestrator_runtime.emit_runtime_event(
            self,
            source_context,
            event_type=event_type,
            message=message,
            emit_runtime_event_fn=_agent_module().emit_runtime_event,
            **details,
        )

    def _live_runtime_stream_enabled(self, source_context: dict[str, Any] | None) -> bool:
        return agent_orchestrator_runtime.live_runtime_stream_enabled(source_context)
