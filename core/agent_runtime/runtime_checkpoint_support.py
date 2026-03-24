from __future__ import annotations

from importlib import import_module
from typing import Any

from core.agent_runtime import checkpoints as agent_checkpoint_runtime
from core.hive_activity_tracker import clear_hive_interaction_state, session_hive_state, set_hive_interaction_state
from core.identity_manager import load_active_persona
from core.reasoning_engine import explicit_planner_style_requested
from core.runtime_execution_tools import execute_runtime_tool, looks_like_execution_request
from core.task_router import (
    chat_surface_execution_task_class,
    looks_like_explicit_lookup_request,
    looks_like_public_entity_lookup_request,
    model_execution_profile,
)


def _agent_module() -> Any:
    return import_module("apps.nulla_agent")


class RuntimeCheckpointSupportMixin:
    def _model_routing_profile(
        self,
        *,
        user_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        routed = dict(classification or {})
        is_chat_surface = self._is_chat_truth_surface(source_context)
        planner_style_requested = bool(is_chat_surface and explicit_planner_style_requested(user_input))
        if is_chat_surface:
            routed["task_class"] = chat_surface_execution_task_class(
                str(classification.get("task_class") or "unknown"),
                user_input=user_input,
                context=getattr(interpretation, "as_context", lambda: {})(),
            )
            routed["routing_origin_task_class"] = str(classification.get("task_class") or "unknown")
            routed["planner_style_requested"] = planner_style_requested
        return routed, model_execution_profile(
            str(routed.get("task_class") or "unknown"),
            chat_surface=is_chat_surface,
            planner_style_requested=planner_style_requested,
        )

    def _explicit_runtime_workflow_request(
        self,
        *,
        user_input: str,
        task_class: str,
    ) -> bool:
        text = " ".join(str(user_input or "").split()).strip()
        if not text:
            return False
        lowered = f" {text.lower()} "
        if looks_like_execution_request(text, task_class="unknown"):
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
        if ("http://" in lowered or "https://" in lowered) and any(
            marker in lowered for marker in (" open ", " fetch ", " browse ", " render ")
        ):
            return True
        return bool(
            str(task_class or "").strip().lower() == "integration_orchestration"
            and any(
                marker in lowered
                for marker in (" write the files ", " edit the files ", " patch the files ", " create the files ", " generate the files ")
            )
        )

    def _should_keep_ai_first_chat_lane(
        self,
        *,
        user_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
        checkpoint_state: dict[str, Any] | None,
    ) -> bool:
        if not self._is_chat_truth_surface(source_context):
            return False
        checkpoint_state = dict(checkpoint_state or {})
        if checkpoint_state.get("executed_steps") or checkpoint_state.get("pending_tool_payload") or checkpoint_state.get(
            "last_tool_payload"
        ):
            return False
        # Generic "do it/proceed" phrasing should only evict the chat lane when there is
        # actual resumable runtime state. Otherwise normal conversational requests like
        # "do all step by step" get misrouted into the tool planner and degrade to a
        # fake failure instead of using the provider/chat surface.
        if self._looks_like_explicit_resume_request(user_input):
            return False
        if self._live_info_mode(user_input, interpretation=interpretation):
            return True
        task_class = str(classification.get("task_class") or "unknown")
        routed_task_class = chat_surface_execution_task_class(
            task_class,
            user_input=user_input,
            context=getattr(interpretation, "as_context", lambda: {})(),
        )
        if self._explicit_runtime_workflow_request(
            user_input=user_input,
            task_class=task_class,
        ):
            return False
        lowered_input = " ".join(str(user_input or "").split()).strip().lower()
        if self._looks_like_hive_topic_drafting_request(lowered_input):
            return True
        if looks_like_public_entity_lookup_request(lowered_input) or looks_like_explicit_lookup_request(lowered_input):
            return False
        if any(marker in lowered_input for marker in ("create task", "create new task", "new task for", "add task", "add to hive", "add to the hive")):
            return False
        if "create" in lowered_input and "task" in lowered_input and ("hive" in lowered_input or "topic" in lowered_input):
            return False
        if self._looks_like_builder_request(user_input.lower()):
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

    def _prepare_runtime_checkpoint(
        self,
        *,
        session_id: str,
        raw_user_input: str,
        effective_input: str,
        source_context: dict[str, object] | None,
        allow_followup_resume: bool = True,
    ) -> dict[str, Any]:
        return agent_checkpoint_runtime.prepare_runtime_checkpoint(
            self,
            session_id=session_id,
            raw_user_input=raw_user_input,
            effective_input=effective_input,
            source_context=source_context,
            allow_followup_resume=allow_followup_resume,
            latest_resumable_checkpoint_fn=_agent_module().latest_resumable_checkpoint,
            resume_runtime_checkpoint_fn=_agent_module().resume_runtime_checkpoint,
            create_runtime_checkpoint_fn=_agent_module().create_runtime_checkpoint,
        )

    def _blocks_runtime_followup_resume(
        self,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> bool:
        if self._nullabook_pending.get(session_id):
            return True
        hive_state = session_hive_state(session_id)
        if self._has_pending_hive_create_confirmation(
            session_id=session_id,
            hive_state=hive_state,
            source_context=source_context,
        ):
            return True
        interaction_mode = str(hive_state.get("interaction_mode") or "").strip().lower()
        return interaction_mode in {"hive_task_active", "hive_task_selection_pending"}

    def _resolve_runtime_task(
        self,
        *,
        effective_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> Any:
        return agent_checkpoint_runtime.resolve_runtime_task(
            self,
            effective_input=effective_input,
            session_id=session_id,
            source_context=source_context,
            get_runtime_checkpoint_fn=_agent_module().get_runtime_checkpoint,
            load_task_record_fn=_agent_module().load_task_record,
            create_task_record_fn=_agent_module().create_task_record,
        )

    def _update_runtime_checkpoint_context(
        self,
        source_context: dict[str, object] | None,
        *,
        task_id: str | None = None,
        task_class: str | None = None,
    ) -> None:
        agent_checkpoint_runtime.update_runtime_checkpoint_context(
            source_context,
            task_id=task_id,
            task_class=task_class,
            update_runtime_checkpoint_fn=_agent_module().update_runtime_checkpoint,
        )

    def _finalize_runtime_checkpoint(
        self,
        source_context: dict[str, object] | None,
        *,
        status: str,
        final_response: str = "",
        failure_text: str = "",
    ) -> None:
        agent_checkpoint_runtime.finalize_runtime_checkpoint(
            source_context,
            status=status,
            final_response=final_response,
            failure_text=failure_text,
            finalize_runtime_checkpoint_fn=_agent_module().finalize_runtime_checkpoint,
        )

    def _runtime_checkpoint_id(self, source_context: dict[str, object] | None) -> str:
        return agent_checkpoint_runtime.runtime_checkpoint_id(source_context)

    def _merge_runtime_source_contexts(
        self,
        primary: dict[str, Any] | None,
        secondary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return agent_checkpoint_runtime.merge_runtime_source_contexts(self, primary, secondary)

    def _session_hive_state(self, session_id: str) -> dict[str, Any]:
        return _agent_module().session_hive_state(session_id)

    def _set_hive_interaction_state(self, session_id: str, *, mode: str, payload: dict[str, Any]) -> None:
        set_hive_interaction_state(session_id, mode=mode, payload=payload)

    def _clear_hive_interaction_state(self, session_id: str) -> None:
        clear_hive_interaction_state(session_id)

    def _research_topic_from_signal(self, signal: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        agent_mod = _agent_module()
        return agent_mod.research_topic_from_signal(signal, **kwargs)

    def _pick_autonomous_research_signal(self, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        agent_mod = _agent_module()
        return agent_mod.pick_autonomous_research_signal(rows)

    def _plan_tool_workflow(self, *args: Any, **kwargs: Any) -> Any:
        agent_mod = _agent_module()
        return agent_mod.plan_tool_workflow(*args, **kwargs)

    def _execute_tool_intent(self, *args: Any, **kwargs: Any) -> Any:
        agent_mod = _agent_module()
        return agent_mod.execute_tool_intent(*args, **kwargs)

    def _planned_search_query(self, *args: Any, **kwargs: Any) -> Any:
        return _agent_module().WebAdapter.planned_search_query(*args, **kwargs)

    def _search_query(self, *args: Any, **kwargs: Any) -> Any:
        return _agent_module().WebAdapter.search_query(*args, **kwargs)

    def _should_attempt_tool_intent(self, *args: Any, **kwargs: Any) -> Any:
        return _agent_module().should_attempt_tool_intent(*args, **kwargs)

    def _get_runtime_checkpoint(self, *args: Any, **kwargs: Any) -> Any:
        return _agent_module().get_runtime_checkpoint(*args, **kwargs)

    def _record_runtime_tool_progress(self, *args: Any, **kwargs: Any) -> Any:
        return _agent_module().record_runtime_tool_progress(*args, **kwargs)

    def _render_capability_truth_response(self, *args: Any, **kwargs: Any) -> Any:
        agent_mod = _agent_module()
        return agent_mod.render_capability_truth_response(*args, **kwargs)

    def _load_active_persona(self, *args: Any, **kwargs: Any) -> Any:
        return load_active_persona(*args, **kwargs)

    def _execute_runtime_tool(self, *args: Any, **kwargs: Any) -> Any:
        return execute_runtime_tool(*args, **kwargs)

    def _search_user_heuristics(self, query: str, **kwargs: Any) -> Any:
        agent_mod = _agent_module()
        return agent_mod.search_user_heuristics(query, **kwargs)

    def _looks_like_workspace_bootstrap_request(self, text: str) -> bool:
        agent_mod = _agent_module()
        return agent_mod._looks_like_workspace_bootstrap_request(text)

    def _default_gate(self, plan: Any, classification: dict[str, Any]) -> Any:
        risk_flags = set(classification.get("risk_flags") or []) | set(getattr(plan, "risk_flags", None) or [])

        hard_block = {
            "destructive_command",
            "privileged_action",
            "persistence_attempt",
            "exfiltration_hint",
            "shell_injection_risk",
        }

        if any(flag in hard_block for flag in risk_flags):
            return self.GateDecision(
                mode="blocked",
                reason="Blocked by safety policy due to risk flags.",
                requires_user_approval=False,
                allowed_actions=[],
            )

        if classification.get("task_class") == "risky_system_action":
            return self.GateDecision(
                mode="advice_only",
                reason="System-sensitive task forced to advice-only.",
                requires_user_approval=True,
                allowed_actions=[],
            )

        return self.GateDecision(
            mode="advice_only",
            reason="v1 defaults to advice-only.",
            requires_user_approval=False,
            allowed_actions=[],
        )
