from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core import audit_logger, feedback_engine, policy_engine
from core.agent_runtime import chat_surface as agent_chat_surface_runtime
from core.agent_runtime import checkpoints as agent_checkpoint_runtime
from core.agent_runtime import fast_command_surface as agent_fast_command_surface
from core.agent_runtime import hive_followups as agent_hive_followups
from core.agent_runtime import hive_runtime as agent_hive_runtime
from core.agent_runtime import memory_runtime as agent_memory_runtime
from core.agent_runtime import nullabook as agent_nullabook_runtime
from core.agent_runtime import orchestrator as agent_orchestrator_runtime
from core.agent_runtime import presence as agent_presence_runtime
from core.agent_runtime import response as agent_response_runtime
from core.agent_runtime import turn_dispatch as agent_turn_dispatch
from core.agent_runtime import turn_frontdoor as agent_turn_frontdoor
from core.agent_runtime import turn_reasoning as agent_turn_reasoning
from core.agent_runtime.builder_facade import BuilderFacadeMixin
from core.agent_runtime.fast_path_facade import FastPathFacadeMixin
from core.agent_runtime.hive_topic_facade import HiveTopicFacadeMixin
from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin
from core.autonomous_topic_research import pick_autonomous_research_signal, research_topic_from_signal
from core.channel_actions import dispatch_outbound_post_intent, parse_channel_post_intent
from core.credit_ledger import (
    escrow_credits_for_task,
    get_credit_balance,
    transfer_credits,
)
from core.curiosity_roamer import AdaptiveResearchResult, CuriosityRoamer
from core.hive_activity_tracker import (
    HiveActivityTracker,
    clear_hive_interaction_state,
    prune_stale_hive_interaction_state,
    session_hive_state,
    set_hive_interaction_state,
    update_session_hive_state,
)
from core.human_input_adapter import adapt_user_input, runtime_session_id
from core.identity_manager import load_active_persona
from core.knowledge_fetcher import request_relevant_holders
from core.knowledge_registry import register_local_shard, sync_local_learning_shards
from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent
from core.logging_config import setup_logging
from core.media_analysis_pipeline import MediaAnalysisPipeline
from core.media_ingestion import build_media_context_snippets, ingest_media_evidence
from core.memory_first_router import MemoryFirstRouter
from core.onboarding import get_agent_display_name
from core.parent_orchestrator import orchestrate_parent_task
from core.persistent_memory import (
    append_conversation_event,
    ensure_memory_files,
    maybe_handle_memory_command,
    search_user_heuristics,
    session_memory_policy,
)
from core.public_hive_bridge import PublicHiveBridge
from core.reasoning_engine import (
    Plan,
    build_plan,
    explicit_planner_style_requested,
    inspect_user_response_shape,
    render_response,
    should_use_planner_renderer,
)
from core.runtime_continuity import (
    create_runtime_checkpoint,
    finalize_runtime_checkpoint,
    get_runtime_checkpoint,
    latest_resumable_checkpoint,
    mark_stale_runtime_checkpoints_interrupted,
    record_runtime_tool_progress,
    resume_runtime_checkpoint,
    update_runtime_checkpoint,
)
from core.runtime_execution_tools import execute_runtime_tool, looks_like_execution_request
from core.runtime_task_events import emit_runtime_event
from core.shard_synthesizer import build_generalized_query, from_task_result
from core.task_router import (
    chat_surface_execution_task_class,
    classify,
    create_task_record,
    load_task_record,
    looks_like_explicit_lookup_request,
    looks_like_public_entity_lookup_request,
    looks_like_semantic_hive_request,
    model_execution_profile,
)
from core.tiered_context_loader import TieredContextLoader
from core.tool_intent_executor import (
    _looks_like_workspace_bootstrap_request,
    capability_truth_for_request,
    execute_tool_intent,
    plan_tool_workflow,
    render_capability_truth_response,
    runtime_capability_ledger,
    should_attempt_tool_intent,
    supported_public_capability_tags,
)
from core.user_preferences import load_preferences, maybe_handle_preference_command
from network import signer as signer_mod
from retrieval.swarm_query import dispatch_query_shard
from retrieval.web_adapter import WebAdapter
from storage.db import get_connection

_log = logging.getLogger(__name__)


_HIVE_TOPIC_FULL_ID_RE = re.compile(r"\b([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\b", re.IGNORECASE)
_HIVE_TOPIC_SHORT_ID_RE = re.compile(r"#\s*([0-9a-f]{8,12})\b", re.IGNORECASE)
_HIVE_CREATE_HARD_PRIVACY_RISKS = {
    "identity_marker",
    "name_disclosure",
    "location_disclosure",
    "phone_number",
    "postal_address",
}


@dataclass
class AgentRuntime:
    backend_name: str
    device: str
    persona_id: str
    swarm_enabled: bool


@dataclass
class GateDecision:
    mode: str
    reason: str
    requires_user_approval: bool
    allowed_actions: list[str]


class ResponseClass(str, Enum):
    SMALLTALK = "smalltalk"
    UTILITY_ANSWER = "utility_answer"
    TASK_LIST = "task_list"
    TASK_SELECTION_CLARIFICATION = "task_selection_clarification"
    TASK_STARTED = "task_started"
    TASK_STATUS = "task_status"
    TASK_FAILED_USER_SAFE = "task_failed_user_safe"
    RESEARCH_PROGRESS = "research_progress"
    APPROVAL_REQUIRED = "approval_required"
    SYSTEM_ERROR_USER_SAFE = "system_error_user_safe"
    GENERIC_CONVERSATION = "generic_conversation"


@dataclass
class ChatTurnResult:
    text: str
    response_class: ResponseClass
    workflow_summary: str = ""
    debug_origin: str | None = None
    allow_planner_style: bool = False


