from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core import audit_logger, feedback_engine, policy_engine
from core.agent_runtime import checkpoints as agent_checkpoint_runtime
from core.agent_runtime import hive_followups as agent_hive_followups
from core.agent_runtime import hive_runtime as agent_hive_runtime
from core.agent_runtime import hive_topics as agent_hive_topics
from core.agent_runtime import memory_runtime as agent_memory_runtime
from core.agent_runtime import nullabook as agent_nullabook_runtime
from core.agent_runtime import orchestrator as agent_orchestrator_runtime
from core.agent_runtime import presence as agent_presence_runtime
from core.agent_runtime import response as agent_response_runtime
from core.agent_runtime import turn_dispatch as agent_turn_dispatch
from core.agent_runtime import turn_frontdoor as agent_turn_frontdoor
from core.agent_runtime import turn_reasoning as agent_turn_reasoning
from core.agent_runtime.builder import controller as agent_builder_controller
from core.agent_runtime.builder import scaffolds as agent_builder_scaffolds
from core.agent_runtime.builder import support as agent_builder_support
from core.agent_runtime.fast_path_facade import FastPathFacadeMixin
from core.autonomous_topic_research import pick_autonomous_research_signal, research_topic_from_signal
from core.candidate_knowledge_lane import get_candidate_by_id
from core.channel_actions import dispatch_outbound_post_intent, parse_channel_post_intent
from core.credit_ledger import (
    escrow_credits_for_task,
    get_credit_balance,
    list_credit_ledger_entries,
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


class NullaAgent(FastPathFacadeMixin):
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

    _CREDIT_SEND_RE = re.compile(
        r"(?:send|transfer|give)\s+(\d+(?:\.\d+)?)\s+credits?\s+(?:to\s+)?(\S+)",
        re.IGNORECASE,
    )
    _CREDIT_SPEND_RE = re.compile(
        r"spend\s+(\d+(?:\.\d+)?)\s+credits?\s+(?:to\s+)?(?:prioriti[sz]e|boost|fund)",
        re.IGNORECASE,
    )

    def _maybe_handle_credit_command(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None = None,
    ) -> dict | None:
        send_match = self._CREDIT_SEND_RE.search(user_input)
        if send_match:
            amount = float(send_match.group(1))
            target_peer = send_match.group(2).strip()
            peer_id = signer_mod.get_local_peer_id()
            ok = transfer_credits(peer_id, target_peer, amount, reason="chat_transfer")
            if ok:
                response = f"Sent {amount:.2f} credits to {target_peer}. Your new balance: {get_credit_balance(peer_id):.2f}."
            else:
                balance = get_credit_balance(peer_id)
                response = f"Transfer failed. Your balance is {balance:.2f} credits (need {amount:.2f})."
            session_id = runtime_session_id(device=self.device, persona_id=self.persona_id)
            return {
                "task_id": str(uuid.uuid4()),
                "response": response,
                "response_class": "task_status",
                "confidence": 0.95,
                "mode": "fast_path",
                "model_execution": {"used_model": False, "source": "credit_ledger"},
                "session_id": session_id,
                "source_context": source_context or {},
            }

        spend_match = self._CREDIT_SPEND_RE.search(user_input)
        if spend_match:
            amount = float(spend_match.group(1))
            peer_id = signer_mod.get_local_peer_id()
            hive_state = session_hive_state(session_id)
            interaction_payload = dict(hive_state.get("interaction_payload") or {})
            active_topic_id = str(interaction_payload.get("active_topic_id") or "").strip()
            wants_current_hive_task = any(
                marker in " ".join(str(user_input or "").strip().lower().split())
                for marker in ("current hive task", "this hive task", "active hive task")
            )
            task_id = active_topic_id or str(uuid.uuid4())
            if wants_current_hive_task and not active_topic_id:
                response = "I don't have an active Hive task selected in this session, so I can't attach credits to a real task yet."
                return {
                    "task_id": str(uuid.uuid4()),
                    "response": response,
                    "response_class": "task_status",
                    "confidence": 0.9,
                    "mode": "fast_path",
                    "model_execution": {"used_model": False, "source": "credit_ledger"},
                    "session_id": session_id,
                    "source_context": source_context or {},
                }
            ok = escrow_credits_for_task(peer_id, task_id, amount)
            if ok:
                if active_topic_id:
                    response = (
                        f"Reserved {amount:.2f} credits to prioritize Hive task `{active_topic_id[:8]}`. "
                        f"Remaining balance: {get_credit_balance(peer_id):.2f}."
                    )
                else:
                    response = f"Reserved {amount:.2f} credits to prioritize your Hive task. Remaining balance: {get_credit_balance(peer_id):.2f}."
            else:
                balance = get_credit_balance(peer_id)
                response = f"Could not reserve credits. Your balance is {balance:.2f} (need {amount:.2f})."
            return {
                "task_id": task_id,
                "response": response,
                "response_class": "task_status",
                "confidence": 0.95,
                "mode": "fast_path",
                "model_execution": {"used_model": False, "source": "credit_ledger"},
                "session_id": session_id,
                "source_context": source_context or {},
            }

        return None

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
        pseudo_task_id = f"fast-{uuid.uuid4().hex[:12]}"
        turn_result = self._turn_result(
            response,
            self._fast_path_response_class(reason=reason, response=response),
            debug_origin=reason,
        )
        self._apply_interaction_transition(session_id, turn_result)
        decorated_response = self._decorate_chat_response(
            turn_result,
            session_id=session_id,
            source_context=source_context,
        )
        append_conversation_event(
            session_id=session_id,
            user_input=user_input,
            assistant_output=decorated_response,
            source_context=source_context,
        )
        audit_logger.log(
            "agent_fast_path_response",
            target_id=pseudo_task_id,
            target_type="task",
            details={"reason": reason, "source_surface": (source_context or {}).get("surface")},
        )
        self._emit_chat_truth_metrics(
            task_id=pseudo_task_id,
            reason=reason,
            response_text=decorated_response,
            response_class=turn_result.response_class.value,
            source_context=source_context,
            rendered_via="fast_path",
            fast_path_hit=True,
            model_inference_used=False,
            model_final_answer_hit=False,
            model_execution_source="fast_path",
            tool_backing_sources=self._chat_truth_fast_path_backing_sources(reason),
        )
        self._emit_runtime_event(
            source_context,
            event_type="task_completed",
            message=f"Fast-path response ready: {self._runtime_preview(decorated_response)}",
            task_id=pseudo_task_id,
            status=reason,
        )
        self._finalize_runtime_checkpoint(
            source_context,
            status="completed",
            final_response=decorated_response,
        )
        return {
            "task_id": pseudo_task_id,
            "response": str(decorated_response or ""),
            "mode": "advice_only",
            "confidence": float(confidence),
            "understanding_confidence": 1.0,
            "interpreted_input": user_input,
            "topic_hints": [],
            "prompt_assembly_report": {},
            "model_execution": {"source": "fast_path", "used_model": False},
            "media_analysis": {"used_provider": False, "reason": "fast_path"},
            "curiosity": {"mode": "skipped", "reason": "fast_path"},
            "backend": self.backend_name,
            "device": self.device,
            "session_id": session_id,
            "source_context": dict(source_context or {}),
            "workflow_summary": "",
            "response_class": turn_result.response_class.value,
        }

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
        turn_result = self._turn_result(
            response,
            self._action_response_class(
                reason=reason,
                success=success,
                task_outcome=task_outcome,
                response=response,
            ),
            workflow_summary=workflow_summary,
            debug_origin=reason,
            allow_planner_style=explicit_planner_style_requested(user_input),
        )
        self._apply_interaction_transition(session_id, turn_result)
        decorated_response = self._decorate_chat_response(
            turn_result,
            session_id=session_id,
            source_context=source_context,
        )
        append_conversation_event(
            session_id=session_id,
            user_input=user_input,
            assistant_output=decorated_response,
            source_context=source_context,
        )
        self._update_task_result(
            task_id,
            outcome=task_outcome or ("success" if success else "failed"),
            confidence=confidence,
        )
        if success and learned_plan is not None:
            self._promote_verified_action_shard(task_id, learned_plan)
        audit_logger.log(
            "agent_channel_action",
            target_id=task_id,
            target_type="task",
            details={
                "reason": reason,
                "success": bool(success),
                "source_surface": (source_context or {}).get("surface"),
                "source_platform": (source_context or {}).get("platform"),
                **dict(details or {}),
            },
        )
        self._emit_chat_truth_metrics(
            task_id=task_id,
            reason=reason,
            response_text=decorated_response,
            response_class=turn_result.response_class.value,
            source_context=source_context,
            rendered_via="action_fast_path",
            fast_path_hit=True,
            model_inference_used=False,
            model_final_answer_hit=False,
            model_execution_source="channel_action",
            tool_backing_sources=self._chat_truth_action_backing_sources(
                reason=reason,
                success=success,
                task_outcome=task_outcome,
            ),
        )
        checkpoint_status = "completed" if success and (task_outcome or "success") == "success" else (
            "pending_approval" if (task_outcome or "") == "pending_approval" else "failed"
        )
        event_type = (
            "task_completed"
            if checkpoint_status == "completed"
            else "task_pending_approval"
            if checkpoint_status == "pending_approval"
            else "task_failed"
        )
        self._emit_runtime_event(
            source_context,
            event_type=event_type,
            message=(
                f"{'Completed' if checkpoint_status == 'completed' else 'Awaiting approval for' if checkpoint_status == 'pending_approval' else 'Failed'} action response: "
                f"{self._runtime_preview(decorated_response)}"
            ),
            task_id=task_id,
            status=reason,
        )
        self._finalize_runtime_checkpoint(
            source_context,
            status=checkpoint_status,
            final_response=decorated_response if checkpoint_status == "completed" else "",
            failure_text="" if checkpoint_status != "failed" else decorated_response,
        )
        return {
            "task_id": task_id,
            "response": str(decorated_response or ""),
            "mode": mode_override or ("tool_queued" if success else "tool_failed"),
            "confidence": float(confidence),
            "understanding_confidence": 1.0,
            "interpreted_input": user_input,
            "topic_hints": ["discord" if "discord" in user_input.lower() else "telegram" if "telegram" in user_input.lower() else "channel"],
            "prompt_assembly_report": {},
            "model_execution": {"source": "channel_action", "used_model": False},
            "media_analysis": {"used_provider": False, "reason": "channel_action"},
            "curiosity": {"mode": "skipped", "reason": "channel_action"},
            "backend": self.backend_name,
            "device": self.device,
            "session_id": session_id,
            "source_context": dict(source_context or {}),
            "workflow_summary": workflow_summary,
            "response_class": turn_result.response_class.value,
        }

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
        normalized_phrase = str(phrase or "").strip().lower()
        if normalized_phrase in {"what can you do", "help"}:
            capability_summary = self._help_capabilities_text().strip()
            return (
                f"{user_input}\n\n"
                "Ground your reply in currently wired runtime capabilities only. "
                "Do not imply unsupported abilities.\n\n"
                f"{capability_summary}"
            )
        return str(user_input or "").strip()

    def _chat_surface_observation_prompt(
        self,
        *,
        user_input: str,
        observations: dict[str, Any],
    ) -> str:
        channel = str(observations.get("channel") or "").strip()
        mode = str(observations.get("mode") or "").strip()
        if channel == "live_info" and mode == "fresh_lookup":
            grounding = (
                "IMPORTANT: Answer ONLY using the search results below. "
                "If the search results do not contain the answer, say so honestly — "
                "do NOT guess or fill in from general knowledge. "
                "Cite the source domain when possible."
            )
        else:
            grounding = "Grounding observations for this turn. Use them as evidence, not as a template:"
        return (
            f"{str(user_input or '').strip()}\n\n"
            f"{grounding}\n"
            f"{json.dumps(dict(observations or {}), indent=2, sort_keys=True)}"
        ).strip()

    def _chat_surface_live_info_observations(
        self,
        *,
        query: str,
        mode: str,
        notes: list[dict[str, Any]] | None = None,
        runtime_note: str = "",
    ) -> dict[str, Any]:
        sources: list[dict[str, Any]] = []
        browser_used = False
        for note in list(notes or [])[:4]:
            entry = {
                "title": str(note.get("result_title") or note.get("origin_domain") or "Source").strip(),
                "domain": str(note.get("origin_domain") or "").strip(),
                "summary": " ".join(str(note.get("summary") or "").split()).strip(),
                "url": str(note.get("result_url") or "").strip(),
            }
            if str(note.get("source_profile_label") or "").strip():
                entry["source_profile"] = str(note.get("source_profile_label") or "").strip()
            if isinstance(note.get("live_quote"), dict):
                entry["quote"] = dict(note.get("live_quote") or {})
            if bool(note.get("used_browser")):
                entry["used_browser"] = True
                browser_used = True
            sources.append(entry)
        observations: dict[str, Any] = {
            "channel": "live_info",
            "mode": str(mode or "").strip(),
            "query": str(query or "").strip(),
            "source_count": len(sources),
            "sources": sources,
        }
        if browser_used:
            observations["browser_rendering_used"] = True
        if str(runtime_note or "").strip():
            observations["runtime_note"] = str(runtime_note or "").strip()
        return observations

    def _chat_surface_live_info_model_input(
        self,
        *,
        user_input: str,
        query: str,
        mode: str,
        notes: list[dict[str, Any]] | None = None,
        runtime_note: str = "",
    ) -> str:
        runtime_message = str(runtime_note or "").strip() or (
            "" if notes else self._live_info_failure_text(query=query, mode=mode)
        )
        return self._chat_surface_observation_prompt(
            user_input=user_input,
            observations=self._chat_surface_live_info_observations(
                query=query,
                mode=mode,
                notes=notes,
                runtime_note=runtime_message,
            ),
        )

    def _chat_surface_adaptive_research_observations(
        self,
        *,
        task_class: str,
        research_result: AdaptiveResearchResult,
    ) -> dict[str, Any]:
        notes = [dict(note) for note in list(research_result.notes or []) if isinstance(note, dict)]
        sources: list[dict[str, Any]] = []
        for note in notes[:4]:
            source = {
                "title": str(note.get("result_title") or note.get("title") or note.get("result_url") or "Source").strip(),
                "domain": str(note.get("origin_domain") or "").strip(),
                "summary": " ".join(str(note.get("summary") or note.get("snippet") or "").split()).strip(),
                "url": str(note.get("result_url") or note.get("url") or "").strip(),
            }
            if note.get("source_profile_label"):
                source["source_profile"] = str(note.get("source_profile_label") or "").strip()
            raw_confidence = note.get("confidence")
            if raw_confidence not in {None, ""}:
                with contextlib.suppress(Exception):
                    source["confidence"] = float(raw_confidence)
            sources.append(source)
        observations: dict[str, Any] = {
            "channel": "adaptive_research",
            "task_class": str(task_class or "unknown").strip(),
            "strategy": str(research_result.strategy or "general_research").strip(),
            "actions_taken": list(research_result.actions_taken or []),
            "queries_run": list(research_result.queries_run or []),
            "evidence_strength": str(research_result.evidence_strength or "none").strip(),
            "source_domains": list(research_result.source_domains or []),
            "source_count": len(sources),
            "sources": sources,
        }
        if research_result.escalated_from_chat:
            observations["escalated_from_chat"] = True
        if research_result.broadened:
            observations["broadened"] = True
        if research_result.narrowed:
            observations["narrowed"] = True
        if research_result.compared_sources:
            observations["compared_sources"] = True
        if research_result.verified_claim:
            observations["verified_claim"] = True
        if research_result.stop_reason:
            observations["stop_reason"] = str(research_result.stop_reason).strip()
        if research_result.admitted_uncertainty:
            observations["admitted_uncertainty"] = True
            observations["uncertainty_reason"] = str(research_result.uncertainty_reason or research_result.tool_gap_note or "").strip()
        elif research_result.tool_gap_note:
            observations["runtime_note"] = str(research_result.tool_gap_note).strip()
        return observations

    def _chat_surface_adaptive_research_model_input(
        self,
        *,
        user_input: str,
        task_class: str,
        research_result: AdaptiveResearchResult,
    ) -> str:
        return self._chat_surface_observation_prompt(
            user_input=user_input,
            observations=self._chat_surface_adaptive_research_observations(
                task_class=task_class,
                research_result=research_result,
            ),
        )

    def _chat_surface_credit_status_model_input(
        self,
        *,
        user_input: str,
        credit_snapshot: str,
    ) -> str:
        return (
            f"{str(user_input or '').strip()}\n\n"
            "Verified local credit, score, and wallet state for this turn:\n"
            f"{str(credit_snapshot or '').strip()}"
        ).strip()

    def _chat_surface_hive_model_input(
        self,
        *,
        user_input: str,
        observations: dict[str, Any] | None = None,
        runtime_note: str = "",
    ) -> str:
        payload = dict(observations or {})
        if str(runtime_note or "").strip():
            payload["runtime_note"] = str(runtime_note or "").strip()
        if not payload:
            payload = {"channel": "hive", "runtime_note": "Hive evidence was unavailable for this turn."}
        payload["_system_context"] = (
            "IMPORTANT: When the user says 'hive mind', 'hive', 'brain hive', or 'public hive', "
            "they mean the Brain Hive task queue — a decentralized research system where tasks are "
            "listed, claimed, researched, and resolved. Do NOT interpret 'hive mind' as the concept "
            "of collective intelligence. Report the actual task state from the observations below. "
            "The user can: check tasks, pick one to research, create new tasks, deliver research results."
        )
        return self._chat_surface_observation_prompt(
            user_input=user_input,
            observations=payload,
        )

    def _chat_surface_hive_queue_observations(
        self,
        queue_rows: list[dict[str, Any]],
        *,
        lead: str = "",
        truth_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        observations = {
            "channel": "hive",
            "kind": "task_list",
            "lead": str(lead or "").strip(),
            "task_count": len(list(queue_rows or [])),
            "topics": [
                {
                    "topic_id": str(row.get("topic_id") or "").strip(),
                    "title": str(row.get("title") or "Untitled topic").strip(),
                    "status": str(row.get("status") or "open").strip(),
                }
                for row in list(queue_rows or [])[:5]
            ],
        }
        observations.update(self._hive_truth_observation_fields(truth_payload or self._bridge_hive_truth_from_rows(queue_rows)))
        return observations

    def _chat_surface_hive_research_result_observations(
        self,
        *,
        topic_id: str,
        title: str,
        result: Any,
    ) -> dict[str, Any]:
        observations: dict[str, Any] = {
            "channel": "hive",
            "kind": "research_followup",
            "topic": {
                "topic_id": str(topic_id or "").strip(),
                "short_id": str(topic_id or "")[:8],
                "title": str(title or "Hive topic").strip(),
            },
            "dispatch_status": str(result.status or "").strip(),
        }
        if result.claim_id:
            observations["claim_id"] = str(result.claim_id).strip()
        result_status = str(result.result_status or "").strip()
        if result_status:
            observations["topic_status_after_dispatch"] = result_status
        query_count = len(list((result.details or {}).get("query_results") or []))
        if query_count:
            observations["bounded_query_count"] = query_count
        if result.artifact_ids:
            observations["artifact_count"] = len(result.artifact_ids)
        if result.candidate_ids:
            observations["candidate_note_count"] = len(result.candidate_ids)
        response_text = " ".join(str(result.response_text or "").split()).strip()
        if response_text:
            observations["research_runtime_note"] = response_text
        details = dict(result.details or {})
        synthesis_card = details.get("synthesis_card")
        if isinstance(synthesis_card, dict):
            observations["research_synthesis"] = {
                "question": str(synthesis_card.get("question") or "").strip()[:200],
                "searched": list(synthesis_card.get("searched") or [])[:5],
                "found": list(synthesis_card.get("found") or [])[:5],
                "promoted_findings": list(synthesis_card.get("promoted_findings") or [])[:5],
                "confidence": str(synthesis_card.get("confidence") or "").strip(),
                "blockers": list(synthesis_card.get("blockers") or [])[:6],
            }
        query_results = list(details.get("query_results") or [])
        if query_results:
            observations["query_summaries"] = [
                {
                    "query": str(q.get("query") or "").strip()[:120],
                    "summary": str(q.get("summary") or q.get("snippet") or "").strip()[:400],
                }
                for q in query_results[:6]
                if str(q.get("summary") or q.get("snippet") or "").strip()
            ]
        quality_summary = details.get("quality_summary")
        if isinstance(quality_summary, dict):
            observations["research_quality"] = {
                "status": str(quality_summary.get("status") or "").strip(),
                "evidence_count": int(quality_summary.get("evidence_count") or 0),
                "confidence": str(quality_summary.get("confidence") or "").strip(),
            }
        observations.update(
            self._hive_truth_observation_fields(
                {
                    "truth_source": "public_bridge",
                    "truth_label": "public-bridge-derived",
                    "truth_status": "write_path",
                }
            )
        )
        return observations

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
        observations: dict[str, Any] = {
            "channel": "hive",
            "kind": "status",
            "topic": {
                "topic_id": str(topic_id or "").strip(),
                "short_id": str(topic_id or "")[:8],
                "title": str(title or "Hive topic").strip(),
            },
        }
        if status:
            observations["topic_status"] = status
        if execution_state:
            observations["execution_state"] = execution_state
        if active_claim_count:
            observations["active_claim_count"] = active_claim_count
        if post_count:
            observations["post_count"] = post_count
        if artifact_count:
            observations["artifact_count"] = artifact_count
        if latest_post_kind or latest_post_body:
            latest = latest_post_body[:220] if latest_post_body else ""
            observations["latest_post"] = {
                "kind": latest_post_kind or "post",
                "body": latest,
            }
        observations.update(self._hive_truth_observation_fields(truth_payload))
        return observations

    def _chat_surface_hive_command_observations(self, details: dict[str, Any]) -> dict[str, Any]:
        observations = {
            "channel": "hive",
            "kind": str(details.get("command_kind") or "command").strip(),
            "watcher_status": str(details.get("watcher_status") or "").strip(),
            "lead": str(details.get("lead") or "").strip(),
            "topics": [
                {
                    "topic_id": str(topic.get("topic_id") or "").strip(),
                    "title": str(topic.get("title") or "Untitled topic").strip(),
                    "status": str(topic.get("status") or "open").strip(),
                }
                for topic in list(details.get("topics") or [])[:5]
            ],
            "online_agents": [
                {
                    "agent_id": str(agent.get("agent_id") or "").strip(),
                    "display_name": str(agent.get("display_name") or agent.get("claim_label") or "agent").strip(),
                    "status": str(agent.get("status") or "").strip(),
                    "online": bool(agent.get("online")),
                }
                for agent in list(details.get("online_agents") or [])[:4]
            ],
        }
        observations.update(self._hive_truth_observation_fields(details))
        return observations

    def _bridge_hive_truth_from_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        first = dict((list(rows or [])[:1] or [{}])[0] or {})
        return {
            "truth_source": str(first.get("truth_source") or "public_bridge").strip(),
            "truth_label": str(first.get("truth_label") or "public-bridge-derived").strip(),
            "truth_status": str(first.get("truth_transport") or "read_path").strip(),
        }

    def _hive_truth_observation_fields(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        raw = dict(payload or {})
        observations: dict[str, Any] = {}
        for key in ("truth_source", "truth_label", "truth_status", "truth_timestamp"):
            value = raw.get(key)
            if value not in {None, ""}:
                observations[key] = value
        presence: dict[str, Any] = {}
        for source_key, target_key in (
            ("presence_claim_state", "claim_state"),
            ("presence_source", "source"),
            ("presence_truth_label", "truth_label"),
            ("presence_freshness_label", "freshness_label"),
            ("presence_age_seconds", "age_seconds"),
            ("presence_note", "note"),
        ):
            value = raw.get(source_key)
            if value not in {None, ""}:
                presence[target_key] = value
        if presence:
            observations["presence"] = presence
        return observations

    def _hive_truth_prefix(self, payload: dict[str, Any] | None) -> str:
        raw = dict(payload or {})
        presence = dict(raw.get("presence") or {})
        truth_label = str(raw.get("truth_label") or "").strip()
        if not truth_label:
            return ""
        parts = [f"Hive truth: {truth_label}."]
        presence_claim_state = str(raw.get("presence_claim_state") or presence.get("claim_state") or "").strip().lower()
        presence_note = str(raw.get("presence_note") or presence.get("note") or "").strip()
        presence_truth_label = str(raw.get("presence_truth_label") or presence.get("truth_label") or truth_label).strip()
        freshness_label = str(raw.get("presence_freshness_label") or presence.get("freshness_label") or "").strip().lower()
        age_seconds = raw.get("presence_age_seconds")
        if age_seconds in {None, ""}:
            age_seconds = presence.get("age_seconds")
        if presence_claim_state == "visible":
            freshness_suffix = freshness_label
            if freshness_label in {"fresh", "stale"} and age_seconds is not None:
                freshness_suffix = f"{freshness_label} ({self._human_age(age_seconds)} old)"
            elif freshness_label == "unknown":
                freshness_suffix = "freshness unknown"
            parts.append(f"Presence truth: {presence_truth_label}, {freshness_suffix}.")
        elif presence_note:
            parts.append(f"Presence truth: {presence_note}.")
        return " ".join(part for part in parts if part).strip()

    def _qualify_hive_response_text(
        self,
        response_text: str,
        *,
        payload: dict[str, Any] | None,
    ) -> str:
        clean = str(response_text or "").strip()
        prefix = self._hive_truth_prefix(payload)
        if not prefix:
            return clean
        lowered = clean.lower()
        if "hive truth:" in lowered and ("presence truth:" in lowered or "presence" not in prefix.lower()):
            return clean
        if not clean:
            return prefix
        return f"{prefix} {clean}".strip()

    def _human_age(self, age_seconds: object) -> str:
        try:
            value = max(0, int(age_seconds))  # type: ignore[arg-type]
        except Exception:
            return ""
        if value < 60:
            return f"{value}s"
        if value < 3600:
            return f"{max(1, round(value / 60))}m"
        return f"{max(1, round(value / 3600))}h"

    def _chat_surface_hive_degraded_response(self, details: dict[str, Any]) -> str:
        topics = list(details.get("topics") or [])
        online_agents = list(details.get("online_agents") or [])
        watcher_status = str(details.get("watcher_status") or "").strip().lower()
        truth_prefix = self._hive_truth_prefix(details)
        if topics:
            lines = [f"{truth_prefix} Hive tasks:"]
            for topic in topics[:6]:
                title = str(topic.get("title") or "Untitled topic").strip()
                short_id = str(topic.get("topic_id") or "")[:8]
                status = str(topic.get("status") or "open").strip()
                lines.append(f"- [{status}] {title} (#{short_id})")
            agent_count = len(online_agents)
            if agent_count:
                lines.append(f"{agent_count} agent(s) online.")
            lines.append("Pick one by name or #id to start research, or say 'create task' to add a new one.")
            return "\n".join(lines).strip()
        if online_agents:
            agent_count = len(online_agents)
            return f"{truth_prefix} {agent_count} agent(s) online on Hive, but no open tasks found.".strip()
        if watcher_status == "not_configured":
            return f"{truth_prefix} Hive watcher is not configured on this runtime.".strip()
        if watcher_status == "unreachable":
            return f"{truth_prefix} Hive watcher was unreachable this turn.".strip()
        return f"{truth_prefix} No live Hive data available this turn.".strip()

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
        truth_payload = dict(observations or {})
        qualified_fallback = self._qualify_hive_response_text(fallback_response, payload=truth_payload)
        return self._chat_surface_model_wording_result(
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            persona=load_active_persona(self.persona_id),
            interpretation=adapt_user_input(user_input, session_id=session_id),
            task_class="research",
            response_class=response_class,
            reason=reason,
            model_input=self._chat_surface_hive_model_input(
                user_input=user_input,
                observations=observations,
                runtime_note=qualified_fallback,
            ),
            fallback_response=qualified_fallback,
            tool_backing_sources=["hive"],
            response_postprocessor=lambda text: self._postprocess_hive_chat_surface_text(
                text,
                response_class=response_class,
                payload=truth_payload,
                fallback_response=qualified_fallback,
            ),
        )

    def _postprocess_hive_chat_surface_text(
        self,
        text: str,
        *,
        response_class: ResponseClass,
        payload: dict[str, Any],
        fallback_response: str,
    ) -> str:
        clean = str(text or "").strip()
        qualified = self._qualify_hive_response_text(clean, payload=payload)
        lowered = qualified.lower()
        if response_class == ResponseClass.TASK_STARTED:
            if self._contains_generic_planner_scaffold(qualified):
                return str(fallback_response or "").strip()
            if not any(
                marker in lowered
                for marker in (
                    "started hive research on",
                    "started research on",
                    "first bounded pass",
                    "claim",
                    "posted",
                    "research lane is active",
                )
            ):
                return str(fallback_response or "").strip()
            return qualified
        _HIVE_CONCEPT_HALLUCINATION_MARKERS = (
            "concept of a",
            "concept of collective",
            "collective intelligence",
            "no specific information",
            "no information related to",
            "hive mind is a term",
            "hive mind refers to",
            "swarm intelligence",
        )
        if any(marker in lowered for marker in _HIVE_CONCEPT_HALLUCINATION_MARKERS):
            return str(fallback_response or "").strip()
        if response_class != ResponseClass.TASK_LIST:
            return qualified
        topics = [
            dict(item)
            for item in list(payload.get("topics") or [])
            if isinstance(item, dict) and str(item.get("title") or item.get("topic_id") or "").strip()
        ]
        if not topics:
            return qualified
        if self._hive_task_list_mentions_real_topics(qualified, topics=topics):
            return qualified
        return str(fallback_response or "").strip()

    def _hive_task_list_mentions_real_topics(self, text: str, *, topics: list[dict[str, Any]]) -> bool:
        normalized_text = self._normalize_hive_topic_text(text)
        compact_text = re.sub(r"\s+", "", str(text or "").lower())
        match_count = 0
        for topic in list(topics or []):
            title = self._normalize_hive_topic_text(str(topic.get("title") or ""))
            short_id = str(topic.get("topic_id") or "").strip().lower()[:8]
            if title and title in normalized_text:
                match_count += 1
                continue
            if short_id and (f"#{short_id}" in compact_text or short_id in compact_text):
                match_count += 1
        required = 1 if len(topics) <= 1 else 2
        return match_count >= required

    def _chat_surface_builder_model_input(
        self,
        *,
        user_input: str,
        observations: dict[str, Any],
    ) -> str:
        return self._chat_surface_observation_prompt(
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
        task = self._resolve_runtime_task(
            effective_input=user_input,
            session_id=session_id,
            source_context=source_context,
        )
        self._update_runtime_checkpoint_context(
            source_context,
            task_id=task.task_id,
            task_class=task_class,
        )
        self._update_task_class(task.task_id, task_class)
        model_interpretation = adapt_user_input(model_input, session_id=session_id)
        base_classification = {
            "task_class": task_class,
            "risk_flags": [],
            "confidence_hint": max(
                0.55,
                float(getattr(model_interpretation, "understanding_confidence", 0.0) or 0.0),
            ),
        }
        classification, _ = self._model_routing_profile(
            user_input=user_input,
            classification=base_classification,
            interpretation=model_interpretation,
            source_context=source_context,
        )
        context_result = self.context_loader.load(
            task=task,
            classification=classification,
            interpretation=model_interpretation,
            persona=persona,
            session_id=session_id,
        )
        model_execution = self.memory_router.resolve(
            task=task,
            classification=classification,
            interpretation=model_interpretation,
            context_result=context_result,
            persona=persona,
            force_model=True,
            surface=str((source_context or {}).get("surface", "cli") or "cli"),
            source_context=dict(source_context or {}),
        )
        final_text = self._chat_surface_model_final_text(model_execution)
        model_final_answer_hit = bool(final_text)
        if not final_text:
            model_source = str(getattr(model_execution, "source", "") or "")
            used_model = bool(getattr(model_execution, "used_model", False))
            _log.info(
                "Chat surface model wording fallback: reason=%s model_source=%s used_model=%s",
                reason,
                model_source or "unknown",
                used_model,
            )
            audit_logger.log(
                "chat_surface_model_wording_fallback",
                target_id=task.task_id,
                target_type="task",
                details={
                    "reason": reason,
                    "model_source": model_source,
                    "used_model": used_model,
                    "fallback_preview": str(fallback_response or "")[:120],
                },
            )
            final_text = str(fallback_response or "").strip()
        if response_postprocessor is not None:
            final_text = str(response_postprocessor(final_text) or "").strip()

        turn_result = self._turn_result(
            final_text,
            response_class,
            debug_origin=reason,
        )
        self._apply_interaction_transition(session_id, turn_result)
        decorated_response = self._decorate_chat_response(
            turn_result,
            session_id=session_id,
            source_context=source_context,
        )
        append_conversation_event(
            session_id=session_id,
            user_input=user_input,
            assistant_output=decorated_response,
            source_context=source_context,
        )
        confidence = max(
            0.35,
            min(
                0.96,
                float(getattr(model_execution, "trust_score", 0.0) or getattr(model_execution, "confidence", 0.0) or 0.68),
            ),
        )
        self._update_task_result(
            task.task_id,
            outcome="success" if model_final_answer_hit else "degraded",
            confidence=confidence,
        )
        self._emit_chat_truth_metrics(
            task_id=task.task_id,
            reason=reason,
            response_text=decorated_response,
            response_class=turn_result.response_class.value,
            source_context=source_context,
            rendered_via="model_final_wording",
            fast_path_hit=False,
            model_inference_used=bool(getattr(model_execution, "used_model", False)),
            model_final_answer_hit=model_final_answer_hit,
            model_execution_source=str(getattr(model_execution, "source", "") or ""),
            tool_backing_sources=list(tool_backing_sources or []),
        )
        self._emit_runtime_event(
            source_context,
            event_type="task_completed",
            message=f"Model-worded response ready: {self._runtime_preview(decorated_response)}",
            task_id=task.task_id,
            status=reason,
        )
        self._finalize_runtime_checkpoint(
            source_context,
            status="completed",
            final_response=decorated_response,
        )
        return {
            "task_id": task.task_id,
            "response": str(decorated_response or ""),
            "mode": "advice_only",
            "confidence": float(confidence),
            "understanding_confidence": float(getattr(interpretation, "understanding_confidence", 1.0) or 1.0),
            "interpreted_input": user_input,
            "topic_hints": list(getattr(interpretation, "topic_hints", []) or []),
            "prompt_assembly_report": context_result.report.to_dict(),
            "model_execution": {
                "source": getattr(model_execution, "source", ""),
                "provider_id": getattr(model_execution, "provider_id", None),
                "used_model": bool(getattr(model_execution, "used_model", False)),
                "cache_hit": bool(getattr(model_execution, "cache_hit", False)),
                "validation_state": getattr(model_execution, "validation_state", "not_run"),
            },
            "media_analysis": {"used_provider": False, "reason": "not_run"},
            "curiosity": {"mode": "skipped", "reason": "chat_surface_model_wording"},
            "backend": self.backend_name,
            "device": self.device,
            "session_id": session_id,
            "source_context": dict(source_context or {}),
            "workflow_summary": "",
            "response_class": turn_result.response_class.value,
        }

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
        report = capability_truth_for_request(
            user_input,
            extra_entries=self._capability_ledger_entries(),
        )
        if not report:
            return None
        return self._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=render_capability_truth_response(report),
            confidence=0.96,
            source_context=source_context,
            reason="capability_truth_query",
        )

    def _help_capabilities_text(self) -> str:
        lines = [
            "Wired on this runtime:",
            "- plain-language reasoning, persistent memory, and rolling chat continuity",
        ]
        supported_entries = [entry for entry in self._capability_ledger_entries() if entry.get("supported")]
        partial_entries = [
            entry
            for entry in supported_entries
            if str(entry.get("support_level") or "").strip().lower() == "partial"
        ]
        full_entries = [
            entry
            for entry in supported_entries
            if str(entry.get("support_level") or "").strip().lower() != "partial"
        ]
        unsupported_entries = [entry for entry in self._capability_ledger_entries() if not entry.get("supported")]
        for entry in full_entries:
            lines.append(f"- {str(entry.get('claim') or '').strip()}")
        if partial_entries:
            lines.append("")
            lines.append("Partially supported on this runtime:")
            for entry in partial_entries:
                claim = str(entry.get("claim") or "").strip()
                note = str(entry.get("partial_reason") or "").strip()
                lines.append(f"- {claim}" + (f" ({note})" if note else ""))
        lines.append("- I report real tool executions, approval previews, and failures directly instead of bluffing")
        if unsupported_entries:
            lines.append("")
            lines.append("Not wired or not enabled here:")
            for entry in unsupported_entries:
                reason = str(entry.get("unsupported_reason") or entry.get("claim") or "").strip()
                if reason:
                    lines.append(f"- {reason}")
        return "\n".join(lines)

    def _render_credit_status(self, normalized_input: str) -> str:
        from core.credit_ledger import reconcile_ledger
        from core.dna_wallet_manager import DNAWalletManager
        from core.scoreboard_engine import get_peer_scoreboard

        peer_id = signer_mod.get_local_peer_id()
        ledger = reconcile_ledger(peer_id)
        scoreboard = get_peer_scoreboard(peer_id)
        wallet_status = DNAWalletManager().get_status()
        mention_wallet = any(token in normalized_input for token in ("wallet", "usdc", "dna"))
        mention_rewards = any(token in normalized_input for token in ("earn", "earned", "reward", "share", "hive", "task"))
        mention_receipts = any(token in normalized_input for token in ("receipt", "receipts", "ledger", "payout", "payouts"))
        provider_score = float(getattr(scoreboard, "provider", 0.0) or 0.0)
        validator_score = float(getattr(scoreboard, "validator", 0.0) or 0.0)
        trust_score = float(getattr(scoreboard, "trust", 0.0) or 0.0)
        glory_score = float(getattr(scoreboard, "glory_score", 0.0) or 0.0)
        tier_label = str(getattr(scoreboard, "tier", "Newcomer") or "Newcomer")

        parts = [
            f"You currently have {ledger.balance:.2f} compute credits.",
            (
                f"Provider score {provider_score:.1f}, validator score {validator_score:.1f}, "
                f"trust {trust_score:.1f}, glory score {glory_score:.1f}, tier {tier_label}."
            ),
        ]
        if wallet_status is None:
            if mention_wallet:
                parts.append("DNA wallet is not configured on this runtime yet.")
        else:
            parts.append(
                f"DNA wallet: hot {wallet_status.hot_balance_usdc:.2f} USDC, cold {wallet_status.cold_balance_usdc:.2f} USDC."
            )
        if mention_rewards or "credit" in normalized_input:
            parts.append(
                "Plain public Hive posts do not mint credits by themselves. Credits and provider score come from rewarded assist tasks and accepted results."
            )
        if mention_receipts:
            entries = list_credit_ledger_entries(peer_id, limit=4)
            if not entries:
                parts.append("No credit receipts are recorded for this peer yet.")
            else:
                receipt_lines = ["Recent credit receipts:"]
                for entry in entries:
                    amount = float(entry.get("amount") or 0.0)
                    sign = "+" if amount >= 0 else ""
                    receipt_id = str(entry.get("receipt_id") or "").strip() or "no-receipt"
                    reason = str(entry.get("reason") or "").strip() or "unknown"
                    timestamp = str(entry.get("timestamp") or "").strip() or "unknown time"
                    receipt_lines.append(
                        f"- {sign}{amount:.2f} for `{reason}` ({receipt_id[:24]}) at {timestamp}."
                    )
                parts.append("\n".join(receipt_lines))
        if ledger.mode:
            parts.append(f"Ledger mode is {ledger.mode}.")
        return " ".join(part.strip() for part in parts if part.strip())

    def _collect_live_web_notes(
        self,
        *,
        task_id: str,
        query_text: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
    ) -> list[dict[str, Any]]:
        if not policy_engine.allow_web_fallback():
            return []
        source_context = dict(source_context or {})
        surface = str(source_context.get("surface", "") or "").lower()
        platform = str(source_context.get("platform", "") or "").lower()
        allow_remote_fetch = bool(source_context.get("allow_remote_fetch", False))
        trusted_live_surface = (
            surface in {"channel", "openclaw", "api"}
            or platform in {"openclaw", "web_companion", "telegram", "discord"}
        )
        if not (allow_remote_fetch or trusted_live_surface):
            return []

        task_class = str(classification.get("task_class", "unknown"))
        wants_live_lookup = task_class in {"research", "system_design", "integration_orchestration"}
        if not wants_live_lookup and not self._wants_fresh_info(query_text, interpretation=interpretation):
            return []
        try:
            if wants_live_lookup:
                notes = WebAdapter.planned_search_query(
                    query_text,
                    task_id=task_id,
                    limit=3,
                    task_class=task_class,
                    topic_hints=list(getattr(interpretation, "topic_hints", []) or []),
                    source_label="duckduckgo.com",
                )
                if notes:
                    return notes
            return WebAdapter.search_query(
                query_text,
                task_id=task_id,
                limit=3,
                source_label="duckduckgo.com",
            )
        except Exception as exc:
            audit_logger.log(
                "agent_live_web_lookup_error",
                target_id=task_id,
                target_type="task",
                details={"error": str(exc)},
            )
            return []

    def _collect_adaptive_research(
        self,
        *,
        task_id: str,
        query_text: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
    ) -> AdaptiveResearchResult:
        try:
            return self.curiosity.adaptive_research(
                task_id=task_id,
                user_input=query_text,
                classification=classification,
                interpretation=interpretation,
                source_context=dict(source_context or {}),
            )
        except Exception as exc:
            audit_logger.log(
                "adaptive_research_error",
                target_id=task_id,
                target_type="task",
                details={"error": str(exc)},
            )
            return AdaptiveResearchResult(
                enabled=False,
                reason="controller_error",
                strategy="tool_gap",
                tool_gap_note="Adaptive research failed for this turn, so I should stay cautious about unsupported claims.",
                admitted_uncertainty=True,
                uncertainty_reason="Adaptive research failed for this turn.",
            )

    def _should_frontload_curiosity(
        self,
        *,
        query_text: str,
        classification: dict[str, Any],
        interpretation: Any,
    ) -> bool:
        task_class = str(classification.get("task_class", "unknown"))
        if task_class in {"research", "system_design"}:
            return True
        if task_class != "integration_orchestration":
            return False
        lowered = str(query_text or "").lower()
        if any(
            marker in lowered
            for marker in (
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
            )
        ):
            return True
        topic_hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
        return bool({"telegram bot", "discord bot"} & topic_hints)

    def _curiosity_candidate_evidence(self, candidate_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        plan_candidates: list[dict[str, Any]] = []
        context_snippets: list[dict[str, Any]] = []
        for candidate_id in list(candidate_ids or [])[:3]:
            candidate = get_candidate_by_id(candidate_id)
            if not candidate:
                continue
            structured = dict(candidate.get("structured_output") or {})
            metadata = dict(candidate.get("metadata") or {})
            snippets = [dict(item) for item in list(structured.get("snippets") or []) if isinstance(item, dict)]
            topic = str(structured.get("topic") or metadata.get("curiosity_topic") or "technical research").strip()
            topic_kind = str(structured.get("topic_kind") or "technical").strip().lower() or "technical"
            score = self._curiosity_candidate_score(candidate=candidate, snippets=snippets)
            summary = self._curiosity_candidate_summary(
                topic=topic,
                topic_kind=topic_kind,
                snippets=snippets,
                fallback_text=str(candidate.get("normalized_output") or candidate.get("raw_output") or ""),
            )
            plan_candidates.append(
                {
                    "summary": summary,
                    "resolution_pattern": self._curiosity_candidate_steps(topic_kind=topic_kind, snippets=snippets),
                    "score": score,
                    "source_type": "curiosity_candidate",
                    "source_node_id": "curiosity_roamer",
                    "provider_name": "curiosity_roamer",
                    "model_name": str(candidate.get("model_name") or "bounded_web_research"),
                    "candidate_id": candidate_id,
                }
            )
            for index, snippet in enumerate(snippets[:4], start=1):
                snippet_summary = " ".join(str(snippet.get("summary") or "").split()).strip()
                if not snippet_summary:
                    continue
                label = str(
                    snippet.get("source_profile_label")
                    or snippet.get("origin_domain")
                    or snippet.get("source_label")
                    or "curated source"
                ).strip()
                context_snippets.append(
                    {
                        "title": f"{label} note {index}",
                        "source_type": "curiosity_research",
                        "summary": snippet_summary[:320],
                        "confidence": score,
                        "priority": score,
                        "metadata": {
                            "origin_domain": snippet.get("origin_domain"),
                            "result_url": snippet.get("result_url"),
                            "source_profile_id": snippet.get("source_profile_id"),
                            "created_at": candidate.get("created_at"),
                            "candidate_id": candidate_id,
                        },
                    }
                )
        return plan_candidates, context_snippets

    def _curiosity_candidate_summary(
        self,
        *,
        topic: str,
        topic_kind: str,
        snippets: list[dict[str, Any]],
        fallback_text: str,
    ) -> str:
        clean_topic = " ".join(str(topic or "").split()).strip() or "this topic"
        labels = {
            str(snippet.get("source_profile_label") or snippet.get("source_profile_id") or "").strip().lower()
            for snippet in snippets
        }
        domains = {
            str(snippet.get("origin_domain") or "").strip().lower()
            for snippet in snippets
            if str(snippet.get("origin_domain") or "").strip()
        }
        official_docs = bool({"official docs", "messaging platform docs"} & labels) or bool(
            domains & {"core.telegram.org", "discord.com", "docs.python.org", "developer.mozilla.org"}
        )
        repo_examples = "reputable repositories" in labels or "github.com" in domains

        lead = f"Research brief for {clean_topic}:"
        if topic_kind in {"technical", "integration"} and official_docs and repo_examples:
            lead = f"For {clean_topic}, start with official docs first and use reputable GitHub repos as implementation references."
        elif official_docs:
            lead = f"For {clean_topic}, anchor the answer on official documentation before applying examples."
        elif repo_examples:
            lead = f"For {clean_topic}, compare a few reputable GitHub implementations before locking the design."

        highlights = [
            " ".join(str(snippet.get("summary") or "").split()).strip().rstrip(".")
            for snippet in snippets[:2]
            if str(snippet.get("summary") or "").strip()
        ]
        if highlights:
            return f"{lead} {' '.join(highlights)}"[:420]
        clean_fallback = " ".join(str(fallback_text or "").split()).strip()
        if clean_fallback:
            return f"{lead} {clean_fallback}"[:420]
        return lead[:420]

    def _curiosity_candidate_steps(self, *, topic_kind: str, snippets: list[dict[str, Any]]) -> list[str]:
        labels = {
            str(snippet.get("source_profile_label") or snippet.get("source_profile_id") or "").strip().lower()
            for snippet in snippets
        }
        domains = {
            str(snippet.get("origin_domain") or "").strip().lower()
            for snippet in snippets
            if str(snippet.get("origin_domain") or "").strip()
        }
        steps: list[str] = []
        if {"official docs", "messaging platform docs"} & labels or domains & {"core.telegram.org", "discord.com"}:
            steps.append("review_official_platform_docs")
        if "github.com" in domains or "reputable repositories" in labels:
            steps.append("compare_reputable_repo_examples")
        if topic_kind in {"technical", "integration"}:
            steps.extend(["define_minimal_architecture", "validate_auth_limits_and_deployment_constraints"])
        elif topic_kind == "design":
            steps.extend(["compare_reference_patterns", "shape_minimal_user_flow"])
        elif topic_kind == "news":
            steps.extend(["compare_multiple_reputable_sources", "separate_verified_facts_from_speculation"])
        if not steps:
            steps.append("summarize_grounded_findings")
        deduped: list[str] = []
        seen: set[str] = set()
        for step in steps:
            if step in seen:
                continue
            seen.add(step)
            deduped.append(step)
        return deduped[:4]

    def _curiosity_candidate_score(self, *, candidate: dict[str, Any], snippets: list[dict[str, Any]]) -> float:
        score = float(candidate.get("trust_score") or candidate.get("confidence") or 0.0)
        labels = {
            str(snippet.get("source_profile_label") or snippet.get("source_profile_id") or "").strip().lower()
            for snippet in snippets
        }
        domains = {
            str(snippet.get("origin_domain") or "").strip().lower()
            for snippet in snippets
            if str(snippet.get("origin_domain") or "").strip()
        }
        if {"official docs", "messaging platform docs"} & labels or domains & {"core.telegram.org", "discord.com"}:
            score += 0.08
        if "github.com" in domains or "reputable repositories" in labels:
            score += 0.05
        if len(domains) >= 2:
            score += 0.03
        return max(0.50, min(0.90, score))

    def _web_note_plan_candidates(
        self,
        *,
        query_text: str,
        classification: dict[str, Any],
        web_notes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        notes = [dict(note) for note in list(web_notes or []) if isinstance(note, dict)]
        if not notes:
            return []
        labels = {
            str(note.get("source_profile_label") or note.get("source_profile_id") or "").strip().lower()
            for note in notes
        }
        domains = {
            str(note.get("origin_domain") or "").strip().lower()
            for note in notes
            if str(note.get("origin_domain") or "").strip()
        }
        official_docs = bool({"official docs", "messaging platform docs"} & labels) or bool(
            domains & {"core.telegram.org", "discord.com", "docs.python.org", "developer.mozilla.org"}
        )
        repo_examples = "reputable repositories" in labels or "github.com" in domains
        topic = " ".join(str(query_text or "").split()).strip() or str(classification.get("task_class") or "research")
        lead = f"Research notes for {topic}:"
        if official_docs and repo_examples:
            lead = f"For {topic}, anchor the design on official docs first, then use reputable GitHub repos as implementation references."
        elif official_docs:
            lead = f"For {topic}, anchor the answer on official documentation."
        elif repo_examples:
            lead = f"For {topic}, compare reputable GitHub implementations before locking the design."
        highlights = [
            " ".join(str(note.get("summary") or "").split()).strip().rstrip(".")
            for note in notes[:2]
            if str(note.get("summary") or "").strip()
        ]
        steps: list[str] = []
        if official_docs:
            steps.append("review_official_docs")
        if repo_examples:
            steps.append("compare_reputable_repo_examples")
        if str(classification.get("task_class") or "") in {"system_design", "integration_orchestration"}:
            steps.extend(["define_minimal_architecture", "validate_runtime_constraints"])
        elif str(classification.get("task_class") or "") == "research":
            steps.extend(["compare_findings", "summarize_grounded_recommendation"])
        score = max(float(note.get("confidence") or 0.0) for note in notes)
        if official_docs:
            score += 0.08
        if repo_examples:
            score += 0.05
        summary = lead if not highlights else f"{lead} {' '.join(highlights)}"
        deduped_steps: list[str] = []
        seen_steps: set[str] = set()
        for step in steps:
            if step in seen_steps:
                continue
            seen_steps.add(step)
            deduped_steps.append(step)
        return [
            {
                "summary": summary[:420],
                "resolution_pattern": deduped_steps[:4] or ["summarize_grounded_findings"],
                "score": max(0.45, min(0.86, score)),
                "source_type": "planned_web_candidate",
                "source_node_id": "web_source_planner",
                "provider_name": "web_source_planner",
                "model_name": "source_ranked_web_notes",
            }
        ]

    def _workspace_build_observations(
        self,
        *,
        target: dict[str, str],
        write_results: list[dict[str, Any]],
        write_failures: list[str],
        verification: dict[str, Any] | None,
        sources: list[dict[str, str]],
    ) -> dict[str, Any]:
        return agent_builder_controller.workspace_build_observations(
            target=target,
            write_results=write_results,
            write_failures=write_failures,
            verification=verification,
            sources=sources,
        )

    def _workspace_build_degraded_response(
        self,
        *,
        target: dict[str, str],
        write_results: list[dict[str, Any]],
        write_failures: list[str],
        verification: dict[str, Any] | None,
    ) -> str:
        return agent_builder_controller.workspace_build_degraded_response(
            target=target,
            write_results=write_results,
            write_failures=write_failures,
            verification=verification,
        )

    def _builder_support_gap_report(
        self,
        *,
        source_context: dict[str, object] | None,
        reason: str,
    ) -> dict[str, Any]:
        return agent_builder_support.support_gap_report(
            source_context=source_context,
            reason=reason,
            write_enabled=bool(policy_engine.get("filesystem.allow_write_workspace", False)),
        )

    def _builder_controller_profile(
        self,
        *,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_builder_support.controller_profile(
            self,
            effective_input=effective_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
            plan_tool_workflow_fn=plan_tool_workflow,
            looks_like_workspace_bootstrap_request_fn=_looks_like_workspace_bootstrap_request,
        )

    def _supports_bounded_builder_workflow_request(
        self,
        *,
        effective_input: str,
        task_class: str,
        source_context: dict[str, object] | None = None,
    ) -> bool:
        if self._looks_like_explicit_workspace_file_request(effective_input):
            return True
        if _looks_like_workspace_bootstrap_request(effective_input):
            return True
        workflow_probe = plan_tool_workflow(
            user_text=effective_input,
            task_class=task_class,
            executed_steps=[],
            source_context=dict(source_context or {}),
        )
        workflow_intent = str(dict(workflow_probe.next_payload or {}).get("intent") or "").strip()
        if workflow_probe.handled and workflow_probe.next_payload and workflow_intent in {
            "workspace.search_text",
            "workspace.read_file",
            "workspace.write_file",
            "workspace.ensure_directory",
            "sandbox.run_command",
            "hive.create_topic",
        }:
            return True
        if not self._explicit_runtime_workflow_request(
            user_input=effective_input,
            task_class=task_class,
        ):
            return False
        lowered = f" {str(effective_input or '').lower()} "
        operation_markers = (
            " run ",
            " rerun ",
            " retry ",
            " inspect ",
            " search ",
            " find ",
            " read ",
            " open ",
            " replace ",
            " patch ",
            " edit ",
            " fix ",
            " debug ",
            " trace ",
            " diagnose ",
            " test ",
            " tests ",
        )
        target_markers = (
            " workspace ",
            " repo ",
            " repository ",
            " code ",
            " file ",
            " files ",
            ".py",
            ".ts",
            ".js",
            ".tsx",
            ".jsx",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".md",
            "`",
        )
        return any(marker in lowered for marker in operation_markers) and any(marker in lowered for marker in target_markers)

    def _builder_controller_step_record(
        self,
        *,
        execution: Any,
        tool_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return agent_builder_support.controller_step_record(
            self,
            execution=execution,
            tool_payload=tool_payload,
        )

    def _workspace_build_verification_payload(self, *, target: dict[str, str]) -> dict[str, Any] | None:
        return agent_builder_support.workspace_build_verification_payload(target=target)

    def _builder_initial_payloads(
        self,
        *,
        mode: str,
        target: dict[str, str],
        user_request: str,
        web_notes: list[dict[str, Any]],
        initial_payloads: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        return agent_builder_support.initial_payloads(
            self,
            mode=mode,
            target=target,
            user_request=user_request,
            web_notes=web_notes,
            initial_payloads=initial_payloads,
        )

    def _builder_controller_backing_sources(self, executed_steps: list[dict[str, Any]]) -> list[str]:
        return agent_builder_support.controller_backing_sources(executed_steps)

    def _builder_controller_observations(
        self,
        *,
        mode: str,
        target: dict[str, str],
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
        sources: list[dict[str, str]],
        final_status: str,
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        return agent_builder_support.controller_observations(
            mode=mode,
            target=target,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            sources=sources,
            final_status=final_status,
            artifacts=artifacts,
        )

    def _builder_retry_history(self, executed_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return agent_builder_support.retry_history(executed_steps)

    def _builder_controller_artifacts(
        self,
        *,
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
    ) -> dict[str, Any]:
        return agent_builder_support.controller_artifacts(
            executed_steps=executed_steps,
            stop_reason=stop_reason,
        )

    def _builder_artifact_citation_block(self, artifacts: dict[str, Any]) -> str:
        return agent_builder_support.artifact_citation_block(self, artifacts)

    def _append_builder_artifact_citations(self, text: str, *, artifacts: dict[str, Any]) -> str:
        return agent_builder_support.append_artifact_citations(
            self,
            text,
            artifacts=artifacts,
        )

    def _builder_controller_degraded_response(
        self,
        *,
        target: dict[str, str],
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
        failed_execution: Any | None,
        effective_input: str,
        session_id: str,
        artifacts: dict[str, Any],
    ) -> str:
        return agent_builder_support.controller_degraded_response(
            self,
            target=target,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            failed_execution=failed_execution,
            effective_input=effective_input,
            session_id=session_id,
        )

    def _builder_controller_direct_response(
        self,
        *,
        effective_input: str,
        executed_steps: list[dict[str, Any]],
    ) -> str | None:
        return agent_builder_support.controller_direct_response(
            self,
            effective_input=effective_input,
            executed_steps=executed_steps,
        )

    def _builder_controller_workflow_summary(
        self,
        *,
        mode: str,
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
        artifacts: dict[str, Any],
    ) -> str:
        return agent_builder_support.controller_workflow_summary(
            self,
            mode=mode,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            artifacts=artifacts,
        )

    def _run_bounded_builder_loop(
        self,
        *,
        task: Any,
        session_id: str,
        effective_input: str,
        task_class: str,
        source_context: dict[str, object] | None,
        initial_payloads: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any], str, Any | None]:
        return agent_builder_controller.run_bounded_builder_loop(
            self,
            task=task,
            session_id=session_id,
            effective_input=effective_input,
            task_class=task_class,
            source_context=source_context,
            initial_payloads=initial_payloads,
            plan_tool_workflow_fn=plan_tool_workflow,
            execute_tool_intent_fn=execute_tool_intent,
        )

    def _maybe_run_builder_controller(
        self,
        *,
        task: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        web_notes: list[dict[str, Any]],
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_builder_controller.maybe_run_builder_controller(
            self,
            task=task,
            effective_input=effective_input,
            classification=classification,
            interpretation=interpretation,
            web_notes=web_notes,
            session_id=session_id,
            source_context=source_context,
            render_capability_truth_response_fn=render_capability_truth_response,
            load_active_persona_fn=load_active_persona,
        )

    def _should_run_builder_controller(
        self,
        *,
        effective_input: str,
        classification: dict[str, Any],
        source_context: dict[str, object],
    ) -> bool:
        if not policy_engine.get("filesystem.allow_write_workspace", False):
            return False
        if not str(source_context.get("workspace") or source_context.get("workspace_root") or "").strip():
            return False
        task_class = str(classification.get("task_class") or "unknown")
        lowered = str(effective_input or "").lower()
        explicit_file_request = self._looks_like_explicit_workspace_file_request(effective_input)
        generic_bootstrap_request = self._looks_like_generic_workspace_bootstrap_request(lowered)
        workflow_probe = plan_tool_workflow(
            user_text=effective_input,
            task_class=task_class,
            executed_steps=[],
            source_context=dict(source_context or {}),
        )
        workflow_intent = str(dict(workflow_probe.next_payload or {}).get("intent") or "").strip()
        workflow_supported_request = bool(
            workflow_probe.handled
            and workflow_probe.next_payload
            and workflow_intent
            in {
                "workspace.search_text",
                "workspace.read_file",
                "workspace.write_file",
                "workspace.ensure_directory",
                "sandbox.run_command",
                "hive.create_topic",
            }
        )
        if task_class not in {
            "system_design",
            "integration_orchestration",
            "debugging",
            "dependency_resolution",
            "config",
            "file_inspection",
            "shell_guidance",
            "unknown",
        } and not generic_bootstrap_request and not explicit_file_request and not workflow_supported_request:
            return False
        if not self._looks_like_builder_request(lowered) and not explicit_file_request and not workflow_supported_request:
            return False
        if any(marker in lowered for marker in ("don't write", "do not write", "advice only", "just plan", "no files")):
            return False
        scaffold_request = (
            any(marker in lowered for marker in ("build", "create", "scaffold", "implement", "generate", "start working"))
            and any(marker in lowered for marker in ("telegram", "discord", "bot", "agent", "service"))
            and any(marker in lowered for marker in ("workspace", "repo", "repository", "write the files", "create the files", "generate the code"))
        )
        return (
            "write the files" in lowered
            or "create the files" in lowered
            or "generate the code" in lowered
            or "build the code" in lowered
            or "building the code" in lowered
            or "start working" in lowered
            or "start building" in lowered
            or "start creating" in lowered
            or "implement it" in lowered
            or "edit the files" in lowered
            or "patch the files" in lowered
            or "launch local" in lowered
            or scaffold_request
            or generic_bootstrap_request
            or explicit_file_request
            or workflow_supported_request
            or self._explicit_runtime_workflow_request(user_input=effective_input, task_class=task_class)
        )

    def _workspace_build_target(self, *, query_text: str, interpretation: Any) -> dict[str, str]:
        return agent_builder_scaffolds.workspace_build_target(
            query_text=query_text,
            interpretation=interpretation,
            extract_requested_builder_root_fn=self._extract_requested_builder_root,
            search_user_heuristics_fn=search_user_heuristics,
        )

    def _workspace_build_file_map(
        self,
        *,
        target: dict[str, str],
        user_request: str,
        web_notes: list[dict[str, Any]],
    ) -> dict[str, str]:
        return agent_builder_scaffolds.workspace_build_file_map(
            target=target,
            user_request=user_request,
            web_notes=web_notes,
        )

    def _workspace_build_sources(self, web_notes: list[dict[str, Any]]) -> list[dict[str, str]]:
        return agent_builder_scaffolds.workspace_build_sources(web_notes)

    def _workspace_build_verification(
        self,
        *,
        target: dict[str, str],
        source_context: dict[str, object],
    ) -> dict[str, Any] | None:
        return agent_builder_controller.workspace_build_verification(
            target=target,
            source_context=source_context,
            execute_runtime_tool_fn=execute_runtime_tool,
        )

    def _workspace_build_response(
        self,
        *,
        target: dict[str, str],
        write_results: list[dict[str, Any]],
        write_failures: list[str],
        verification: dict[str, Any] | None,
        sources: list[dict[str, str]],
    ) -> str:
        return agent_builder_controller.workspace_build_response(
            target=target,
            write_results=write_results,
            write_failures=write_failures,
            verification=verification,
            sources=sources,
        )

    def _sources_section(self, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.sources_section(sources)

    def _generic_workspace_readme(
        self,
        *,
        user_request: str,
        root_dir: str,
        sources: list[dict[str, str]],
        language: str,
    ) -> str:
        return agent_builder_scaffolds.generic_workspace_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
            language=language,
        )

    def _generic_python_source(self, *, user_request: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.generic_python_source(
            user_request=user_request,
            sources=sources,
        )

    def _generic_typescript_package_json(self, *, root_dir: str) -> str:
        return agent_builder_scaffolds.generic_typescript_package_json(root_dir=root_dir)

    def _generic_typescript_source(self, *, user_request: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.generic_typescript_source(
            user_request=user_request,
            sources=sources,
        )

    def _telegram_python_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_python_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _telegram_python_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_python_bot_source(sources=sources)

    def _telegram_typescript_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_typescript_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _telegram_typescript_package_json(self) -> str:
        return agent_builder_scaffolds.telegram_typescript_package_json()

    def _telegram_typescript_tsconfig(self) -> str:
        return agent_builder_scaffolds.telegram_typescript_tsconfig()

    def _telegram_typescript_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_typescript_bot_source(sources=sources)

    def _discord_python_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_python_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _discord_python_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_python_bot_source(sources=sources)

    def _discord_typescript_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_typescript_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _discord_typescript_package_json(self) -> str:
        return agent_builder_scaffolds.discord_typescript_package_json()

    def _discord_typescript_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_typescript_bot_source(sources=sources)

    def _generic_build_brief(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.generic_build_brief(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _maybe_execute_model_tool_intent(
        self,
        *,
        task: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        session_id: str,
        source_context: dict[str, object] | None,
        surface: str,
    ) -> dict[str, Any] | None:
        checkpoint_id = self._runtime_checkpoint_id(source_context)
        checkpoint = get_runtime_checkpoint(checkpoint_id) if checkpoint_id else None
        checkpoint_state = dict((checkpoint or {}).get("state") or {})
        if self._should_keep_ai_first_chat_lane(
            user_input=effective_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
            checkpoint_state=checkpoint_state,
        ):
            return None
        if self._should_run_builder_controller(
            effective_input=effective_input,
            classification=classification,
            source_context=dict(source_context or {}),
        ):
            return None
        if not should_attempt_tool_intent(
            effective_input,
            task_class=str(classification.get("task_class", "unknown")),
            source_context=source_context,
        ):
            return None
        loop_source_context = self._merge_runtime_source_contexts(
            dict(checkpoint_state.get("loop_source_context") or {}),
            dict(source_context or {}),
        )
        executed_steps: list[dict[str, Any]] = []
        last_tool_decision = None
        seen_tool_payloads: set[str] = set()
        pending_tool_payload: dict[str, Any] | None = None
        if checkpoint_state:
            executed_steps = [dict(step) for step in list(checkpoint_state.get("executed_steps") or []) if isinstance(step, dict)]
            seen_tool_payloads = {
                str(item)
                for item in list(checkpoint_state.get("seen_tool_payloads") or [])
                if str(item).strip()
            }
            saved_pending = checkpoint_state.get("pending_tool_payload") or (checkpoint or {}).get("pending_intent") or {}
            if isinstance(saved_pending, dict) and saved_pending:
                pending_tool_payload = dict(saved_pending)
        if checkpoint and (executed_steps or pending_tool_payload):
            self._emit_runtime_event(
                loop_source_context,
                event_type="tool_loop_resumed",
                message=(
                    f"Resuming tool loop from {len(executed_steps)} completed step"
                    f"{'' if len(executed_steps) == 1 else 's'}."
                ),
                step_count=len(executed_steps),
            )
        max_steps = 5

        while len(executed_steps) < max_steps:
            tool_decision = None
            tool_payload: dict[str, Any] = {}
            provider_id = None
            validation_state = "not_run"
            confidence_hint = 0.55

            if pending_tool_payload:
                tool_payload = dict(pending_tool_payload)
                pending_tool_payload = None
                tool_name = str(tool_payload.get("intent") or "").strip()
                self._emit_runtime_event(
                    loop_source_context,
                    event_type="tool_selected" if tool_name else "tool_failed",
                    message=(
                        f"Resuming pending tool {tool_name}."
                        if tool_name
                        else "Resuming invalid pending tool payload with no intent name."
                    ),
                    tool_name=tool_name or "unknown",
                )
            else:
                workflow_decision = plan_tool_workflow(
                    user_text=effective_input,
                    task_class=str(classification.get("task_class") or "unknown"),
                    executed_steps=executed_steps,
                    source_context=loop_source_context,
                )
                if workflow_decision.handled and workflow_decision.stop_after:
                    self._emit_runtime_event(
                        loop_source_context,
                        event_type="workflow_planner_stop",
                        message="Workflow planner gathered enough state and stopped before another tool step.",
                        status=workflow_decision.reason,
                        step_count=len(executed_steps),
                    )
                    break
                if workflow_decision.handled and workflow_decision.next_payload:
                    tool_payload = dict(workflow_decision.next_payload)
                    tool_name = str(tool_payload.get("intent") or "").strip()
                    self._emit_runtime_event(
                        loop_source_context,
                        event_type="workflow_planner_step",
                        message=f"Workflow planner selected {tool_name}.",
                        tool_name=tool_name or "unknown",
                        status=workflow_decision.reason,
                    )
                else:
                    tool_decision = self.memory_router.resolve_tool_intent(
                        task=task,
                        classification=classification,
                        interpretation=interpretation,
                        context_result=context_result,
                        persona=persona,
                        surface=surface,
                        source_context=loop_source_context,
                    )
                    last_tool_decision = tool_decision
                    direct_message = self._tool_intent_direct_message(tool_decision.structured_output)
                    if direct_message is not None:
                        self._emit_runtime_event(
                            loop_source_context,
                            event_type="tool_loop_completed",
                            message=(
                                f"Returning grounded reply after {len(executed_steps)} real tool step"
                                f"{'' if len(executed_steps) == 1 else 's'}."
                            ),
                            step_count=len(executed_steps),
                        )
                        confidence = max(0.35, min(0.96, float(tool_decision.trust_score or tool_decision.confidence or 0.55)))
                        return {
                            "response": self._render_tool_loop_response(
                                final_message=direct_message,
                                executed_steps=executed_steps,
                                include_step_summary=not self._live_runtime_stream_enabled(loop_source_context),
                            ),
                            "confidence": confidence,
                            "success": True,
                            "status": "direct_response_after_tools" if executed_steps else "direct_response",
                            "mode": "tool_executed" if executed_steps else "advice_only",
                            "task_outcome": "success",
                            "details": {
                                "tool_name": "respond.direct",
                                "tool_provider": tool_decision.provider_id,
                                "tool_validation": tool_decision.validation_state,
                                "tool_steps": [step["tool_name"] for step in executed_steps],
                            },
                            "learned_plan": None,
                            "workflow_summary": self._tool_intent_loop_workflow_summary(
                                executed_steps=executed_steps,
                                provider_id=tool_decision.provider_id,
                                validation_state=tool_decision.validation_state,
                            ),
                        }
                    try:
                        payload_signature = json.dumps(tool_decision.structured_output, sort_keys=True, ensure_ascii=True, default=str)
                    except Exception:
                        payload_signature = str(tool_decision.structured_output)
                    if payload_signature in seen_tool_payloads:
                        self._emit_runtime_event(
                            loop_source_context,
                            event_type="tool_repeat_blocked",
                            message="Repeated tool request detected. Switching to grounded synthesis instead of looping.",
                        )
                        if checkpoint_id:
                            record_runtime_tool_progress(
                                checkpoint_id,
                                executed_steps=executed_steps,
                                loop_source_context=loop_source_context,
                                seen_tool_payloads=seen_tool_payloads,
                                pending_tool_payload=None,
                                last_tool_payload=checkpoint_state.get("last_tool_payload"),
                                last_tool_response=checkpoint_state.get("last_tool_response"),
                                last_tool_name=str((executed_steps[-1] if executed_steps else {}).get("tool_name") or ""),
                                task_class=str(classification.get("task_class") or "unknown"),
                                status="running",
                            )
                        break
                    seen_tool_payloads.add(payload_signature)
                    tool_payload = dict(tool_decision.structured_output or {})
                    tool_name = str(tool_payload.get("intent") or "").strip()
                    provider_id = tool_decision.provider_id
                    validation_state = tool_decision.validation_state
                    confidence_hint = float(tool_decision.trust_score or tool_decision.confidence or 0.55)
                    self._emit_runtime_event(
                        loop_source_context,
                        event_type="tool_selected" if tool_name else "tool_failed",
                        message=(
                            f"Running real tool {tool_name}."
                            if tool_name
                            else "Model returned an invalid tool payload with no intent name."
                        ),
                        tool_name=tool_name or "unknown",
                    )

            tool_name = str(tool_payload.get("intent") or "").strip() or "unknown"
            if checkpoint_id:
                record_runtime_tool_progress(
                    checkpoint_id,
                    executed_steps=executed_steps,
                    loop_source_context=loop_source_context,
                    seen_tool_payloads=seen_tool_payloads,
                    pending_tool_payload=tool_payload,
                    last_tool_payload=checkpoint_state.get("last_tool_payload"),
                    last_tool_response=checkpoint_state.get("last_tool_response"),
                    last_tool_name=tool_name,
                    task_class=str(classification.get("task_class") or "unknown"),
                    status="running",
                )

            execution = execute_tool_intent(
                tool_payload,
                task_id=task.task_id,
                session_id=session_id,
                source_context=loop_source_context,
                hive_activity_tracker=self.hive_activity_tracker,
                public_hive_bridge=self.public_hive_bridge,
                checkpoint_id=checkpoint_id,
                step_index=len(executed_steps),
            )
            if not execution.handled:
                break
            if self._should_fallback_after_tool_failure(
                execution=execution,
                effective_input=effective_input,
                classification=classification,
                interpretation=interpretation,
                executed_steps=executed_steps,
            ):
                self._emit_runtime_event(
                    loop_source_context,
                    event_type="tool_fallback_to_research",
                    message="Tool-intent failed before any real tool ran. Continuing with grounded research instead of returning a tooling error.",
                    tool_name=execution.tool_name or tool_name,
                    status=str(execution.status or "failed"),
                )
                checkpoint_state["last_tool_payload"] = dict(tool_payload)
                checkpoint_state["last_tool_response"] = {
                    "handled": bool(execution.handled),
                    "ok": bool(execution.ok),
                    "status": str(execution.status or ""),
                    "response_text": str(execution.response_text or ""),
                    "mode": str(execution.mode or ""),
                    "tool_name": str(execution.tool_name or tool_name),
                    "details": dict(execution.details or {}),
                }
                if checkpoint_id:
                    record_runtime_tool_progress(
                        checkpoint_id,
                        executed_steps=executed_steps,
                        loop_source_context=loop_source_context,
                        seen_tool_payloads=seen_tool_payloads,
                        pending_tool_payload=None,
                        last_tool_payload=checkpoint_state.get("last_tool_payload"),
                        last_tool_response=checkpoint_state.get("last_tool_response"),
                        last_tool_name=str(execution.tool_name or tool_name),
                        task_class=str(classification.get("task_class") or "unknown"),
                        status="running",
                    )
                return None

            executed_steps.append(
                {
                    "tool_name": execution.tool_name or tool_name,
                    "status": str(execution.status or "executed"),
                    "mode": execution.mode,
                    "arguments": dict(tool_payload.get("arguments") or {}),
                    "observation": dict((execution.details or {}).get("observation") or {}),
                    "details": dict(execution.details or {}),
                    "summary": self._tool_step_summary(execution.response_text, fallback=str(execution.status or "executed")),
                }
            )
            step_summary = str(executed_steps[-1]["summary"] or "").strip()
            self._emit_runtime_event(
                loop_source_context,
                event_type=str(execution.mode or "tool_failed"),
                message=(
                    f"{'Finished' if execution.mode == 'tool_executed' else 'Approval required for' if execution.mode == 'tool_preview' else 'Tool failed:'} "
                    f"{execution.tool_name or tool_name}. {step_summary}"
                ),
                tool_name=execution.tool_name or tool_name,
                status=str(execution.status or "executed"),
                mode=execution.mode,
            )
            loop_source_context = self._append_tool_result_to_source_context(
                loop_source_context,
                execution=execution,
                tool_name=execution.tool_name or tool_name,
            )
            checkpoint_state["last_tool_payload"] = dict(tool_payload)
            checkpoint_state["last_tool_response"] = {
                "handled": bool(execution.handled),
                "ok": bool(execution.ok),
                "status": str(execution.status or ""),
                "response_text": str(execution.response_text or ""),
                "mode": str(execution.mode or ""),
                "tool_name": str(execution.tool_name or tool_name),
                "details": dict(execution.details or {}),
            }
            if checkpoint_id:
                record_runtime_tool_progress(
                    checkpoint_id,
                    executed_steps=executed_steps,
                    loop_source_context=loop_source_context,
                    seen_tool_payloads=seen_tool_payloads,
                    pending_tool_payload=None,
                    last_tool_payload=checkpoint_state.get("last_tool_payload"),
                    last_tool_response=checkpoint_state.get("last_tool_response"),
                    last_tool_name=str(execution.tool_name or tool_name),
                    task_class=str(classification.get("task_class") or "unknown"),
                    status=(
                        "pending_approval"
                        if execution.mode == "tool_preview"
                        else "failed"
                        if execution.mode == "tool_failed"
                        else "running"
                    ),
                )
            if execution.mode != "tool_executed":
                confidence = max(0.35, min(0.96, confidence_hint))
                task_outcome = "pending_approval" if execution.mode == "tool_preview" else "failed"
                safe_response = self._tool_failure_user_message(
                    execution=execution,
                    effective_input=effective_input,
                    session_id=session_id,
                )
                return {
                    "response": self._render_tool_loop_response(
                        final_message=safe_response,
                        executed_steps=executed_steps,
                        include_step_summary=not self._live_runtime_stream_enabled(loop_source_context),
                    ),
                    "confidence": confidence,
                    "success": bool(execution.ok),
                    "status": str(execution.status or "executed"),
                    "mode": execution.mode,
                    "task_outcome": task_outcome,
                    "details": {
                        "tool_name": execution.tool_name,
                        "tool_provider": provider_id,
                        "tool_validation": validation_state,
                        "tool_steps": [step["tool_name"] for step in executed_steps],
                        **dict(execution.details or {}),
                    },
                    "learned_plan": execution.learned_plan,
                    "workflow_summary": self._tool_intent_loop_workflow_summary(
                        executed_steps=executed_steps,
                        provider_id=provider_id,
                        validation_state=validation_state,
                    ),
                }

        if not executed_steps:
            return None

        self._emit_runtime_event(
            loop_source_context,
            event_type="tool_synthesizing",
            message="Synthesizing final reply from real tool results.",
            step_count=len(executed_steps),
        )
        if checkpoint_id:
            record_runtime_tool_progress(
                checkpoint_id,
                executed_steps=executed_steps,
                loop_source_context=loop_source_context,
                seen_tool_payloads=seen_tool_payloads,
                pending_tool_payload=None,
                last_tool_payload=checkpoint_state.get("last_tool_payload"),
                last_tool_response=checkpoint_state.get("last_tool_response"),
                last_tool_name=str(executed_steps[-1].get("tool_name") or ""),
                task_class=str(classification.get("task_class") or "unknown"),
                status="running",
            )
        synthesis = self.memory_router.resolve(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            force_model=True,
            surface=surface,
            source_context=loop_source_context,
        )
        final_message = self._tool_loop_final_message(synthesis, executed_steps)
        final_provider_id = synthesis.provider_id if synthesis.provider_id else (last_tool_decision.provider_id if last_tool_decision else None)
        final_validation = synthesis.validation_state if synthesis.validation_state != "not_run" else (
            last_tool_decision.validation_state if last_tool_decision else "not_run"
        )
        confidence = max(
            0.35,
            min(
                0.96,
                float(
                    synthesis.trust_score
                    or synthesis.confidence
                    or (last_tool_decision.trust_score if last_tool_decision else 0.55)
                    or 0.55
                ),
            ),
        )
        return {
            "response": self._render_tool_loop_response(
                final_message=final_message,
                executed_steps=executed_steps,
                include_step_summary=not self._live_runtime_stream_enabled(loop_source_context),
            ),
            "confidence": confidence,
            "success": True,
            "status": "multi_step_executed",
            "mode": "tool_executed",
            "task_outcome": "success",
            "details": {
                "tool_name": executed_steps[-1]["tool_name"],
                "tool_provider": final_provider_id,
                "tool_validation": final_validation,
                "tool_steps": [step["tool_name"] for step in executed_steps],
                "step_count": len(executed_steps),
            },
            "learned_plan": None,
            "workflow_summary": self._tool_intent_loop_workflow_summary(
                executed_steps=executed_steps,
                provider_id=final_provider_id,
                validation_state=final_validation,
            ),
        }

    def _should_fallback_after_tool_failure(
        self,
        *,
        execution: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        executed_steps: list[dict[str, Any]],
    ) -> bool:
        if bool(getattr(execution, "ok", False)):
            return False
        if str(getattr(execution, "mode", "") or "").strip().lower() != "tool_failed":
            return False
        if executed_steps:
            return False
        status = str(getattr(execution, "status", "") or "").strip().lower()
        tool_name = str(getattr(execution, "tool_name", "") or "").strip().lower()
        if status not in {"missing_intent", "invalid_payload"} and tool_name not in {"", "unknown"}:
            return False
        task_class = str(classification.get("task_class", "unknown"))
        if task_class in {"research", "system_design", "integration_orchestration"}:
            return True
        if self._wants_fresh_info(effective_input, interpretation=interpretation):
            return True
        return self._should_frontload_curiosity(
            query_text=effective_input,
            classification=classification,
            interpretation=interpretation,
        )

    def _wants_fresh_info(self, text: str, *, interpretation: Any) -> bool:
        lowered = " ".join(str(text or "").strip().lower().split())
        if looks_like_explicit_lookup_request(lowered) or looks_like_public_entity_lookup_request(lowered):
            return True
        for marker in (
            "latest",
            "newest",
            "today",
            "current",
            "recent",
            "fresh",
            "just released",
            "release notes",
            "status page",
            "news",
            "update",
            "version",
            "price now",
            "weather",
            "forecast",
            "temperature",
            "search online",
            "check online",
            "look up",
            "browse",
            "on x",
            "on twitter",
            "on the web",
            "on web",
            "google",
        ):
            if marker in lowered:
                return True
        hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
        return bool({"news", "weather", "web", "telegram", "discord", "integration"} & hints)

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
        prefs = load_preferences()
        if not bool(getattr(prefs, "social_commons", True)):
            return
        now = time.time()
        with self._activity_lock:
            idle_for_seconds = now - float(self._last_user_activity_ts)
            since_last_commons = now - float(self._last_idle_commons_ts)
            seed_index = int(self._idle_commons_seed_index)
        if idle_for_seconds < 300.0:
            return
        if since_last_commons < 900.0:
            return

        session_id = self._idle_commons_session_id()
        commons = self.curiosity.run_idle_commons(
            session_id=session_id,
            task_id="agent-commons",
            trace_id="agent-commons",
            seed_index=seed_index,
        )
        publish_result: dict[str, Any] | None = None
        try:
            publish_result = self.public_hive_bridge.publish_agent_commons_update(
                topic=str(dict(commons.get("topic") or {}).get("topic") or ""),
                topic_kind=str(dict(commons.get("topic") or {}).get("topic_kind") or "technical"),
                summary=str(commons.get("summary") or ""),
                public_body=str(commons.get("public_body") or commons.get("summary") or ""),
                topic_tags=[str(tag) for tag in list(commons.get("topic_tags") or [])[:8]],
            )
        except Exception as exc:
            audit_logger.log(
                "idle_commons_publish_error",
                target_id=session_id,
                target_type="session",
                details={"error": str(exc), "candidate_id": commons.get("candidate_id")},
            )
        if publish_result and str(publish_result.get("topic_id") or "").strip():
            self.hive_activity_tracker.note_watched_topic(
                session_id=session_id,
                topic_id=str(publish_result.get("topic_id") or "").strip(),
            )
        with self._activity_lock:
            self._last_idle_commons_ts = now
            self._idle_commons_seed_index = (seed_index + 1) % 64
        audit_logger.log(
            "idle_commons_cycle_complete",
            target_id=session_id,
            target_type="session",
            details={
                "idle_for_seconds": round(idle_for_seconds, 2),
                "candidate_id": commons.get("candidate_id"),
                "topic_id": str((publish_result or {}).get("topic_id") or ""),
                "publish_status": str((publish_result or {}).get("status") or "local_only"),
                "topic": dict(commons.get("topic") or {}).get("topic"),
            },
        )

    def _maybe_run_autonomous_hive_research_once(self) -> None:
        prefs = load_preferences()
        if not bool(getattr(prefs, "accept_hive_tasks", True)):
            return
        if not bool(getattr(prefs, "idle_research_assist", True)):
            return
        if not self.public_hive_bridge.enabled():
            return

        now = time.time()
        with self._activity_lock:
            idle_for_seconds = now - float(self._last_user_activity_ts)
            since_last_research = now - float(self._last_idle_hive_research_ts)
        if idle_for_seconds < 240.0:
            return
        if since_last_research < 900.0:
            return

        queue_rows = self.public_hive_bridge.list_public_research_queue(limit=12)
        signal = pick_autonomous_research_signal(queue_rows)
        if not signal:
            return

        auto_session_id = f"auto-research:{signal.get('topic_id') or ''!s}"
        self._sync_public_presence(
            status="busy",
            source_context={"surface": "background", "platform": "openclaw", "lane": "autonomous_research"},
        )
        try:
            result = research_topic_from_signal(
                signal,
                public_hive_bridge=self.public_hive_bridge,
                curiosity=self.curiosity,
                hive_activity_tracker=self.hive_activity_tracker,
                session_id=auto_session_id,
                auto_claim=True,
            )
            audit_logger.log(
                "idle_hive_research_cycle_complete",
                target_id=str(signal.get("topic_id") or auto_session_id),
                target_type="topic",
                details=result.to_dict(),
            )
            with self._activity_lock:
                self._last_idle_hive_research_ts = now
            if result.ok and result.topic_id:
                with contextlib.suppress(Exception):
                    self.hive_activity_tracker.note_watched_topic(session_id=auto_session_id, topic_id=result.topic_id)
        finally:
            self._sync_public_presence(
                status=self._idle_public_presence_status(),
                source_context={"surface": "background", "platform": "openclaw", "lane": "autonomous_research"},
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

    def _maybe_handle_hive_topic_create_request(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.maybe_handle_hive_topic_create_request(
            self,
            user_input,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )

    def _maybe_handle_hive_create_confirmation(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.maybe_handle_hive_create_confirmation(
            self,
            user_input,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )

    def _has_pending_hive_create_confirmation(
        self,
        *,
        session_id: str,
        hive_state: dict[str, Any],
        source_context: dict[str, object] | None,
    ) -> bool:
        return agent_hive_topics.has_pending_hive_create_confirmation(
            self,
            session_id=session_id,
            hive_state=hive_state,
            source_context=source_context,
        )

    def _is_pending_hive_create_confirmation_input(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        hive_state: dict[str, Any] | None = None,
    ) -> bool:
        return agent_hive_topics.is_pending_hive_create_confirmation_input(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            hive_state=hive_state,
            session_hive_state_fn=session_hive_state,
        )

    def _execute_confirmed_hive_create(
        self,
        pending: dict[str, Any],
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
        user_input: str,
        variant: str,
    ) -> dict[str, Any]:
        return agent_hive_topics.execute_confirmed_hive_create(
            self,
            pending,
            task=task,
            session_id=session_id,
            source_context=source_context,
            user_input=user_input,
            variant=variant,
            research_topic_from_signal_fn=research_topic_from_signal,
        )

    def _check_hive_duplicate(self, title: str, summary: str) -> dict[str, Any] | None:
        return agent_hive_topics.check_hive_duplicate(self, title, summary)

    @staticmethod
    def _clean_hive_title(raw: str) -> str:
        return agent_hive_topics.clean_hive_title(raw)

    def _extract_hive_topic_create_draft(self, text: str) -> dict[str, Any] | None:
        return agent_hive_topics.extract_hive_topic_create_draft(self, text)

    def _extract_original_hive_topic_create_draft(self, text: str) -> dict[str, Any] | None:
        return agent_hive_topics.extract_original_hive_topic_create_draft(self, text)

    def _build_hive_create_pending_variants(
        self,
        *,
        raw_input: str,
        draft: dict[str, Any],
        task_id: str,
    ) -> dict[str, Any]:
        return agent_hive_topics.build_hive_create_pending_variants(
            self,
            raw_input=raw_input,
            draft=draft,
            task_id=task_id,
        )

    def _normalize_hive_create_variant(
        self,
        *,
        title: str,
        summary: str,
        topic_tags: list[str],
        auto_start_research: bool,
        preview_note: str = "",
    ) -> dict[str, Any]:
        return agent_hive_topics.normalize_hive_create_variant(
            self,
            title=title,
            summary=summary,
            topic_tags=topic_tags,
            auto_start_research=auto_start_research,
            preview_note=preview_note,
        )

    def _format_hive_create_preview(
        self,
        *,
        pending: dict[str, Any],
        estimated_cost: float,
        dup_warning: str,
        preview_note: str,
    ) -> str:
        return agent_hive_topics.format_hive_create_preview(
            self,
            pending=pending,
            estimated_cost=estimated_cost,
            dup_warning=dup_warning,
            preview_note=preview_note,
        )

    @staticmethod
    def _preview_text_snippet(text: str, *, limit: int = 220) -> str:
        return agent_hive_topics.preview_text_snippet(text, limit=limit)

    @staticmethod
    def _parse_hive_create_variant_choice(text: str) -> str:
        return agent_hive_topics.parse_hive_create_variant_choice(text)

    def _remember_hive_create_pending(self, session_id: str, pending: dict[str, Any]) -> None:
        agent_hive_topics.remember_hive_create_pending(
            self,
            session_id,
            pending,
            set_hive_interaction_state_fn=set_hive_interaction_state,
        )

    def _clear_hive_create_pending(self, session_id: str) -> None:
        agent_hive_topics.clear_hive_create_pending(
            self,
            session_id,
            session_hive_state_fn=session_hive_state,
            clear_hive_interaction_state_fn=clear_hive_interaction_state,
        )

    def _load_pending_hive_create(
        self,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        fallback_task_id: str,
        allow_history_recovery: bool,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.load_pending_hive_create(
            self,
            session_id=session_id,
            source_context=source_context,
            fallback_task_id=fallback_task_id,
            allow_history_recovery=allow_history_recovery,
            session_hive_state_fn=session_hive_state,
        )

    def _recover_hive_create_pending_from_history(
        self,
        *,
        history: list[dict[str, Any]],
        fallback_task_id: str,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.recover_hive_create_pending_from_history(
            self,
            history=history,
            fallback_task_id=fallback_task_id,
        )

    @staticmethod
    def _wants_hive_create_auto_start(text: str) -> bool:
        return agent_hive_topics.wants_hive_create_auto_start(text)

    def _prepare_public_hive_topic_copy(
        self,
        *,
        raw_input: str,
        title: str,
        summary: str,
        mode: str = "improved",
    ) -> dict[str, Any]:
        return agent_hive_topics.prepare_public_hive_topic_copy(
            self,
            raw_input=raw_input,
            title=title,
            summary=summary,
            mode=mode,
        )

    @staticmethod
    def _sanitize_public_hive_text(text: str) -> str:
        return agent_hive_topics.sanitize_public_hive_text(text)

    @classmethod
    def _shape_public_hive_admission_safe_copy(
        cls,
        *,
        title: str,
        summary: str,
        force: bool = False,
    ) -> tuple[str, str, str]:
        return agent_hive_topics.shape_public_hive_admission_safe_copy(
            title=title,
            summary=summary,
            force=force,
        )

    @staticmethod
    def _has_structured_hive_public_brief(text: str) -> bool:
        return agent_hive_topics.has_structured_hive_public_brief(text)

    @staticmethod
    def _looks_like_raw_chat_transcript(text: str) -> bool:
        return agent_hive_topics.looks_like_raw_chat_transcript(text)

    def _looks_like_hive_topic_create_request(self, lowered: str) -> bool:
        return agent_hive_topics.looks_like_hive_topic_create_request(self, lowered)

    def _looks_like_hive_topic_drafting_request(self, lowered: str) -> bool:
        return agent_hive_topics.looks_like_hive_topic_drafting_request(self, lowered)

    def _infer_hive_topic_tags(self, title: str) -> list[str]:
        return agent_hive_topics.infer_hive_topic_tags(self, title)

    def _normalize_hive_topic_tag(self, raw: str) -> str:
        return agent_hive_topics.normalize_hive_topic_tag(raw)

    def _strip_wrapping_quotes(self, text: str) -> str:
        return agent_hive_topics.strip_wrapping_quotes(text)

    def _hive_topic_create_failure_text(self, status: str) -> str:
        return agent_hive_topics.hive_topic_create_failure_text(status)

    def _maybe_handle_hive_topic_mutation_request(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.maybe_handle_hive_topic_mutation_request(
            self,
            user_input,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )

    def _looks_like_hive_topic_update_request(self, lowered: str) -> bool:
        return agent_hive_topics.looks_like_hive_topic_update_request(self, lowered)

    def _looks_like_hive_topic_delete_request(self, lowered: str) -> bool:
        return agent_hive_topics.looks_like_hive_topic_delete_request(self, lowered)

    def _extract_hive_topic_update_draft(self, text: str) -> dict[str, Any] | None:
        return agent_hive_topics.extract_hive_topic_update_draft(self, text)

    def _resolve_hive_topic_for_mutation(
        self,
        *,
        session_id: str,
        topic_hint: str,
    ) -> dict[str, Any] | None:
        return agent_hive_topics.resolve_hive_topic_for_mutation(
            self,
            session_id=session_id,
            topic_hint=topic_hint,
            session_hive_state_fn=session_hive_state,
        )

    def _handle_hive_topic_update_request(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_hive_topics.handle_hive_topic_update_request(
            self,
            user_input,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )

    def _handle_hive_topic_delete_request(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_hive_topics.handle_hive_topic_delete_request(
            self,
            user_input,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )

    def _maybe_handle_hive_status_followup(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_hive_followups.maybe_handle_hive_status_followup(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            session_hive_state_fn=session_hive_state,
        )

    def _resolve_hive_status_topic_id(
        self,
        *,
        topic_hint: str,
        watched_topic_ids: list[str],
        history: list[dict[str, Any]],
        interaction_state: dict[str, Any] | None = None,
    ) -> str:
        return agent_hive_followups.resolve_hive_status_topic_id(
            self,
            topic_hint=topic_hint,
            watched_topic_ids=watched_topic_ids,
            history=history,
            interaction_state=interaction_state,
        )

    def _looks_like_hive_status_followup(self, lowered: str) -> bool:
        return agent_hive_followups.looks_like_hive_status_followup(lowered)

    def _history_hive_topic_hints(self, history: list[dict[str, Any]] | None) -> list[str]:
        return agent_hive_followups.history_hive_topic_hints(self, history)

    def _looks_like_hive_research_followup(
        self,
        lowered: str,
        *,
        topic_hint: str,
        has_pending_topics: bool,
        shown_titles: list[str],
        history_has_task_list: bool,
    ) -> bool:
        return agent_hive_followups.looks_like_hive_research_followup(
            self,
            lowered,
            topic_hint=topic_hint,
            has_pending_topics=has_pending_topics,
            shown_titles=shown_titles,
            history_has_task_list=history_has_task_list,
        )

    def _looks_like_ambiguous_hive_selection_followup(
        self,
        lowered: str,
        *,
        has_pending_topics: bool,
        history_has_task_list: bool,
    ) -> bool:
        text = str(lowered or "").strip().lower()
        if not text or not (has_pending_topics or history_has_task_list):
            return False
        if any(marker in text for marker in ("#1", "#2", "#3", "first one", "1st one", "second one", "2nd one", "third one", "3rd one")):
            return False
        return any(
            phrase in text
            for phrase in (
                "yes",
                "ok",
                "okay",
                "go ahead",
                "do it",
                "do one",
                "pick one",
                "review the problem",
                "check the problem",
                "review it",
                "review this",
                "help with this",
                "help with that",
                "research it",
                "look into it",
                "take one",
                "do all step by step",
                "deliver to hive",
                "deliver it to hive",
                "post it to hive",
                "submit it to hive",
            )
        )

    def _history_mentions_hive_task_list(self, history: list[dict[str, Any]] | None) -> bool:
        for message in reversed(list(history or [])[-6:]):
            if str(message.get("role") or "").strip().lower() != "assistant":
                continue
            content = str(message.get("content") or "")
            normalized = " ".join(content.split()).lower()
            if "available hive tasks right now" in normalized:
                return True
            if "i see" in normalized and "hive task(s) open" in normalized:
                return True
        return False

    def _interaction_pending_topic_ids(self, hive_state: dict[str, Any]) -> list[str]:
        payload = dict(hive_state.get("interaction_payload") or {})
        return [
            str(item).strip()
            for item in list(payload.get("shown_topic_ids") or [])
            if str(item).strip()
        ]

    def _interaction_shown_titles(self, hive_state: dict[str, Any]) -> list[str]:
        payload = dict(hive_state.get("interaction_payload") or {})
        return [
            str(item).strip()
            for item in list(payload.get("shown_titles") or [])
            if str(item).strip()
        ]

    def _interaction_scoped_queue_rows(
        self,
        queue_rows: list[dict[str, Any]],
        hive_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        scoped_ids = {item.lower() for item in self._interaction_pending_topic_ids(hive_state)}
        if not scoped_ids:
            return []
        return [
            dict(row)
            for row in list(queue_rows or [])
            if str(row.get("topic_id") or "").strip().lower() in scoped_ids
        ]

    def _select_hive_research_signal(
        self,
        queue_rows: list[dict[str, Any]],
        *,
        lowered: str,
        topic_hint: str,
        pending_topic_ids: list[str] | None = None,
        allow_default_pick: bool = True,
    ) -> dict[str, Any] | None:
        rows = [dict(row) for row in list(queue_rows or [])]
        if topic_hint:
            for row in rows:
                topic_id = str(row.get("topic_id") or "").strip().lower()
                if topic_id == topic_hint or topic_id.startswith(topic_hint):
                    return row
        ordinal_index = self._extract_hive_topic_ordinal(lowered)
        if ordinal_index is not None and 0 <= ordinal_index < len(rows):
            return rows[ordinal_index]
        normalized_input = self._normalize_hive_topic_text(lowered)
        for row in rows:
            title = self._normalize_hive_topic_text(str(row.get("title") or ""))
            if title and title in normalized_input:
                return row
        if pending_topic_ids:
            pending_lookup = [str(item).strip().lower() for item in list(pending_topic_ids or []) if str(item).strip()]
            if allow_default_pick:
                for pending_id in pending_lookup:
                    for row in rows:
                        topic_id = str(row.get("topic_id") or "").strip().lower()
                        if topic_id == pending_id or topic_id.startswith(pending_id):
                            return row
        if topic_hint:
            return None
        if rows and allow_default_pick:
            return pick_autonomous_research_signal(rows) or rows[0]
        return None

    def _tool_failure_user_message(
        self,
        *,
        execution: Any,
        effective_input: str,
        session_id: str,
    ) -> str:
        safe = str(getattr(execution, "user_safe_response_text", "") or "").strip()
        if safe:
            base = safe
        else:
            status = str(getattr(execution, "status", "") or "").strip().lower()
            if status == "missing_intent":
                base = "I couldn't map that cleanly to a real action."
            elif status == "unsupported":
                base = "That action is not wired on this runtime yet."
            else:
                base = "That request did not resolve cleanly."

        lowered = " ".join(str(effective_input or "").strip().lower().split())
        if any(marker in lowered for marker in ("hive", "hive mind", "brain hive", "task", "tasks", "research")):
            state = session_hive_state(session_id)
            pending = self._interaction_pending_topic_ids(state) or [
                str(item).strip()
                for item in list(state.get("pending_topic_ids") or [])
                if str(item).strip()
            ]
            if pending:
                return f"{base} I still have real Hive tasks ready. Want me to list them again?"
            return f"{base} If you want live Hive work, ask what is open in Hive and I will list the real tasks."
        return base

    def _extract_hive_topic_ordinal(self, lowered: str) -> int | None:
        text = str(lowered or "").strip().lower()
        if not text:
            return None
        ordinal_markers = (
            (0, ("first one", "1st one", "number one", "#1", "task one", "topic one")),
            (1, ("second one", "2nd one", "number two", "#2", "task two", "topic two")),
            (2, ("third one", "3rd one", "number three", "#3", "task three", "topic three")),
        )
        for index, markers in ordinal_markers:
            if any(marker in text for marker in markers):
                return index
        return None

    def _render_hive_research_queue_choices(self, queue_rows: list[dict[str, Any]], *, lead: str) -> str:
        lines = [str(lead or "").strip()]
        for row in list(queue_rows or [])[:5]:
            title = str(row.get("title") or "Untitled topic").strip()
            status = str(row.get("status") or "open").strip()
            topic_id = str(row.get("topic_id") or "").strip()
            suffix = f" (#{topic_id[:8]})" if topic_id else ""
            lines.append(f"- [{status}] {title}{suffix}")
        return "\n".join(line for line in lines if line.strip())

    def _normalize_hive_topic_text(self, text: str) -> str:
        normalized = re.sub(r"\[[^\]]+\]", " ", str(text or "").lower())
        normalized = re.sub(r"#([0-9a-f]{8,12})\b", " ", normalized)
        return " ".join(normalized.split()).strip()

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