class NullaAgent(
    FastPathFacadeMixin,
    HiveTopicFacadeMixin,
    BuilderFacadeMixin,
    ResearchToolLoopFacadeMixin,
):
    ResponseClass = ResponseClass
    ChatTurnResult = ChatTurnResult

    def __init__(self, backend_name: str, device: str, persona_id: str = "default"):
        self.backend_name = backend_name
        self.device = device
        self.persona_id = persona_id
        self.swarm_enabled = True
        self.context_loader = TieredContextLoader()
        self.memory_router = MemoryFirstRouter()
        self.curiosity = CuriosityRoamer()
        self.media_pipeline = MediaAnalysisPipeline()
        self.public_hive_bridge = PublicHiveBridge()
        self.hive_activity_tracker = HiveActivityTracker()
        self._public_presence_lock = threading.Lock()
        self._activity_lock = threading.Lock()
        self._public_presence_running = False
        self._public_presence_registered = False
        self._public_presence_status = "idle"
        self._public_presence_source_context: dict[str, object] | None = None
        self._public_presence_thread: threading.Thread | None = None
        self._idle_commons_running = False
        self._idle_commons_thread: threading.Thread | None = None
        self._last_user_activity_ts = time.time()
        self._last_idle_commons_ts = 0.0
        self._last_idle_hive_research_ts = 0.0
        self._idle_commons_seed_index = 0
        self._hive_create_pending: dict[str, dict[str, Any]] = {}
        self._nullabook_pending: dict[str, dict[str, str]] = {}

    def start(self) -> AgentRuntime:
        setup_logging(
            level=str(policy_engine.get("observability.log_level", "INFO")),
            json_output=bool(policy_engine.get("observability.json_logs", True)),
        )
        mark_stale_runtime_checkpoints_interrupted()
        ensure_memory_files()
        _ = load_active_persona(self.persona_id)
        self._sync_public_presence(status=self._idle_public_presence_status())
        if self._background_runtime_threads_enabled():
            self._start_public_presence_heartbeat()
            self._start_idle_commons_loop()

        return AgentRuntime(
            backend_name=self.backend_name,
            device=self.device,
            persona_id=self.persona_id,
            swarm_enabled=self.swarm_enabled,
        )

    def _background_runtime_threads_enabled(self) -> bool:
        if str(self.backend_name or "").strip().lower().startswith("test-"):
            return False
        if str(self.device or "").strip().lower().endswith("-test"):
            return False
        return not os.environ.get("PYTEST_CURRENT_TEST")

    def run_once(
        self,
        user_input: str,
        *,
        session_id_override: str | None = None,
        source_context: dict[str, object] | None = None,
    ) -> dict:
        persona = load_active_persona(self.persona_id)
        session_id = session_id_override or runtime_session_id(device=self.device, persona_id=self.persona_id)
        self._mark_user_activity()
        runtime_source_context = dict(source_context or {})
        interpreted = adapt_user_input(user_input, session_id=session_id)
        effective_input = interpreted.reconstructed_text or interpreted.normalized_text or user_input
        normalized_input = str(interpreted.normalized_text or "").strip()
        checkpoint_bundle = self._prepare_runtime_checkpoint(
            session_id=session_id,
            raw_user_input=user_input,
            effective_input=effective_input,
            source_context=runtime_source_context,
            allow_followup_resume=not self._blocks_runtime_followup_resume(
                session_id=session_id,
                source_context=runtime_source_context,
            ),
        )
        runtime_source_context = dict(checkpoint_bundle.get("source_context") or runtime_source_context)
        checkpoint_state = str(checkpoint_bundle.get("state") or "created")
        if checkpoint_state == "missing_resume":
            return self._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=(
                    "No interrupted runtime task is available to resume in this session. "
                    "If you were retrying a Discord post: set DISCORD_WEBHOOK_URL or DISCORD_BOT_TOKEN+DISCORD_CHANNEL_ID. "
                    "For Telegram: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Then retry your original message."
                ),
                confidence=0.78,
                source_context=runtime_source_context,
                reason="runtime_resume_missing",
            )
        effective_input = str(checkpoint_bundle.get("effective_input") or effective_input)
        if checkpoint_state == "resumed":
            interpreted = adapt_user_input(effective_input, session_id=session_id)
            normalized_input = str(interpreted.normalized_text or "").strip()
        source_context = runtime_source_context
        source_surface = str((source_context or {}).get("surface", "cli")).lower()
        prune_stale_hive_interaction_state(session_id)
        self._emit_runtime_event(
            source_context,
            event_type="task_resumed" if checkpoint_state == "resumed" else "task_received",
            message=(
                f"Resuming interrupted task: {self._runtime_preview(effective_input)}"
                if checkpoint_state == "resumed"
                else f"Received request: {self._runtime_preview(effective_input)}"
            ),
            request_preview=self._runtime_preview(effective_input, limit=160),
            resume_available=checkpoint_state == "resumed",
        )

        frontdoor_bundle = self._handle_turn_frontdoor(
            raw_user_input=user_input,
            effective_input=effective_input,
            normalized_input=normalized_input,
            source_surface=source_surface,
            session_id=session_id,
            source_context=source_context,
            persona=persona,
            interpreted=interpreted,
        )
        frontdoor_result = frontdoor_bundle.get("result")
        if frontdoor_result is not None:
            return frontdoor_result

        self._sync_public_presence(status="busy", source_context=source_context)
        try:
            turn_bundle = self._prepare_turn_task_bundle(
                effective_input=effective_input,
                user_input=user_input,
                session_id=session_id,
                source_context=source_context,
                interpreted=interpreted,
            )
            frontdoor_result = turn_bundle.get("result")
            if frontdoor_result is not None:
                return frontdoor_result
            task = turn_bundle["task"]
            classification = dict(turn_bundle.get("classification") or {})

            return self._execute_grounded_turn(
                task=task,
                effective_input=effective_input,
                classification=classification,
                interpreted=interpreted,
                persona=persona,
                session_id=session_id,
                source_context=source_context,
            )
        except Exception as exc:
            self._finalize_runtime_checkpoint(
                source_context,
                status="interrupted",
                failure_text=str(exc),
            )
            self._emit_runtime_event(
                source_context,
                event_type="task_interrupted",
                message=f"Task failed: {self._runtime_preview(str(exc), limit=200)}",
            )
            raise
        finally:
            self._sync_public_presence(
                status=self._idle_public_presence_status(),
                source_context=source_context,
            )

    def _maybe_handle_memory_fast_path(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_memory_runtime.maybe_handle_memory_fast_path(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            maybe_handle_memory_command_fn=maybe_handle_memory_command,
        )

    def _model_final_response_text(self, model_execution: Any) -> str:
        return agent_memory_runtime.model_final_response_text(model_execution)

    def _prepare_turn_task_bundle(
        self,
        *,
        effective_input: str,
        user_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
        interpreted: Any,
    ) -> dict[str, Any]:
        return agent_turn_dispatch.prepare_turn_task_bundle(
            self,
            effective_input=effective_input,
            user_input=user_input,
            session_id=session_id,
            source_context=source_context,
            interpreted=interpreted,
            classify_fn=classify,
            parse_channel_post_intent_fn=parse_channel_post_intent,
            dispatch_outbound_post_intent_fn=dispatch_outbound_post_intent,
            parse_operator_action_intent_fn=parse_operator_action_intent,
            dispatch_operator_action_fn=dispatch_operator_action,
        )

    def _handle_turn_frontdoor(
        self,
        *,
        raw_user_input: str,
        effective_input: str,
        normalized_input: str,
        source_surface: str,
        session_id: str,
        source_context: dict[str, object] | None,
        persona: Any,
        interpreted: Any,
    ) -> dict[str, Any]:
        return agent_turn_frontdoor.handle_turn_frontdoor(
            self,
            raw_user_input=raw_user_input,
            effective_input=effective_input,
            normalized_input=normalized_input,
            source_surface=source_surface,
            session_id=session_id,
            source_context=source_context,
            persona=persona,
            interpreted=interpreted,
            maybe_handle_preference_command_fn=maybe_handle_preference_command,
            set_hive_interaction_state_fn=set_hive_interaction_state,
        )

    def _execute_grounded_turn(
        self,
        *,
        task: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpreted: Any,
        persona: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_turn_reasoning.execute_grounded_turn(
            self,
            task=task,
            effective_input=effective_input,
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id=session_id,
            source_context=source_context,
            adapt_user_input_fn=adapt_user_input,
            ingest_media_evidence_fn=ingest_media_evidence,
            build_media_context_snippets_fn=build_media_context_snippets,
            orchestrate_parent_task_fn=orchestrate_parent_task,
            build_plan_fn=build_plan,
            render_response_fn=render_response,
            explicit_planner_style_requested_fn=explicit_planner_style_requested,
            should_use_planner_renderer_fn=should_use_planner_renderer,
            request_relevant_holders_fn=request_relevant_holders,
            dispatch_query_shard_fn=dispatch_query_shard,
            build_generalized_query_fn=build_generalized_query,
            feedback_engine_module=feedback_engine,
            policy_engine_module=policy_engine,
            from_task_result_fn=from_task_result,
            append_conversation_event_fn=append_conversation_event,
            audit_logger_module=audit_logger,
        )

    def _chat_surface_cache_or_memory_source(self, model_execution: Any) -> bool:
        return agent_memory_runtime.chat_surface_cache_or_memory_source(model_execution)

    def _chat_surface_model_final_text(self, model_execution: Any) -> str:
        return agent_memory_runtime.chat_surface_model_final_text(model_execution)

    def _chat_surface_honest_degraded_response(
        self,
        model_execution: Any,
        *,
        user_input: str = "",
        interpretation: Any | None = None,
    ) -> str:
        return agent_memory_runtime.chat_surface_honest_degraded_response(
            self,
            model_execution,
            user_input=user_input,
            interpretation=interpretation,
        )

    def _maybe_handle_credit_command(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None = None,
    ) -> dict | None:
        return agent_fast_command_surface.maybe_handle_credit_command(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            signer_module=signer_mod,
            transfer_credits_fn=transfer_credits,
            get_credit_balance_fn=get_credit_balance,
            escrow_credits_for_task_fn=escrow_credits_for_task,
            session_hive_state_fn=session_hive_state,
            runtime_session_id_fn=runtime_session_id,
        )

    def _fast_path_result(
        self,
        *,
        session_id: str,
        user_input: str,
        response: str,
        confidence: float,
        source_context: dict[str, object] | None,
        reason: str,
    ) -> dict:
        return agent_fast_command_surface.fast_path_result(
            self,
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=confidence,
            source_context=source_context,
            reason=reason,
            append_conversation_event_fn=append_conversation_event,
            audit_logger_module=audit_logger,
        )

    def _action_fast_path_result(
        self,
        *,
        task_id: str,
        session_id: str,
        user_input: str,
        response: str,
        confidence: float,
        source_context: dict[str, object] | None,
        reason: str,
        success: bool,
        details: dict[str, object] | None = None,
        mode_override: str | None = None,
        task_outcome: str | None = None,
        learned_plan: Plan | None = None,
        workflow_summary: str = "",
    ) -> dict:
        return agent_fast_command_surface.action_fast_path_result(
            self,
            task_id=task_id,
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=confidence,
            source_context=source_context,
            reason=reason,
            success=success,
            details=details,
            mode_override=mode_override,
            task_outcome=task_outcome,
            learned_plan=learned_plan,
            workflow_summary=workflow_summary,
            append_conversation_event_fn=append_conversation_event,
            audit_logger_module=audit_logger,
            explicit_planner_style_requested_fn=explicit_planner_style_requested,
        )

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

    def _chat_surface_smalltalk_model_input(self, *, user_input: str, phrase: str) -> str:
        return agent_chat_surface_runtime.smalltalk_model_input(self, user_input=user_input, phrase=phrase)

    def _chat_surface_observation_prompt(
        self,
        *,
        user_input: str,
        observations: dict[str, Any],
    ) -> str:
        return agent_chat_surface_runtime.observation_prompt(
            user_input=user_input,
            observations=observations,
        )

    def _chat_surface_live_info_observations(
        self,
        *,
        query: str,
        mode: str,
        notes: list[dict[str, Any]] | None = None,
        runtime_note: str = "",
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.live_info_observations(
            query=query,
            mode=mode,
            notes=notes,
            runtime_note=runtime_note,
        )

    def _chat_surface_live_info_model_input(
        self,
        *,
        user_input: str,
        query: str,
        mode: str,
        notes: list[dict[str, Any]] | None = None,
        runtime_note: str = "",
    ) -> str:
        return agent_chat_surface_runtime.live_info_model_input(
            self,
            user_input=user_input,
            query=query,
            mode=mode,
            notes=notes,
            runtime_note=runtime_note,
        )

    def _chat_surface_adaptive_research_observations(
        self,
        *,
        task_class: str,
        research_result: AdaptiveResearchResult,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.adaptive_research_observations(
            task_class=task_class,
            research_result=research_result,
        )

    def _chat_surface_adaptive_research_model_input(
        self,
        *,
        user_input: str,
        task_class: str,
        research_result: AdaptiveResearchResult,
    ) -> str:
        return agent_chat_surface_runtime.adaptive_research_model_input(
            self,
            user_input=user_input,
            task_class=task_class,
            research_result=research_result,
        )

    def _chat_surface_credit_status_model_input(
        self,
        *,
        user_input: str,
        credit_snapshot: str,
    ) -> str:
        return agent_chat_surface_runtime.credit_status_model_input(
            user_input=user_input,
            credit_snapshot=credit_snapshot,
        )

    def _chat_surface_hive_model_input(
        self,
        *,
        user_input: str,
        observations: dict[str, Any] | None = None,
        runtime_note: str = "",
    ) -> str:
        return agent_chat_surface_runtime.hive_model_input(
            self,
            user_input=user_input,
            observations=observations,
            runtime_note=runtime_note,
        )

    def _chat_surface_hive_queue_observations(
        self,
        queue_rows: list[dict[str, Any]],
        *,
        lead: str = "",
        truth_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.hive_queue_observations(
            self,
            queue_rows,
            lead=lead,
            truth_payload=truth_payload,
        )

    def _chat_surface_hive_research_result_observations(
        self,
        *,
        topic_id: str,
        title: str,
        result: Any,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.hive_research_result_observations(
            self,
            topic_id=topic_id,
            title=title,
            result=result,
        )

    def _chat_surface_hive_status_observations(
        self,
        *,
        topic_id: str,
        title: str,
        status: str,
        execution_state: str,
        active_claim_count: int,
        artifact_count: int,
        post_count: int,
        latest_post_kind: str,
        latest_post_body: str,
        truth_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.hive_status_observations(
            self,
            topic_id=topic_id,
            title=title,
            status=status,
            execution_state=execution_state,
            active_claim_count=active_claim_count,
            artifact_count=artifact_count,
            post_count=post_count,
            latest_post_kind=latest_post_kind,
            latest_post_body=latest_post_body,
            truth_payload=truth_payload,
        )

    def _chat_surface_hive_command_observations(self, details: dict[str, Any]) -> dict[str, Any]:
        return agent_chat_surface_runtime.hive_command_observations(self, details)

    def _bridge_hive_truth_from_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return agent_chat_surface_runtime.bridge_hive_truth_from_rows(rows)

    def _hive_truth_observation_fields(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        return agent_chat_surface_runtime.hive_truth_observation_fields(payload)

    def _hive_truth_prefix(self, payload: dict[str, Any] | None) -> str:
        return agent_chat_surface_runtime.hive_truth_prefix(self, payload)

    def _qualify_hive_response_text(
        self,
        response_text: str,
        *,
        payload: dict[str, Any] | None,
    ) -> str:
        return agent_chat_surface_runtime.qualify_hive_response_text(
            self,
            response_text,
            payload=payload,
        )

    def _human_age(self, age_seconds: object) -> str:
        return agent_chat_surface_runtime.human_age(age_seconds)

    def _chat_surface_hive_degraded_response(self, details: dict[str, Any]) -> str:
        return agent_chat_surface_runtime.chat_surface_hive_degraded_response(self, details)

    def _chat_surface_hive_wording_result(
        self,
        *,
        session_id: str,
        user_input: str,
        source_context: dict[str, object] | None,
        response_class: ResponseClass,
        reason: str,
        observations: dict[str, Any] | None = None,
        fallback_response: str,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.chat_surface_hive_wording_result(
            self,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            response_class=response_class,
            reason=reason,
            observations=observations,
            fallback_response=fallback_response,
        )

    def _postprocess_hive_chat_surface_text(
        self,
        text: str,
        *,
        response_class: ResponseClass,
        payload: dict[str, Any],
        fallback_response: str,
    ) -> str:
        return agent_chat_surface_runtime.postprocess_hive_chat_surface_text(
            self,
            text,
            response_class=response_class,
            payload=payload,
            fallback_response=fallback_response,
        )

    def _hive_task_list_mentions_real_topics(self, text: str, *, topics: list[dict[str, Any]]) -> bool:
        return agent_chat_surface_runtime.hive_task_list_mentions_real_topics(
            self,
            text,
            topics=topics,
        )

    def _chat_surface_builder_model_input(
        self,
        *,
        user_input: str,
        observations: dict[str, Any],
    ) -> str:
        return agent_chat_surface_runtime.builder_model_input(
            self,
            user_input=user_input,
            observations=observations,
        )

    def _chat_surface_model_wording_result(
        self,
        *,
        session_id: str,
        user_input: str,
        source_context: dict[str, object] | None,
        persona: Any,
        interpretation: Any,
        task_class: str,
        response_class: ResponseClass,
        reason: str,
        model_input: str,
        fallback_response: str,
        tool_backing_sources: list[str] | None = None,
        response_postprocessor: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        return agent_chat_surface_runtime.chat_surface_model_wording_result(
            self,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            persona=persona,
            interpretation=interpretation,
            task_class=task_class,
            response_class=response_class,
            reason=reason,
            model_input=model_input,
            fallback_response=fallback_response,
            tool_backing_sources=tool_backing_sources,
            response_postprocessor=response_postprocessor,
        )

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
        return bool(str(task_class or "").strip().lower() == "integration_orchestration" and any(marker in lowered for marker in (" write the files ", " edit the files ", " patch the files ", " create the files ", " generate the files ")))

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
            latest_resumable_checkpoint_fn=latest_resumable_checkpoint,
            resume_runtime_checkpoint_fn=resume_runtime_checkpoint,
            create_runtime_checkpoint_fn=create_runtime_checkpoint,
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
            get_runtime_checkpoint_fn=get_runtime_checkpoint,
            load_task_record_fn=load_task_record,
            create_task_record_fn=create_task_record,
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
            update_runtime_checkpoint_fn=update_runtime_checkpoint,
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
            finalize_runtime_checkpoint_fn=finalize_runtime_checkpoint,
        )

    def _runtime_checkpoint_id(self, source_context: dict[str, object] | None) -> str:
        return agent_checkpoint_runtime.runtime_checkpoint_id(source_context)

    def _merge_runtime_source_contexts(
        self,
        primary: dict[str, Any] | None,
        secondary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return agent_checkpoint_runtime.merge_runtime_source_contexts(self, primary, secondary)

    def _looks_like_explicit_resume_request(self, text: str) -> bool:
        normalized = self._resume_request_key(text)
        return normalized in {
            "continue",
            "resume",
            "retry",
            "try again",
            "continue please",
            "resume please",
            "keep going",
            "go on",
            "pick up where you left off",
        }

    def _looks_like_resume_request(self, text: str) -> bool:
        return self._looks_like_explicit_resume_request(text) or self._is_proceed_message(text)

    def _resume_request_key(self, text: str) -> str:
        return " ".join(str(text or "").strip().lower().split())

    def _session_hive_state(self, session_id: str) -> dict[str, Any]:
        return session_hive_state(session_id)

    def _set_hive_interaction_state(self, session_id: str, *, mode: str, payload: dict[str, Any]) -> None:
        set_hive_interaction_state(session_id, mode=mode, payload=payload)

    def _clear_hive_interaction_state(self, session_id: str) -> None:
        clear_hive_interaction_state(session_id)

    def _research_topic_from_signal(self, signal: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return research_topic_from_signal(signal, **kwargs)

    def _pick_autonomous_research_signal(self, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        return pick_autonomous_research_signal(rows)

    def _plan_tool_workflow(self, *args: Any, **kwargs: Any) -> Any:
        return plan_tool_workflow(*args, **kwargs)

    def _execute_tool_intent(self, *args: Any, **kwargs: Any) -> Any:
        return execute_tool_intent(*args, **kwargs)

    def _planned_search_query(self, *args: Any, **kwargs: Any) -> Any:
        return WebAdapter.planned_search_query(*args, **kwargs)

    def _search_query(self, *args: Any, **kwargs: Any) -> Any:
        return WebAdapter.search_query(*args, **kwargs)

    def _should_attempt_tool_intent(self, *args: Any, **kwargs: Any) -> Any:
        return should_attempt_tool_intent(*args, **kwargs)

    def _get_runtime_checkpoint(self, *args: Any, **kwargs: Any) -> Any:
        return get_runtime_checkpoint(*args, **kwargs)

    def _record_runtime_tool_progress(self, *args: Any, **kwargs: Any) -> Any:
        return record_runtime_tool_progress(*args, **kwargs)

    def _render_capability_truth_response(self, *args: Any, **kwargs: Any) -> Any:
        return render_capability_truth_response(*args, **kwargs)

    def _load_active_persona(self, *args: Any, **kwargs: Any) -> Any:
        return load_active_persona(*args, **kwargs)

    def _execute_runtime_tool(self, *args: Any, **kwargs: Any) -> Any:
        return execute_runtime_tool(*args, **kwargs)

    def _search_user_heuristics(self, query: str, **kwargs: Any) -> Any:
        return search_user_heuristics(query, **kwargs)

    def _looks_like_workspace_bootstrap_request(self, text: str) -> bool:
        return _looks_like_workspace_bootstrap_request(text)

    _nullabook_pending: dict[str, dict[str, str]]

    @staticmethod
    def _classify_nullabook_intent(lowered: str) -> str | None:
        return agent_nullabook_runtime.classify_nullabook_intent(lowered)

    def _maybe_handle_nullabook_fast_path(
        self,
        user_input: str,
        *,
        raw_user_input: str | None = None,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_nullabook_runtime.maybe_handle_nullabook_fast_path(
            self,
            user_input,
            raw_user_input=raw_user_input,
            session_id=session_id,
            source_context=source_context,
            signer_module=signer_mod,
        )

    def _try_compound_nullabook_message(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_nullabook_runtime.try_compound_nullabook_message(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            signer_module=signer_mod,
        )

    def _handle_nullabook_pending_step(
        self,
        user_input: str,
        lowered: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        pending: dict[str, str],
    ) -> dict[str, Any] | None:
        return agent_nullabook_runtime.handle_nullabook_pending_step(
            self,
            user_input,
            lowered,
            session_id=session_id,
            source_context=source_context,
            pending=pending,
            signer_module=signer_mod,
        )

    def _nullabook_step_handle(
        self, user_input: str, lowered: str, *, session_id: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.nullabook_step_handle(
            self,
            user_input,
            lowered,
            session_id=session_id,
            source_context=source_context,
            signer_module=signer_mod,
        )

    def _nullabook_step_bio(
        self, user_input: str, *, session_id: str, source_context: dict[str, object] | None, pending: dict[str, str],
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.nullabook_step_bio(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            pending=pending,
        )

    def _handle_nullabook_post(
        self, user_input: str, lowered: str, profile: Any, *, session_id: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.handle_nullabook_post(
            self,
            user_input,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    def _execute_nullabook_post(
        self, content: str, profile: Any, *, session_id: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.execute_nullabook_post(
            self,
            content,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    def _nullabook_result(
        self, session_id: str, user_input: str, source_context: dict[str, object] | None, response: str,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.nullabook_result(
            self,
            session_id,
            user_input,
            source_context,
            response,
        )

    def _sync_profile_to_hive(self, profile) -> None:
        agent_nullabook_runtime.sync_profile_to_hive(self, profile)

    @staticmethod
    def _is_nullabook_post_request(lowered: str) -> bool:
        return agent_nullabook_runtime.is_nullabook_post_request(lowered)

    @staticmethod
    def _is_nullabook_delete_request(lowered: str) -> bool:
        return agent_nullabook_runtime.is_nullabook_delete_request(lowered)

    @staticmethod
    def _is_nullabook_edit_request(lowered: str) -> bool:
        return agent_nullabook_runtime.is_nullabook_edit_request(lowered)

    def _handle_nullabook_delete(
        self, user_input: str, lowered: str, profile: Any,
        *, session_id: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.handle_nullabook_delete(
            self,
            user_input,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    def _handle_nullabook_edit(
        self, user_input: str, lowered: str, profile: Any,
        *, session_id: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.handle_nullabook_edit(
            self,
            user_input,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    @staticmethod
    def _extract_post_id(text: str) -> str:
        return agent_nullabook_runtime.extract_post_id(text)

    @staticmethod
    def _extract_edit_content(text: str) -> str:
        return agent_nullabook_runtime.extract_edit_content(text)

    @staticmethod
    def _is_nullabook_create_request(lowered: str) -> bool:
        return agent_nullabook_runtime.is_nullabook_create_request(lowered)

    @staticmethod
    def _extract_nullabook_bio_update(text: str) -> str:
        return agent_nullabook_runtime.extract_nullabook_bio_update(text)

    @staticmethod
    def _extract_twitter_handle(text: str) -> str:
        return agent_nullabook_runtime.extract_twitter_handle(text)

    @staticmethod
    def _extract_handle_from_text(text: str) -> str | None:
        return agent_nullabook_runtime.extract_handle_from_text(text)

    @staticmethod
    def _looks_like_nullabook_handle_rules_question(text: str, lowered: str) -> bool:
        return agent_nullabook_runtime.looks_like_nullabook_handle_rules_question(text, lowered)

    @staticmethod
    def _extract_post_content(text: str) -> str:
        return agent_nullabook_runtime.extract_post_content(text)

    @staticmethod
    def _is_substantive_post_content(text: str) -> bool:
        return agent_nullabook_runtime.is_substantive_post_content(text)

    @staticmethod
    def _looks_like_direct_social_post_request(lowered: str) -> bool:
        return agent_nullabook_runtime.looks_like_direct_social_post_request(lowered)

    @staticmethod
    def _strip_context_subject_suffix(text: str) -> str:
        return agent_nullabook_runtime.strip_context_subject_suffix(text)

    @staticmethod
    def _extract_display_name(text: str) -> str:
        return agent_nullabook_runtime.extract_display_name(text)

    def _handle_nullabook_rename(
        self, new_handle: str, profile: Any, *,
        session_id: str, user_input: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.handle_nullabook_rename(
            self,
            new_handle,
            profile,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
        )

    def _maybe_handle_capability_truth_request(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_fast_command_surface.maybe_handle_capability_truth_request(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            capability_truth_for_request_fn=capability_truth_for_request,
            render_capability_truth_response_fn=render_capability_truth_response,
        )

    def _help_capabilities_text(self) -> str:
        return agent_fast_command_surface.help_capabilities_text(self)

    def _render_credit_status(self, normalized_input: str) -> str:
        return agent_fast_command_surface.render_credit_status(normalized_input)

    def _maybe_attach_workflow(
        self,
        response: str,
        workflow_summary: str,
        *,
        source_context: dict[str, object] | None = None,
    ) -> str:
        prefs = load_preferences()
        if not getattr(prefs, "show_workflow", False):
            return str(response or "")
        summary = str(workflow_summary or "").strip()
        if not summary:
            return str(response or "")
        if not self._should_show_workflow_summary(
            response=response,
            workflow_summary=summary,
            source_context=source_context,
        ):
            return str(response or "")
        return f"Workflow:\n{summary}\n\n{str(response or '').strip()}".strip()

    def _turn_result(
        self,
        text: str,
        response_class: ResponseClass,
        *,
        workflow_summary: str = "",
        debug_origin: str | None = None,
        allow_planner_style: bool = False,
    ) -> ChatTurnResult:
        return agent_response_runtime.turn_result(
            ChatTurnResult,
            text,
            response_class,
            workflow_summary=workflow_summary,
            debug_origin=debug_origin,
            allow_planner_style=allow_planner_style,
        )

    def _decorate_chat_response(
        self,
        response: ChatTurnResult | str,
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

    def _shape_user_facing_text(self, result: ChatTurnResult) -> str:
        return agent_response_runtime.shape_user_facing_text(self, result)

    def _should_show_workflow_for_result(
        self,
        result: ChatTurnResult,
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
        response_class: ResponseClass,
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

    def _should_attach_hive_footer(
        self,
        result: ChatTurnResult,
        *,
        source_context: dict[str, object] | None,
    ) -> bool:
        surface = str((source_context or {}).get("surface", "") or "").strip().lower()
        if surface not in {"channel", "openclaw", "api"}:
            return False
        if result.response_class == ResponseClass.TASK_SELECTION_CLARIFICATION:
            return True
        if result.response_class != ResponseClass.APPROVAL_REQUIRED:
            return False
        lowered = str(result.text or "").strip().lower()
        if "ready to post this to the public hive" in lowered or "confirm? (yes / no)" in lowered:
            return False
        return not ("reply with:" in lowered and "approve " in lowered)

    def _fast_path_response_class(self, *, reason: str, response: str) -> ResponseClass:
        if reason in {"smalltalk_fast_path", "startup_sequence_fast_path"}:
            return ResponseClass.SMALLTALK
        if reason in {
            "date_time_fast_path",
            "direct_math_fast_path",
            "ui_command_fast_path",
            "credit_status_fast_path",
            "memory_command",
            "user_preference_command",
            "live_info_fast_path",
            "capability_truth_query",
            "builder_capability_gap",
            "builder_controller_direct_response",
        }:
            return ResponseClass.UTILITY_ANSWER
        if reason == "help_fast_path":
            return ResponseClass.TASK_SELECTION_CLARIFICATION
        if reason == "evaluative_conversation_fast_path":
            return ResponseClass.GENERIC_CONVERSATION
        if reason == "runtime_resume_missing":
            return ResponseClass.SYSTEM_ERROR_USER_SAFE
        if reason == "hive_activity_command":
            return self._classify_hive_text_response(response)
        if reason == "hive_research_followup":
            lowered = str(response or "").lower()
            if lowered.startswith("started hive research on") or lowered.startswith("autonomous research on"):
                return ResponseClass.TASK_STARTED
            if lowered.startswith("research follow-up:") or lowered.startswith("research result:"):
                return ResponseClass.RESEARCH_PROGRESS
            if "multiple real hive tasks open" in lowered or "pick one by name" in lowered:
                return ResponseClass.TASK_SELECTION_CLARIFICATION
            if "couldn't map that follow-up" in lowered or "couldn't find an open hive task" in lowered:
                return ResponseClass.TASK_SELECTION_CLARIFICATION
            return ResponseClass.TASK_FAILED_USER_SAFE
        if reason == "hive_status_followup":
            return ResponseClass.TASK_STATUS
        return ResponseClass.GENERIC_CONVERSATION

    def _classify_hive_text_response(self, response: str) -> ResponseClass:
        lowered = str(response or "").strip().lower()
        if (
            lowered.startswith("hive watcher is not configured")
            or lowered.startswith("i couldn't reach the hive watcher")
            or lowered.startswith("i couldn't reach hive")
            or lowered.startswith("public hive is not enabled")
        ):
            return ResponseClass.TASK_FAILED_USER_SAFE
        if lowered.startswith("available hive tasks right now"):
            return ResponseClass.TASK_LIST
        if lowered.startswith("i couldn't reach the live hive watcher just now, but these are the real hive tasks i already had in session"):
            return ResponseClass.TASK_LIST
        if lowered.startswith("i couldn't reach the live hive watcher, but i can still pull public hive tasks"):
            return ResponseClass.TASK_LIST
        if lowered.startswith("live hive watcher is not configured here, but i can still pull public hive tasks"):
            return ResponseClass.TASK_LIST
        if lowered.startswith("online now:"):
            return ResponseClass.TASK_LIST
        if "pick one by name" in lowered or "point at the task name" in lowered:
            return ResponseClass.TASK_SELECTION_CLARIFICATION
        if lowered.startswith("no open hive tasks"):
            return ResponseClass.TASK_STATUS
        return ResponseClass.TASK_STATUS

    def _action_response_class(
        self,
        *,
        reason: str,
        success: bool,
        task_outcome: str | None,
        response: str,
    ) -> ResponseClass:
        lowered = str(response or "").lower()
        if task_outcome == "pending_approval":
            return ResponseClass.APPROVAL_REQUIRED
        if not success:
            return ResponseClass.TASK_FAILED_USER_SAFE
        if "started hive research on" in lowered or lowered.startswith("autonomous research on"):
            return ResponseClass.TASK_STARTED
        if reason.startswith("model_tool_intent_"):
            return ResponseClass.RESEARCH_PROGRESS
        if reason.startswith("hive_topic_create_"):
            return ResponseClass.TASK_STATUS
        return ResponseClass.GENERIC_CONVERSATION

    def _grounded_response_class(self, *, gate: GateDecision, classification: dict[str, Any]) -> ResponseClass:
        if bool(getattr(gate, "requires_user_approval", False)) or str(getattr(gate, "mode", "") or "").lower() in {"approval_required", "tool_preview"}:
            return ResponseClass.APPROVAL_REQUIRED
        return ResponseClass.GENERIC_CONVERSATION

    def _apply_interaction_transition(self, session_id: str, result: ChatTurnResult) -> None:
        agent_orchestrator_runtime.apply_interaction_transition(
            self,
            session_id,
            result,
            session_hive_state_fn=session_hive_state,
            set_hive_interaction_state_fn=set_hive_interaction_state,
        )

    def _maybe_handle_hive_frontdoor(
        self,
        *,
        raw_user_input: str,
        effective_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
        return agent_hive_followups.maybe_handle_hive_frontdoor(
            self,
            raw_user_input=raw_user_input,
            effective_input=effective_input,
            session_id=session_id,
            source_context=source_context,
        )

    def _maybe_handle_hive_runtime_command(
        self,
        user_input: str,
        *,
        session_id: str,
    ) -> tuple[bool, str, bool, dict[str, Any] | None]:
        return agent_hive_runtime.maybe_handle_hive_runtime_command(
            self,
            user_input,
            session_id=session_id,
        )

    def _recover_hive_runtime_command_input(self, user_input: str) -> str:
        return agent_hive_runtime.recover_hive_runtime_command_input(
            self,
            user_input,
            looks_like_semantic_hive_request_fn=looks_like_semantic_hive_request,
        )

    def _hive_tracker_needs_bridge_fallback(self, response: str) -> bool:
        return agent_hive_runtime.hive_tracker_needs_bridge_fallback(response)

    def _looks_like_hive_prompt_control_command(self, user_input: str) -> bool:
        return agent_hive_runtime.looks_like_hive_prompt_control_command(user_input)

    def _maybe_handle_hive_bridge_fallback(
        self,
        user_input: str,
        *,
        session_id: str,
        tracker_response: str,
    ) -> dict[str, Any] | None:
        return agent_hive_runtime.maybe_handle_hive_bridge_fallback(
            self,
            user_input,
            session_id=session_id,
            tracker_response=tracker_response,
        )

    def _store_hive_topic_selection_state(
        self,
        session_id: str,
        topics: list[dict[str, Any]],
    ) -> None:
        agent_hive_runtime.store_hive_topic_selection_state(
            session_id,
            topics,
            session_hive_state_fn=session_hive_state,
            update_session_hive_state_fn=update_session_hive_state,
        )

    def _should_show_workflow_summary(
        self,
        *,
        response: str,
        workflow_summary: str,
        source_context: dict[str, object] | None,
    ) -> bool:
        surface = str((source_context or {}).get("surface", "") or "").strip().lower()
        response_text = str(response or "").strip()
        if surface not in {"channel", "openclaw", "api"}:
            return True
        if "recognized operator action" in workflow_summary:
            return True
        if "classified task as `research`" in workflow_summary:
            return True
        if "classified task as `integration_orchestration`" in workflow_summary:
            return True
        if "classified task as `system_design`" in workflow_summary:
            return True
        if "classified task as `debugging`" in workflow_summary:
            return True
        if "classified task as `code_" in workflow_summary:
            return True
        if "curiosity/research lane: `executed`" in workflow_summary:
            return True
        if "execution posture: `tool_" in workflow_summary:
            return True
        return len(response_text) >= 280

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
        if not isinstance(structured_output, dict):
            return None
        intent = str(structured_output.get("intent") or "").strip().lower()
        if intent not in {"respond.direct", "none", "no_tool"}:
            return None
        arguments = structured_output.get("arguments") or {}
        if not isinstance(arguments, dict):
            return None
        message = str(arguments.get("message") or arguments.get("response") or "").strip()
        return message or None

    def _append_tool_result_to_source_context(
        self,
        source_context: dict[str, Any] | None,
        *,
        execution: Any,
        tool_name: str,
    ) -> dict[str, Any]:
        updated = dict(source_context or {})
        history = list(updated.get("conversation_history") or [])
        observation_message = self._tool_history_observation_message(
            execution=execution,
            tool_name=tool_name,
        )
        if history and history[-1] == observation_message:
            updated["conversation_history"] = history[-12:]
            return updated
        history.append(observation_message)
        updated["conversation_history"] = history[-12:]
        return updated

    def _normalize_tool_history_message(self, item: dict[str, Any]) -> dict[str, str]:
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role != "assistant" or not content.startswith("Real tool result from `"):
            return {"role": role, "content": content}
        match = re.match(r"^Real tool result from `([^`]+)`:\s*(.*)$", content, re.DOTALL)
        if not match:
            return {"role": role, "content": content}
        tool_name = str(match.group(1) or "").strip() or "tool"
        response_text = str(match.group(2) or "").strip()
        observation = {
            "schema": "tool_observation_v1",
            "intent": tool_name,
            "tool_surface": self._tool_surface_for_history(tool_name),
            "ok": True,
            "status": "executed",
            "response_preview": response_text[:1800] if response_text else "No tool output returned.",
        }
        return {
            "role": "user",
            "content": self._tool_history_observation_prompt(observation),
        }

    def _tool_surface_for_history(self, tool_name: str) -> str:
        lowered = str(tool_name or "").strip().lower()
        if lowered.startswith("web.") or lowered.startswith("browser."):
            return "web"
        if lowered.startswith("workspace."):
            return "workspace"
        if lowered.startswith("sandbox."):
            return "sandbox"
        if lowered.startswith("operator."):
            return "local_operator"
        if lowered.startswith("hive."):
            return "hive"
        return "runtime_tool"

    def _tool_history_observation_payload(
        self,
        *,
        execution: Any,
        tool_name: str,
    ) -> dict[str, Any]:
        details = dict(getattr(execution, "details", {}) or {})
        observation = details.get("observation")
        if isinstance(observation, dict) and observation:
            payload = dict(observation)
        else:
            response_text = str(getattr(execution, "response_text", "") or "").strip()
            payload = {
                "schema": "tool_observation_v1",
                "intent": str(tool_name or getattr(execution, "tool_name", "") or "tool").strip() or "tool",
                "tool_surface": self._tool_surface_for_history(str(tool_name or getattr(execution, "tool_name", "") or "tool")),
                "ok": bool(getattr(execution, "ok", False)),
                "status": str(getattr(execution, "status", "") or "executed").strip() or "executed",
                "response_preview": response_text[:1800] if response_text else "No tool output returned.",
            }
        payload.setdefault("mode", str(getattr(execution, "mode", "") or "").strip())
        if not payload.get("response_preview"):
            response_text = str(getattr(execution, "response_text", "") or "").strip()
            if response_text:
                payload["response_preview"] = response_text[:1800]
        return payload

    def _tool_history_observation_prompt(self, observation: dict[str, Any]) -> str:
        return agent_orchestrator_runtime.tool_history_observation_prompt(observation)

    def _tool_history_observation_message(
        self,
        *,
        execution: Any,
        tool_name: str,
    ) -> dict[str, str]:
        observation = self._tool_history_observation_payload(
            execution=execution,
            tool_name=tool_name,
        )
        return {
            "role": "user",
            "content": self._tool_history_observation_prompt(observation),
        }

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
            emit_runtime_event_fn=emit_runtime_event,
            **details,
        )

    def _live_runtime_stream_enabled(self, source_context: dict[str, Any] | None) -> bool:
        return agent_orchestrator_runtime.live_runtime_stream_enabled(source_context)

    def _sync_public_presence(
        self,
        *,
        status: str,
        source_context: dict[str, object] | None = None,
    ) -> None:
        agent_presence_runtime.sync_public_presence(
            self,
            status=status,
            source_context=source_context,
            get_agent_display_name_fn=get_agent_display_name,
            audit_log_fn=audit_logger.log,
        )

    def _start_public_presence_heartbeat(self) -> None:
        agent_presence_runtime.start_public_presence_heartbeat(
            self,
            thread_factory=threading.Thread,
        )

    def _start_idle_commons_loop(self) -> None:
        agent_presence_runtime.start_idle_commons_loop(
            self,
            thread_factory=threading.Thread,
        )

    def _public_presence_heartbeat_loop(self) -> None:
        agent_presence_runtime.public_presence_heartbeat_loop(
            self,
            sleep_fn=time.sleep,
        )

    def _idle_commons_loop(self) -> None:
        agent_presence_runtime.idle_commons_loop(
            self,
            sleep_fn=time.sleep,
            audit_log_fn=audit_logger.log,
        )

    def _maybe_run_idle_commons_once(self) -> None:
        agent_presence_runtime.maybe_run_idle_commons_once(
            self,
            load_preferences_fn=load_preferences,
            time_fn=time.time,
            audit_log_fn=audit_logger.log,
        )

    def _maybe_run_autonomous_hive_research_once(self) -> None:
        agent_presence_runtime.maybe_run_autonomous_hive_research_once(
            self,
            load_preferences_fn=load_preferences,
            time_fn=time.time,
            pick_signal_fn=pick_autonomous_research_signal,
            research_topic_fn=research_topic_from_signal,
            audit_log_fn=audit_logger.log,
        )

    def _mark_user_activity(self) -> None:
        with self._activity_lock:
            self._last_user_activity_ts = time.time()

    def _idle_commons_session_id(self) -> str:
        return agent_presence_runtime.idle_commons_session_id(
            get_local_peer_id_fn=signer_mod.get_local_peer_id,
        )

    def _normalize_public_presence_status(self, status: str) -> str:
        return agent_presence_runtime.normalize_public_presence_status(self, status)

    def _idle_public_presence_status(self) -> str:
        return agent_presence_runtime.idle_public_presence_status(
            load_preferences_fn=load_preferences,
        )

    def _public_transport_source(self, source_context: dict[str, object] | None) -> dict[str, object]:
        if source_context:
            return dict(source_context)
        with self._public_presence_lock:
            return dict(self._public_presence_source_context or {})

    def _maybe_publish_public_task(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        assistant_response: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        if str(getattr(task, "share_scope", "local_only") or "local_only") != "public_knowledge":
            return None
        try:
            result = self.public_hive_bridge.publish_public_task(
                task_id=str(getattr(task, "task_id", "") or ""),
                task_summary=str(getattr(task, "task_summary", "") or ""),
                task_class=str(classification.get("task_class") or "unknown"),
                assistant_response=assistant_response,
                topic_tags=[str(tag) for tag in list(classification.get("topic_hints") or [])[:6]],
            )
            audit_logger.log(
                "public_hive_task_export",
                target_id=str(getattr(task, "task_id", "") or ""),
                target_type="task",
                details={
                    "share_scope": getattr(task, "share_scope", "local_only"),
                    "session_id": session_id,
                    **dict(result or {}),
                },
            )
            return dict(result or {})
        except Exception as exc:
            audit_logger.log(
                "public_hive_task_export_error",
                target_id=str(getattr(task, "task_id", "") or ""),
                target_type="task",
                details={
                    "error": str(exc),
                    "share_scope": getattr(task, "share_scope", "local_only"),
                    "session_id": session_id,
                },
            )
        return None

    def _maybe_hive_footer(
        self,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> str:
        surface = str((source_context or {}).get("surface", "") or "").lower()
        if surface not in {"channel", "openclaw", "api"}:
            return ""
        prefs = load_preferences()
        try:
            return self.hive_activity_tracker.build_chat_footer(
                session_id=session_id,
                hive_followups_enabled=bool(getattr(prefs, "hive_followups", True)),
                idle_research_assist=bool(getattr(prefs, "idle_research_assist", True)),
            )
        except Exception as exc:
            audit_logger.log(
                "hive_activity_footer_error",
                target_id=session_id,
                target_type="session",
                details={"error": str(exc)},
            )
            return ""

    _PROCEED_PATTERNS: frozenset[str] = frozenset({
        "proceed", "carry on", "continue", "do it", "do all", "go ahead",
        "start working", "yes", "yes proceed", "yes do it", "ok do it",
        "ok proceed", "ok go ahead", "deliver it", "submit it", "just do it",
        "yes pls", "yes please", "all good carry on", "proceed with next steps",
        "proceed with that", "all good", "no proceed",
    })
    _HIVE_REVIEW_ACTION_RE = re.compile(
        r"\b(?P<decision>approve|approved|reject|rejected|needs?\s+more\s+evidence|needs?\s+improvement|send\s+back|quarantine|void)\b"
        r"(?:\s+(?:the\s+)?)?"
        r"(?:(?P<object_type>post|topic)\s+)?"
        r"(?:#)?(?P<object_id>[a-z0-9][a-z0-9-]{5,255})\b",
        re.IGNORECASE,
    )

    def _is_proceed_message(self, text: str) -> bool:
        compact = " ".join(str(text or "").strip().lower().split()).strip(" \t\n\r?!.,")
        if compact in self._PROCEED_PATTERNS:
            return True
        padded = f" {compact} "
        if any(f" {p} " in padded for p in (
            "proceed", "carry on", "continue", "do it", "do all",
            "go ahead", "start working", "just do it",
        )):
            return True
        return bool(any(marker in compact for marker in ("do research", "start research", "run research", "deliver to hive", "deliver to the hive", "deliver it to hive", "deliver it to the hive", "submit to hive", "submit to the hive", "post to hive", "research and deliver", "research it", "do it properly")))

    def _maybe_handle_hive_review_command(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_followups.maybe_handle_hive_review_command(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
        )

    def _looks_like_hive_review_queue_command(self, lowered: str) -> bool:
        return agent_hive_followups.looks_like_hive_review_queue_command(lowered)

    def _parse_hive_review_action(self, user_input: str) -> dict[str, str] | None:
        return agent_hive_followups.parse_hive_review_action(user_input)

    def _looks_like_hive_cleanup_command(self, lowered: str) -> bool:
        return agent_hive_followups.looks_like_hive_cleanup_command(lowered)

    def _handle_hive_review_queue_command(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_hive_followups.handle_hive_review_queue_command(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
        )

    def _handle_hive_review_action(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        review_action: dict[str, str],
    ) -> dict[str, Any]:
        return agent_hive_followups.handle_hive_review_action(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            review_action=review_action,
        )

    def _handle_hive_cleanup_command(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_hive_followups.handle_hive_cleanup_command(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
        )

    def _looks_like_disposable_hive_cleanup_topic(self, topic: dict[str, Any]) -> bool:
        return agent_hive_followups.looks_like_disposable_hive_cleanup_topic(topic)

    def _maybe_handle_hive_research_followup(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_followups.maybe_handle_hive_research_followup(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            session_hive_state_fn=session_hive_state,
            clear_hive_interaction_state_fn=clear_hive_interaction_state,
            set_hive_interaction_state_fn=set_hive_interaction_state,
            research_topic_from_signal_fn=research_topic_from_signal,
        )

    def _maybe_resume_active_hive_task(
        self,
        lowered: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        hive_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        return agent_hive_followups.maybe_resume_active_hive_task(
            self,
            lowered,
            session_id=session_id,
            source_context=source_context,
            hive_state=hive_state,
            set_hive_interaction_state_fn=set_hive_interaction_state,
            research_topic_from_signal_fn=research_topic_from_signal,
        )

    def _extract_hive_topic_hint(self, text: str) -> str:
        return agent_hive_followups.extract_hive_topic_hint(text)

    def _append_footer(self, response: str, *, prefix: str, footer: str) -> str:
        clean_response = str(response or "").strip()
        clean_footer = str(footer or "").strip()
        if not clean_footer:
            return clean_response
        if clean_footer.lower().startswith(f"{str(prefix or '').strip().lower()}:"):
            return f"{clean_response}\n\n{clean_footer}".strip()
        return f"{clean_response}\n\n{prefix}:\n{clean_footer}".strip()

    def _public_capabilities(self) -> list[str]:
        capabilities = [
            "persistent_memory",
            "chat_continuity",
            *supported_public_capability_tags(limit=12),
        ]
        build_entry = self._workspace_build_capability_entry()
        if build_entry.get("supported"):
            capabilities.append(str(build_entry.get("capability_id") or "workspace.build_scaffold"))
        seen: set[str] = set()
        out: list[str] = []
        for item in capabilities:
            if item in seen:
                continue
            seen.add(item)
            out.append(item[:64])
            if len(out) >= 16:
                break
        return out

    def _capability_ledger_entries(self) -> list[dict[str, Any]]:
        entries = [dict(entry) for entry in runtime_capability_ledger()]
        entries.append(self._workspace_build_capability_entry())
        return entries

    def _workspace_build_capability_entry(self) -> dict[str, Any]:
        write_enabled = bool(policy_engine.get("filesystem.allow_write_workspace", False))
        sandbox_enabled = bool(policy_engine.get("execution.allow_sandbox_execution", False))
        verification_note = (
            "bounded verification can run through local commands"
            if sandbox_enabled
            else "verification is limited because sandbox execution is disabled"
        )
        return {
            "capability_id": "workspace.build_scaffold",
            "surface": "workspace",
            "supported": write_enabled,
            "support_level": "partial" if write_enabled else "unsupported",
            "claim": (
                "run bounded local build/edit/run/inspect loops in the active workspace, including starter folders/files and narrow Telegram or Discord bot scaffolds; "
                f"{verification_note}"
            ),
            "partial_reason": (
                "This is still a bounded local builder controller, not a full autonomous research -> build -> debug -> test loop for arbitrary software."
                if write_enabled
                else ""
            ),
            "unsupported_reason": "Workspace scaffold generation is disabled because workspace writes are not enabled on this runtime.",
            "nearby_capability_ids": ["workspace.write", "sandbox.command"],
            "public_tag": "workspace.build_scaffold",
        }

    def _public_transport_mode(self, source_context: dict[str, object] | None) -> str:
        resolved_context = self._public_transport_source(source_context)
        surface = str((resolved_context or {}).get("surface") or "").strip().lower()
        platform = str((resolved_context or {}).get("platform") or "").strip().lower()
        if surface and platform:
            return f"{surface}_{platform}"[:64]
        if surface:
            return surface[:64]
        if platform:
            return platform[:64]
        return "nulla_agent"

    def _default_gate(self, plan: Plan, classification: dict) -> GateDecision:
        risk_flags = set(classification.get("risk_flags") or []) | set(plan.risk_flags or [])

        hard_block = {
            "destructive_command",
            "privileged_action",
            "persistence_attempt",
            "exfiltration_hint",
            "shell_injection_risk",
        }

        if any(flag in hard_block for flag in risk_flags):
            return GateDecision(
                mode="blocked",
                reason="Blocked by safety policy due to risk flags.",
                requires_user_approval=False,
                allowed_actions=[],
            )

        if classification.get("task_class") == "risky_system_action":
            return GateDecision(
                mode="advice_only",
                reason="System-sensitive task forced to advice-only.",
                requires_user_approval=True,
                allowed_actions=[],
            )

        return GateDecision(
            mode="advice_only",
            reason="v1 defaults to advice-only.",
            requires_user_approval=False,
            allowed_actions=[],
        )

    def _update_task_class(self, task_id: str, task_class: str) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE local_tasks
                SET task_class = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
                """,
                (task_class, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _promote_verified_action_shard(self, task_id: str, plan: Plan) -> None:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT task_id, session_id, task_class, task_summary, environment_os, environment_shell,
                       environment_runtime, environment_version_hint
                FROM local_tasks
                WHERE task_id = ?
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            return

        task_view = type("TaskView", (), dict(row))()
        outcome = type(
            "ActionOutcome",
            (),
            {
                "status": "success",
                "is_success": True,
                "is_durable": True,
                "harmful_flag": False,
                "confidence_before": float(plan.confidence),
                "confidence_after": min(1.0, float(plan.confidence) + 0.05),
            },
        )()
        shard = from_task_result(task_view, plan, outcome)
        if policy_engine.validate_learned_shard(shard):
            self._store_local_shard(
                shard,
                origin_task_id=task_id,
                origin_session_id=str(getattr(task_view, "session_id", "") or ""),
            )

    def _update_task_result(self, task_id: str, *, outcome: str, confidence: float) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE local_tasks
                SET outcome = ?,
                    confidence = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    str(outcome),
                    max(0.0, min(1.0, float(confidence))),
                    datetime.now(timezone.utc).isoformat(),
                    task_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _store_local_shard(
        self,
        shard: dict,
        *,
        origin_task_id: str | None = None,
        origin_session_id: str | None = None,
    ) -> None:
        policy = session_memory_policy(origin_session_id)
        requested_share_scope = str(policy.get("share_scope") or "local_only")
        restricted_terms = list(policy.get("restricted_terms") or [])
        effective_share_scope = requested_share_scope
        outbound_reasons: list[str] = []
        if requested_share_scope != "local_only":
            outbound_reasons = policy_engine.outbound_shard_validation_errors(
                shard,
                share_scope=requested_share_scope,
                restricted_terms=restricted_terms,
            )
            if outbound_reasons:
                effective_share_scope = "local_only"

        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, origin_task_id, origin_session_id, share_scope,
                    restricted_terms_json, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0,
                    'active', ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE((SELECT created_at FROM learning_shards WHERE shard_id = ?), CURRENT_TIMESTAMP),
                    CURRENT_TIMESTAMP
                )
                """,
                (
                    shard["shard_id"],
                    int(shard["schema_version"]),
                    shard["problem_class"],
                    shard["problem_signature"],
                    shard["summary"],
                    json.dumps(shard["resolution_pattern"], sort_keys=True),
                    json.dumps(shard["environment_tags"], sort_keys=True),
                    shard["source_type"],
                    shard["source_node_id"],
                    float(shard["quality_score"]),
                    float(shard["trust_score"]),
                    json.dumps(shard["risk_flags"], sort_keys=True),
                    shard["freshness_ts"],
                    shard["expires_ts"],
                    shard["signature"],
                    str(origin_task_id or ""),
                    str(origin_session_id or ""),
                    effective_share_scope,
                    json.dumps(restricted_terms, sort_keys=True),
                    shard["shard_id"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

        audit_logger.log(
            "local_shard_stored",
            target_id=shard["shard_id"],
            target_type="shard",
            details={
                "problem_class": shard["problem_class"],
                "requested_share_scope": requested_share_scope,
                "effective_share_scope": effective_share_scope,
                "privacy_blocked": bool(outbound_reasons),
                "privacy_reasons": outbound_reasons,
            },
        )
        if effective_share_scope != "local_only":
            manifest = register_local_shard(str(shard["shard_id"]), restricted_terms=restricted_terms)
            if not manifest:
                audit_logger.log(
                    "local_shard_kept_candidate_only",
                    target_id=shard["shard_id"],
                    target_type="shard",
                    details={"reason": "shareability_gate_blocked"},
                )
            elif policy_engine.get("shards.marketplace_auto_list", False):
                with contextlib.suppress(Exception):
                    from core.knowledge_marketplace import publish_listing
                    from network.signer import get_local_peer_id as _mp_peer

                    publish_listing(
                        shard_id=str(shard["shard_id"]),
                        seller_peer_id=_mp_peer(),
                        title=str(shard.get("summary", ""))[:128] or shard["problem_class"],
                        description=str(shard.get("summary", "")),
                        domain_tags=[shard["problem_class"]],
                        price_credits=float(policy_engine.get("shards.marketplace_default_price", 1.0)),
                        quality_score=float(shard.get("quality_score", 0.5)),
                    )
        with contextlib.suppress(Exception):
            sync_local_learning_shards()


def main() -> int:
    parser = argparse.ArgumentParser(prog="nulla-agent")
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--persona", default="default")
    parser.add_argument("--input", default="")
    parser.add_argument("--json", action="store_true", help="Print full response payload as JSON.")
    args = parser.parse_args()

    from core.runtime_bootstrap import bootstrap_runtime_mode

    backend_name = str(args.backend)
    device = str(args.device)
    boot = bootstrap_runtime_mode(
        mode="agent",
        force_policy_reload=True,
        resolve_backend=backend_name == "auto" or device == "auto",
    )

    if backend_name == "auto" or device == "auto":
        selection = boot.backend_selection
        if selection is None:
            raise RuntimeError("Runtime bootstrap did not resolve a backend selection.")
        backend_name = backend_name if backend_name != "auto" else selection.backend_name
        device = device if device != "auto" else selection.device

    agent = NullaAgent(
        backend_name=backend_name,
        device=device,
        persona_id=str(args.persona),
    )
    agent.start()
    if not str(args.input or "").strip():
        print("Nulla agent started. Provide --input for one-shot execution.")
        return 0

    result = agent.run_once(str(args.input))
    if bool(args.json):
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(str(result.get("response") or "").strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
