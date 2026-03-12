from __future__ import annotations

import argparse
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core import audit_logger, feedback_engine, policy_engine
from core.autonomous_topic_research import pick_autonomous_research_signal, research_topic_from_signal
from core.candidate_knowledge_lane import get_candidate_by_id
from core.channel_actions import dispatch_outbound_post_intent, parse_channel_post_intent
from core.curiosity_roamer import CuriosityRoamer
from core.human_input_adapter import adapt_user_input, runtime_session_id
from core.hive_activity_tracker import (
    HiveActivityTracker,
    clear_hive_interaction_state,
    note_smalltalk_turn,
    prune_stale_hive_interaction_state,
    session_hive_state,
    set_hive_interaction_state,
    update_session_hive_state,
)
from core.local_operator_actions import dispatch_operator_action, list_operator_tools, parse_operator_action_intent
from core.onboarding import get_agent_display_name
from core.media_analysis_pipeline import MediaAnalysisPipeline
from core.media_ingestion import build_media_context_snippets, ingest_media_evidence
from core.identity_manager import load_active_persona
from core.knowledge_fetcher import request_relevant_holders
from core.knowledge_registry import register_local_shard, sync_local_learning_shards
from core.memory_first_router import MemoryFirstRouter
from core.public_hive_bridge import PublicHiveBridge
from core.persistent_memory import (
    append_conversation_event,
    ensure_memory_files,
    maybe_handle_memory_command,
    search_user_heuristics,
    session_memory_policy,
)
from core.reasoning_engine import Plan, build_plan, render_response
from core.parent_orchestrator import orchestrate_parent_task
from core.runtime_execution_tools import execute_runtime_tool
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
from core.runtime_task_events import emit_runtime_event
from core.shard_synthesizer import build_generalized_query, from_task_result
from core.task_router import classify, create_task_record, load_task_record
from core.tool_intent_executor import execute_tool_intent, should_attempt_tool_intent
from core.tiered_context_loader import TieredContextLoader
from core.user_preferences import load_preferences, maybe_handle_preference_command
from core.logging_config import setup_logging
from retrieval.swarm_query import dispatch_query_shard
from retrieval.web_adapter import WebAdapter
from storage.db import get_connection
from storage.migrations import run_migrations
from network.signer import get_local_peer_id


_HIVE_TOPIC_FULL_ID_RE = re.compile(r"\b([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\b", re.IGNORECASE)
_HIVE_TOPIC_SHORT_ID_RE = re.compile(r"#\s*([0-9a-f]{8,12})\b", re.IGNORECASE)


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


class NullaAgent:
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

    def start(self) -> AgentRuntime:
        setup_logging(
            level=str(policy_engine.get("observability.log_level", "INFO")),
            json_output=bool(policy_engine.get("observability.json_logs", True)),
        )
        run_migrations()
        mark_stale_runtime_checkpoints_interrupted()
        policy_engine.load(force_reload=True)
        ensure_memory_files()
        _ = load_active_persona(self.persona_id)
        self._sync_public_presence(status=self._idle_public_presence_status())
        self._start_public_presence_heartbeat()
        self._start_idle_commons_loop()

        return AgentRuntime(
            backend_name=self.backend_name,
            device=self.device,
            persona_id=self.persona_id,
            swarm_enabled=self.swarm_enabled,
        )

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
        )
        runtime_source_context = dict(checkpoint_bundle.get("source_context") or runtime_source_context)
        checkpoint_state = str(checkpoint_bundle.get("state") or "created")
        if checkpoint_state == "missing_resume":
            return self._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response="No interrupted runtime task is available to resume in this session.",
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

        startup_message = self._startup_sequence_fast_path(effective_input)
        if startup_message:
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=startup_message,
                confidence=0.97,
                source_context=source_context,
                reason="startup_sequence_fast_path",
            )

        handled, response = maybe_handle_preference_command(effective_input)
        if handled:
            self._sync_public_presence(
                status=self._idle_public_presence_status(),
                source_context=source_context,
            )
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=response,
                confidence=0.92,
                source_context=source_context,
                reason="user_preference_command",
            )

        handled, response = self._maybe_handle_hive_runtime_command(
            effective_input,
            session_id=session_id,
        )
        if handled:
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=response,
                confidence=0.89,
                source_context=source_context,
                reason="hive_activity_command",
            )

        hive_followup = self._maybe_handle_hive_research_followup(
            effective_input,
            session_id=session_id,
            source_context=source_context,
        )
        if hive_followup is not None:
            return hive_followup

        hive_status = self._maybe_handle_hive_status_followup(
            effective_input,
            session_id=session_id,
            source_context=source_context,
        )
        if hive_status is not None:
            return hive_status

        handled, response = maybe_handle_memory_command(effective_input, session_id=session_id)
        if handled:
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=response,
                confidence=0.93,
                source_context=source_context,
                reason="memory_command",
            )

        ui_command = self._ui_command_fast_path(normalized_input, source_surface=source_surface)
        if ui_command:
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=ui_command,
                confidence=0.97,
                source_context=source_context,
                reason="ui_command_fast_path",
            )

        credit_status = self._credit_status_fast_path(normalized_input, source_surface=source_surface)
        if credit_status:
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=credit_status,
                confidence=0.95,
                source_context=source_context,
                reason="credit_status_fast_path",
            )

        date_time_status = self._date_time_fast_path(normalized_input, source_surface=source_surface)
        if date_time_status:
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=date_time_status,
                confidence=0.97,
                source_context=source_context,
                reason="date_time_fast_path",
            )

        live_info_status = self._maybe_handle_live_info_fast_path(
            effective_input,
            session_id=session_id,
            source_context=source_context,
            interpretation=interpreted,
        )
        if live_info_status is not None:
            return live_info_status

        evaluative = self._evaluative_conversation_fast_path(normalized_input, source_surface=source_surface)
        if evaluative:
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=evaluative,
                confidence=0.88,
                source_context=source_context,
                reason="evaluative_conversation_fast_path",
            )

        smalltalk = self._smalltalk_fast_path(
            normalized_input,
            source_surface=source_surface,
            session_id=session_id,
        )
        if smalltalk:
            smalltalk_phrase = normalized_input.lower().strip(" \t\r\n?!.,")
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=smalltalk,
                confidence=0.90,
                source_context=source_context,
                reason="help_fast_path" if smalltalk_phrase in {"what can you do", "help"} else "smalltalk_fast_path",
            )

        self._sync_public_presence(status="busy", source_context=source_context)
        try:
            # 1) create + classify
            task = self._resolve_runtime_task(
                effective_input=effective_input,
                session_id=session_id,
                source_context=source_context,
            )
            self._update_runtime_checkpoint_context(
                source_context,
                task_id=task.task_id,
            )
            classification_context = interpreted.as_context()
            if source_context:
                classification_context["source_context"] = dict(source_context)
                classification_context["source_surface"] = source_context.get("surface")
                classification_context["source_platform"] = source_context.get("platform")
            classification = classify(effective_input, context=classification_context)
            self._update_task_class(task.task_id, classification["task_class"])
            self._update_runtime_checkpoint_context(
                source_context,
                task_id=task.task_id,
                task_class=str(classification.get("task_class") or "unknown"),
            )
            self._emit_runtime_event(
                source_context,
                event_type="task_classified",
                message=f"Task classified as {str(classification.get('task_class') or 'unknown')}.",
                task_id=task.task_id,
                task_class=str(classification.get("task_class") or "unknown"),
            )

            post_intent, post_error = parse_channel_post_intent(effective_input)
            if post_intent is not None:
                dispatch = dispatch_outbound_post_intent(
                    post_intent,
                    task_id=task.task_id,
                    session_id=session_id,
                    source_context=source_context,
                )
                return self._action_fast_path_result(
                    task_id=task.task_id,
                    session_id=session_id,
                    user_input=effective_input,
                    response=dispatch.response_text,
                    confidence=0.95 if dispatch.ok else 0.42,
                    source_context=source_context,
                    reason=f"channel_post_{dispatch.status}",
                    success=dispatch.ok,
                    details={
                        "platform": dispatch.platform,
                        "target": dispatch.target,
                        "record_id": dispatch.record_id,
                        "error": dispatch.error,
                    },
                )
            if post_error:
                return self._action_fast_path_result(
                    task_id=task.task_id,
                    session_id=session_id,
                    user_input=effective_input,
                    response=(
                        "I can do that, but I need the exact message text. "
                        "Use a format like: post to Discord: \"We are live tonight.\""
                    ),
                    confidence=0.40,
                    source_context=source_context,
                    reason="channel_post_missing_message",
                    success=False,
                    details={"error": post_error},
                )

            operator_intent = parse_operator_action_intent(user_input) or parse_operator_action_intent(effective_input)
            if operator_intent is not None:
                dispatch = dispatch_operator_action(
                    operator_intent,
                    task_id=task.task_id,
                    session_id=session_id,
                )
                workflow_summary = self._action_workflow_summary(
                    operator_kind=operator_intent.kind,
                    dispatch_status=dispatch.status,
                    details=dispatch.details,
                )
                return self._action_fast_path_result(
                    task_id=task.task_id,
                    session_id=session_id,
                    user_input=effective_input,
                    response=dispatch.response_text,
                    confidence=dispatch.learned_plan.confidence if dispatch.learned_plan else (0.9 if dispatch.ok else 0.45),
                    source_context=source_context,
                    reason=f"operator_action_{dispatch.status}",
                    success=dispatch.ok,
                    details=dispatch.details,
                    mode_override=(
                        "tool_executed"
                        if dispatch.status == "executed"
                        else "tool_preview"
                        if dispatch.status in {"reported", "approval_required"}
                        else "tool_failed"
                    ),
                    task_outcome=(
                        "success"
                        if dispatch.status == "executed"
                        else "pending_approval"
                        if dispatch.status in {"reported", "approval_required"}
                        else "failed"
                    ),
                    learned_plan=dispatch.learned_plan,
                    workflow_summary=workflow_summary,
                )

            hive_topic_create = self._maybe_handle_hive_topic_create_request(
                effective_input,
                task=task,
                session_id=session_id,
                source_context=source_context,
            )
            if hive_topic_create is not None:
                return hive_topic_create

            # 2) build tiered prompt context and relevant evidence
            surface = str((source_context or {}).get("surface", "cli")).lower()
            is_chat_surface = surface in {"channel", "openclaw", "api"}
            context_result = self.context_loader.load(
                task=task,
                classification=classification,
                interpretation=interpreted,
                persona=persona,
                session_id=session_id,
            )
            ranked = context_result.local_candidates
            curiosity_result = None
            curiosity_plan_candidates: list[dict[str, Any]] = []
            curiosity_context_snippets: list[dict[str, Any]] = []
            if self._should_frontload_curiosity(
                query_text=effective_input,
                classification=classification,
                interpretation=interpreted,
            ):
                curiosity_result = self.curiosity.maybe_roam(
                    task=task,
                    user_input=effective_input,
                    classification=classification,
                    interpretation=interpreted,
                    context_result=context_result,
                    session_id=session_id,
                )
                curiosity_plan_candidates, curiosity_context_snippets = self._curiosity_candidate_evidence(
                    curiosity_result.candidate_ids
                )
            tool_execution = self._maybe_execute_model_tool_intent(
                task=task,
                effective_input=effective_input,
                classification=classification,
                interpretation=interpreted,
                context_result=context_result,
                persona=persona,
                session_id=session_id,
                source_context=source_context,
                surface=surface,
            )
            if tool_execution is not None:
                return self._action_fast_path_result(
                    task_id=task.task_id,
                    session_id=session_id,
                    user_input=effective_input,
                    response=tool_execution["response"],
                    confidence=float(tool_execution["confidence"]),
                    source_context=source_context,
                    reason=f"model_tool_intent_{tool_execution['status']}",
                    success=bool(tool_execution["success"]),
                    details=dict(tool_execution["details"]),
                    mode_override=str(tool_execution["mode"]),
                    task_outcome=str(tool_execution["task_outcome"]),
                    learned_plan=tool_execution.get("learned_plan"),
                    workflow_summary=str(tool_execution["workflow_summary"]),
                )

            # Parent orchestration decides whether to decompose immediately or stay local.
            orchestrate_parent_task(
                parent_task_id=task.task_id,
                user_input=effective_input,
                classification=classification,
                environment_tags={
                    "os": task.environment_os,
                    "shell": task.environment_shell,
                    "runtime": task.environment_runtime,
                    "version_family": task.environment_version_hint,
                },
                exclude_host_group_hint_hash=None,
            )

            model_execution = self.memory_router.resolve(
                task=task,
                classification=classification,
                interpretation=interpreted,
                context_result=context_result,
                persona=persona,
                force_model=is_chat_surface,
                surface=surface,
                source_context=source_context,
            )
            model_candidate = model_execution.as_plan_candidate()
            media_source_context = dict(source_context or {})
            if is_chat_surface and "fetch_text_references" not in media_source_context:
                media_source_context["fetch_text_references"] = True
            media_evidence = ingest_media_evidence(
                task_id=task.task_id,
                trace_id=task.task_id,
                user_input=effective_input,
                source_context=media_source_context,
            )
            media_analysis = self.media_pipeline.analyze(
                task_id=task.task_id,
                task_summary=task.task_summary,
                evidence_items=media_evidence,
            )
            media_context_snippets = build_media_context_snippets(media_analysis.evidence_items or media_evidence)
            media_candidate = None
            if media_analysis.analysis_text:
                media_candidate = {
                    "summary": media_analysis.analysis_text.splitlines()[0][:220] if media_analysis.analysis_text else "Media evidence review",
                    "resolution_pattern": [],
                    "score": 0.58,
                    "source_type": "multimodal_candidate",
                    "source_node_id": media_analysis.provider_id,
                    "provider_name": media_analysis.provider_id,
                    "model_name": media_analysis.provider_id,
                    "candidate_id": media_analysis.candidate_id,
                }

            web_notes = self._collect_live_web_notes(
                task_id=task.task_id,
                query_text=effective_input,
                classification=classification,
                interpretation=interpreted,
                source_context=source_context,
            )
            web_plan_candidates = self._web_note_plan_candidates(
                query_text=effective_input,
                classification=classification,
                web_notes=web_notes,
            )

            # 3) if weak local confidence, dispatch async swarm query for future cache warming
            if (not ranked) or float(context_result.retrieval_confidence_score or 0.0) < 0.65:
                try:
                    query = build_generalized_query(task, classification)
                    request_relevant_holders(
                        classification.get("task_class", "unknown"),
                        task.task_summary,
                        query_id=query["query_id"],
                        limit=3,
                    )
                    dispatch_query_shard(query, limit=5)
                except Exception as e:
                    audit_logger.log(
                        "swarm_query_dispatch_error",
                        target_id=task.task_id,
                        target_type="task",
                        details={"error": str(e)},
                    )

            # 4) build evidence from current local state only
            evidence = {
                "candidates": sorted(
                    curiosity_plan_candidates + web_plan_candidates,
                    key=lambda item: float(item.get("score") or 0.0),
                    reverse=True,
                )[:3],
                "local_candidates": ranked[:3],
                "swarm_candidates": context_result.swarm_metadata[:3],
                "model_candidates": [candidate for candidate in [model_candidate, media_candidate] if candidate],
                "context_snippets": curiosity_context_snippets + context_result.context_snippets() + media_context_snippets,
                "assembled_context": context_result.assembled_context(),
                "prompt_assembly_report": context_result.report.to_dict(),
                "model_execution": {
                    "source": model_execution.source,
                    "provider_id": model_execution.provider_id,
                    "used_model": model_execution.used_model,
                    "cache_hit": model_execution.cache_hit,
                    "candidate_id": model_execution.candidate_id,
                    "trust_score": model_execution.trust_score,
                    "validation_state": model_execution.validation_state,
                },
                "media_analysis": {
                    "used_provider": media_analysis.used_provider,
                    "provider_id": media_analysis.provider_id,
                    "candidate_id": media_analysis.candidate_id,
                    "reason": media_analysis.reason,
                    "evidence_count": len(media_analysis.evidence_items or media_evidence),
                },
                "external_media_evidence": media_analysis.evidence_items or media_evidence,
                "web_notes": web_notes,
            }

            workspace_build = self._maybe_run_workspace_build_pipeline(
                task=task,
                effective_input=effective_input,
                classification=classification,
                interpretation=interpreted,
                web_notes=web_notes,
                session_id=session_id,
                source_context=source_context,
            )
            if workspace_build is not None:
                return workspace_build

            # 5) build safe local plan
            plan = build_plan(
                task=task,
                classification=classification,
                evidence=evidence,
                persona=persona,
            )

            # 6) safety-first gate (advice-only default)
            gate = self._default_gate(plan, classification)

            # 7) render response from the grounded plan rather than raw model prose
            response = render_response(
                plan,
                gate,
                persona,
                input_interpretation=interpreted,
                prompt_assembly_report=context_result.report,
                surface=surface,
            )
            # 8) evaluate outcome (v1: advice-only heuristic)
            execution_result = {"mode": "advice_only"}
            outcome = feedback_engine.evaluate_outcome(task, plan, gate, execution_result)
            feedback_engine.apply(task, evidence, outcome)

            if curiosity_result is None:
                curiosity_result = self.curiosity.maybe_roam(
                    task=task,
                    user_input=effective_input,
                    classification=classification,
                    interpretation=interpreted,
                    context_result=context_result,
                    session_id=session_id,
                )
            workflow_summary = self._task_workflow_summary(
                classification=classification,
                context_result=context_result,
                model_execution=evidence["model_execution"],
                media_analysis=evidence["media_analysis"],
                curiosity_result=curiosity_result.to_dict(),
                gate_mode=gate.mode,
            )
            # 9) synthesize a local shard if durable enough
            if outcome.is_success and outcome.is_durable:
                shard = from_task_result(task, plan, outcome)
                if policy_engine.validate_learned_shard(shard):
                    self._store_local_shard(
                        shard,
                        origin_task_id=task.task_id,
                        origin_session_id=session_id,
                    )

            public_export = self._maybe_publish_public_task(
                task=task,
                classification=classification,
                assistant_response=response,
                session_id=session_id,
            )
            topic_id = str((public_export or {}).get("topic_id") or "").strip()
            if topic_id:
                self.hive_activity_tracker.note_watched_topic(session_id=session_id, topic_id=topic_id)
            turn_result = self._turn_result(
                response,
                self._grounded_response_class(gate=gate, classification=classification),
                workflow_summary=workflow_summary,
                debug_origin="grounded_plan",
            )
            self._apply_interaction_transition(session_id, turn_result)
            response = self._decorate_chat_response(
                turn_result,
                session_id=session_id,
                source_context=source_context,
            )

            audit_logger.log(
                "agent_run_once_complete",
                target_id=task.task_id,
                target_type="task",
                details={
                    "mode": gate.mode,
                    "confidence": plan.confidence,
                    "swarm_candidates_present": len(ranked),
                    "understanding_confidence": interpreted.understanding_confidence,
                    "input_quality_flags": interpreted.quality_flags,
                    "context_retrieval_confidence": context_result.report.retrieval_confidence,
                    "context_budget_used": context_result.report.total_tokens_used(),
                    "model_execution_source": model_execution.source,
                    "model_provider_id": model_execution.provider_id,
                    "media_analysis_reason": media_analysis.reason,
                    "media_evidence_count": len(media_analysis.evidence_items or media_evidence),
                    "curiosity_mode": curiosity_result.mode,
                    "curiosity_reason": curiosity_result.reason,
                    "curiosity_candidate_count": len(curiosity_result.candidate_ids),
                    "source_surface": (source_context or {}).get("surface"),
                    "source_platform": (source_context or {}).get("platform"),
                },
            )

            append_conversation_event(
                session_id=session_id,
                user_input=effective_input,
                assistant_output=response,
                source_context=source_context,
            )
            self._emit_runtime_event(
                source_context,
                event_type="task_completed",
                message=f"Completed task with final response: {self._runtime_preview(response)}",
                task_id=task.task_id,
                task_class=str(classification.get("task_class") or "unknown"),
            )
            self._finalize_runtime_checkpoint(
                source_context,
                status="completed",
                final_response=response,
            )

            return {
                "task_id": task.task_id,
                "response": response,
                "mode": gate.mode,
                "confidence": plan.confidence,
                "understanding_confidence": interpreted.understanding_confidence,
                "interpreted_input": effective_input,
                "topic_hints": interpreted.topic_hints,
                "prompt_assembly_report": context_result.report.to_dict(),
                "model_execution": evidence["model_execution"],
                "media_analysis": evidence["media_analysis"],
                "curiosity": curiosity_result.to_dict(),
                "backend": self.backend_name,
                "device": self.device,
                "session_id": session_id,
                "source_context": dict(source_context or {}),
                "workflow_summary": workflow_summary,
                "response_class": turn_result.response_class.value,
            }
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

    def _prepare_runtime_checkpoint(
        self,
        *,
        session_id: str,
        raw_user_input: str,
        effective_input: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        base_source_context = dict(source_context or {})
        base_source_context.setdefault("runtime_session_id", session_id)
        base_source_context.setdefault("session_id", session_id)
        resumable = latest_resumable_checkpoint(session_id)
        wants_resume = self._looks_like_resume_request(raw_user_input)
        same_request_retry = bool(
            resumable
            and self._resume_request_key(effective_input) == self._resume_request_key(str(resumable.get("request_text") or ""))
        )
        if resumable and (wants_resume or same_request_retry):
            resumed = resume_runtime_checkpoint(
                str(resumable.get("checkpoint_id") or ""),
                source_context=base_source_context,
            )
            if resumed is not None:
                merged_source_context = dict(resumed.get("source_context") or {})
                merged_source_context.update(base_source_context)
                merged_source_context["runtime_session_id"] = session_id
                merged_source_context["session_id"] = session_id
                merged_source_context["runtime_checkpoint_id"] = str(resumed.get("checkpoint_id") or "")
                return {
                    "state": "resumed",
                    "checkpoint": resumed,
                    "effective_input": str(resumed.get("request_text") or effective_input),
                    "source_context": merged_source_context,
                }
        if wants_resume and not resumable:
            return {
                "state": "missing_resume",
                "checkpoint": None,
                "effective_input": effective_input,
                "source_context": base_source_context,
            }
        checkpoint = create_runtime_checkpoint(
            session_id=session_id,
            request_text=effective_input,
            source_context=base_source_context,
        )
        base_source_context["runtime_session_id"] = session_id
        base_source_context["session_id"] = session_id
        base_source_context["runtime_checkpoint_id"] = str(checkpoint.get("checkpoint_id") or "")
        return {
            "state": "created",
            "checkpoint": checkpoint,
            "effective_input": effective_input,
            "source_context": base_source_context,
        }

    def _resolve_runtime_task(
        self,
        *,
        effective_input: str,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> Any:
        checkpoint_id = self._runtime_checkpoint_id(source_context)
        if checkpoint_id:
            checkpoint = get_runtime_checkpoint(checkpoint_id)
            if checkpoint:
                existing_task = load_task_record(str(checkpoint.get("task_id") or ""))
                if existing_task is not None:
                    return existing_task
        return create_task_record(effective_input, session_id=session_id)

    def _update_runtime_checkpoint_context(
        self,
        source_context: dict[str, object] | None,
        *,
        task_id: str | None = None,
        task_class: str | None = None,
    ) -> None:
        checkpoint_id = self._runtime_checkpoint_id(source_context)
        if not checkpoint_id:
            return
        update_runtime_checkpoint(
            checkpoint_id,
            task_id=task_id,
            task_class=task_class,
            source_context=dict(source_context or {}),
        )

    def _finalize_runtime_checkpoint(
        self,
        source_context: dict[str, object] | None,
        *,
        status: str,
        final_response: str = "",
        failure_text: str = "",
    ) -> None:
        checkpoint_id = self._runtime_checkpoint_id(source_context)
        if not checkpoint_id:
            return
        finalize_runtime_checkpoint(
            checkpoint_id,
            status=status,
            final_response=final_response,
            failure_text=failure_text,
        )

    def _runtime_checkpoint_id(self, source_context: dict[str, object] | None) -> str:
        return str((source_context or {}).get("runtime_checkpoint_id") or "").strip()

    def _merge_runtime_source_contexts(
        self,
        primary: dict[str, Any] | None,
        secondary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        merged = dict(primary or {})
        secondary_dict = dict(secondary or {})
        primary_history = [item for item in list(merged.get("conversation_history") or []) if isinstance(item, dict)]
        secondary_history = [item for item in list(secondary_dict.get("conversation_history") or []) if isinstance(item, dict)]
        merged.update(secondary_dict)
        history: list[dict[str, Any]] = []
        for item in (primary_history + secondary_history)[-16:]:
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            if role not in {"system", "user", "assistant"} or not content:
                continue
            history.append({"role": role, "content": content[:4000]})
        merged["conversation_history"] = history[-12:]
        return merged

    def _looks_like_resume_request(self, text: str) -> bool:
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

    def _resume_request_key(self, text: str) -> str:
        return " ".join(str(text or "").strip().lower().split())

    def _smalltalk_fast_path(self, normalized_input: str, *, source_surface: str, session_id: str) -> str | None:
        if source_surface not in {"channel", "openclaw", "api"}:
            return None
        phrase = normalized_input.lower().strip(" \t\r\n?!.,")
        if not phrase:
            return None
        name = get_agent_display_name()
        prefs = load_preferences()
        with_joke = prefs.humor_percent >= 70
        character = str(prefs.character_mode or "").strip()

        if phrase in {"hi", "hello", "hey", "yo", "sup", "gm", "good morning", "morning"}:
            repeat_count = note_smalltalk_turn(session_id, key="greeting")
            if repeat_count >= 3:
                return "Yep, I got the hello. Skip the greeting and tell me what you want me to do."
            if repeat_count == 2:
                return "Yep, got your hello. What do you want me to do?"
            msg = f"Hey. I’m {name}. What do you need?"
            if with_joke:
                msg += " Keep it sharp and I’ll keep it fast."
            return msg
        if phrase in {"how are you", "how are you doing", "how are u", "how r u"}:
            repeat_count = note_smalltalk_turn(session_id, key="status_check")
            if repeat_count >= 2:
                return "Still stable. Memory online, mesh ready. Give me the task."
            msg = "Running stable. Memory online, mesh ready."
            if with_joke:
                msg += " Caffeine level: synthetic but dangerous."
            if character:
                msg += f" Character mode: {character}."
            return msg
        if any(marker in phrase for marker in {"same crap answer", "same answer", "why same", "why are you repeating"}):
            return "Because the fallback lane fired instead of the real task lane. Give me the task again or say `pull the tasks` and I will act."
        if ("took u" in phrase or "took you" in phrase) and any(marker in phrase for marker in {"2 mins", "two mins", "bs", "bullshit"}):
            return "You're right. That reply was slow and useless. Give me the task again and I will go straight for the action lane."
        if phrase in {"thanks", "thank you", "thx"}:
            return "Anytime. Send the next task."
        if phrase in {"what can you do", "help"}:
            return self._help_capabilities_text()
        if phrase in {"kill me lol", "omfg just kill me", "omfg just kill me lol", "kms lol"}:
            return "You're frustrated. Let's fix the thing instead. If you want me to go by a different name, I'll use it."
        return None

    def _evaluative_conversation_fast_path(self, normalized_input: str, *, source_surface: str) -> str | None:
        if source_surface not in {"channel", "openclaw", "api"}:
            return None
        phrase = " ".join(str(normalized_input or "").strip().lower().split())
        if not phrase:
            return None
        if not self._looks_like_evaluative_turn(phrase):
            return None
        if "not a dumb" in phrase or "better now" in phrase or "not dumb" in phrase:
            return "Better than before, yes. The Hive/task flow is cleaner now, but the conversation layer still needs work."
        if any(marker in phrase for marker in ("how are you acting", "why are you acting", "you sound weird", "still feels weird", "this feels weird")):
            return "Because the routing is still too stitched together. Hive flow is better now, but normal conversation still needs a cleaner control path."
        if any(marker in phrase for marker in ("you sound dumb", "you are dumb", "you so stupid", "this still feels dumb")):
            return "Fair. The wrapper got better, but it still drops into weak fallback behavior too often."
        return "Yeah, better than before, but still uneven. Give me a concrete task and I'll stay on the action lane."

    def _looks_like_evaluative_turn(self, normalized_input: str) -> bool:
        text = " ".join(str(normalized_input or "").strip().lower().split())
        if not text:
            return False
        markers = (
            "you sound dumb",
            "you are dumb",
            "you so stupid",
            "still feels dumb",
            "this feels dumb",
            "this feels weird",
            "you sound weird",
            "why are you acting like this",
            "how are you acting",
            "not a dumb",
            "not dumb anymore",
            "dumbs anymore",
            "bot-grade",
        )
        return any(marker in text for marker in markers)

    def _date_time_fast_path(self, normalized_input: str, *, source_surface: str) -> str | None:
        if source_surface not in {"channel", "openclaw", "api"}:
            return None
        phrase = str(normalized_input or "").strip().lower()
        if not phrase:
            return None
        cleaned = phrase.strip(" \t\r\n?!.,")
        asks_date = any(
            marker in cleaned
            for marker in (
                "what is the date today",
                "what's the date today",
                "what is todays date",
                "what's today's date",
                "what day is it",
                "what day is it today",
                "what day is today",
                "what is the day today",
                "what's the day today",
                "what day today",
                "date today",
                "today's date",
                "day today",
            )
        )
        asks_time = any(
            marker in cleaned
            for marker in (
                "what time is it",
                "what's the time",
                "current time",
                "time now",
            )
        )
        if not asks_date and not asks_time:
            return None
        now = datetime.now().astimezone()
        if asks_date and asks_time:
            return now.strftime("Today is %A, %Y-%m-%d. Current time is %H:%M %Z.")
        if asks_date:
            return now.strftime("Today is %A, %Y-%m-%d.")
        return now.strftime("Current time is %H:%M %Z.")

    def _ui_command_fast_path(self, normalized_input: str, *, source_surface: str) -> str | None:
        phrase = str(normalized_input or "").strip().lower()
        if not phrase.startswith("/"):
            return None
        if phrase in {"/new", "/new-session", "/new_session", "/clear", "/reset"}:
            return "Use the OpenClaw `New session` button on the lower right. Slash `/new` is not a wired command in this runtime."
        if phrase in {"/trace", "/rail", "/task-rail"}:
            return "Open the live trace rail at `http://127.0.0.1:11435/trace`."
        return "That slash command is not wired here. Use plain language, the `New session` button, or open `http://127.0.0.1:11435/trace` for the runtime rail."

    def _startup_sequence_fast_path(self, user_input: str) -> str | None:
        normalized = " ".join(str(user_input or "").strip().lower().split())
        if not normalized:
            return None
        if "new session was started" not in normalized:
            return None
        if "session startup sequence" not in normalized:
            return None
        return f"I’m {get_agent_display_name()}. New session is clean and I’m ready. What do you want to do?"

    def _credit_status_fast_path(self, normalized_input: str, *, source_surface: str) -> str | None:
        if source_surface not in {"channel", "openclaw", "api"}:
            return None
        phrase = str(normalized_input or "").strip().lower()
        if not phrase:
            return None
        credit_markers = (
            "credit",
            "credits",
            "credit balance",
            "compute credits",
            "provider score",
            "validator score",
            "trust score",
            "wallet balance",
            "dna wallet",
        )
        if not any(marker in phrase for marker in credit_markers):
            return None
        return self._render_credit_status(phrase)

    def _maybe_handle_live_info_fast_path(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        interpretation: Any,
    ) -> dict[str, Any] | None:
        live_mode = self._live_info_mode(user_input, interpretation=interpretation)
        if not live_mode:
            return None
        if not policy_engine.allow_web_fallback():
            return self._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response="Live web lookup is disabled on this runtime, so I can't answer current weather or latest-news requests honestly.",
                confidence=0.82,
                source_context=source_context,
                reason="live_info_fast_path",
            )

        query = self._normalize_live_info_query(user_input, mode=live_mode)
        try:
            notes = self._live_info_search_notes(
                query=query,
                live_mode=live_mode,
                interpretation=interpretation,
            )
            if not notes and query != str(user_input or "").strip():
                notes = self._live_info_search_notes(
                    query=str(user_input or "").strip(),
                    live_mode=live_mode,
                    interpretation=interpretation,
                )
        except Exception as exc:
            audit_logger.log(
                "agent_live_info_fast_path_error",
                target_id=session_id,
                target_type="session",
                details={"error": str(exc), "query": query, "mode": live_mode},
            )
            notes = []
        if not notes and live_mode == "fresh_lookup":
            return None
        response = (
            self._render_live_info_response(query=query, notes=notes, mode=live_mode)
            if notes
            else self._live_info_failure_text(query=query, mode=live_mode)
        )
        return self._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.86 if notes else 0.52,
            source_context=source_context,
            reason="live_info_fast_path",
        )

    def _live_info_search_notes(
        self,
        *,
        query: str,
        live_mode: str,
        interpretation: Any,
    ) -> list[dict[str, Any]]:
        topic_hints = [str(item).strip().lower() for item in getattr(interpretation, "topic_hints", []) or [] if str(item).strip()]
        if live_mode == "weather":
            return WebAdapter.search_query(
                query,
                limit=3,
                source_label="duckduckgo.com",
            )
        if live_mode == "news":
            return WebAdapter.planned_search_query(
                query,
                limit=3,
                task_class="research",
                topic_kind="news",
                topic_hints=topic_hints,
                source_label="duckduckgo.com",
            )
        return WebAdapter.planned_search_query(
            query,
            limit=3,
            task_class="research",
            topic_hints=topic_hints,
            source_label="duckduckgo.com",
        )

    def _live_info_mode(self, text: str, *, interpretation: Any) -> str:
        lowered = " ".join(str(text or "").strip().lower().split())
        if not lowered:
            return ""
        if self._looks_like_builder_request(lowered):
            return ""
        if any(
            marker in lowered
            for marker in (
                "what day is it",
                "what day is today",
                "what is the day today",
                "today's date",
                "date today",
                "what time is it",
                "what's the time",
                "time now",
            )
        ):
            return ""
        weather_markers = (
            "weather",
            "forecast",
            "temperature",
            "rain",
            "snow",
            "wind",
            "humidity",
            "humid",
            "sunrise",
            "sunset",
        )
        news_markers = (
            "latest news",
            "breaking news",
            "headlines",
            "headline",
            "news on",
            "news about",
            "what happened today",
        )
        if any(marker in lowered for marker in weather_markers):
            return "weather"
        if any(marker in lowered for marker in news_markers):
            return "news"
        if any(
            marker in lowered
            for marker in (
                "look up",
                "check online",
                "search online",
                "browse",
            )
        ):
            return "fresh_lookup"
        if any(
            marker in lowered
            for marker in (
                "release notes",
                "changelog",
                "latest update",
                "latest updates",
                "current version",
                "latest version",
                "status page",
                "current price",
                "price now",
                "exchange rate",
            )
        ):
            return "fresh_lookup"
        if any(marker in lowered for marker in ("latest", "newest", "recent", "just released")) and any(
            marker in lowered
            for marker in (
                "api",
                "sdk",
                "library",
                "package",
                "release",
                "version",
                "bot",
                "telegram",
                "discord",
                "model",
                "framework",
                "price",
                "stock",
            )
        ):
            return "fresh_lookup"
        hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
        if "weather" in hints:
            return "weather"
        if "news" in hints:
            return "news"
        if "web" in hints and self._wants_fresh_info(lowered, interpretation=interpretation):
            return "fresh_lookup"
        return ""

    def _looks_like_builder_request(self, lowered: str) -> bool:
        text = " ".join(str(lowered or "").split()).strip().lower()
        if not text:
            return False
        build_markers = (
            "build",
            "create",
            "scaffold",
            "implement",
            "generate",
            "start working",
            "write the files",
            "create the files",
            "generate the code",
        )
        design_markers = (
            "design",
            "architecture",
            "best practice",
            "best practices",
            "framework",
            "stack",
        )
        source_markers = (
            "github",
            "repo",
            "repos",
            "docs",
            "documentation",
            "official docs",
        )
        return (
            any(marker in text for marker in build_markers)
            or (
                any(marker in text for marker in design_markers)
                and any(marker in text for marker in source_markers)
            )
        )

    def _normalize_live_info_query(self, text: str, *, mode: str) -> str:
        clean = " ".join(str(text or "").split()).strip()
        lowered = clean.lower()
        if mode == "weather" and "forecast" not in lowered and "weather" in lowered:
            return f"{clean} forecast"
        if mode == "news" and "latest" not in lowered and "news" in lowered:
            return f"latest {clean}"
        return clean

    def _render_live_info_response(self, *, query: str, notes: list[dict[str, Any]], mode: str) -> str:
        label = {
            "weather": "Live weather results",
            "news": "Live news results",
            "fresh_lookup": "Live web results",
        }.get(mode, "Live web results")
        lines = [f"{label} for `{query}`:"]
        browser_used = False
        for note in list(notes or [])[:3]:
            title = str(note.get("result_title") or note.get("origin_domain") or "Source").strip()
            domain = str(note.get("origin_domain") or "").strip()
            snippet = " ".join(str(note.get("summary") or "").split()).strip()
            url = str(note.get("result_url") or "").strip()
            line = f"- {title}"
            if domain and domain.lower() not in title.lower():
                line += f" ({domain})"
            if snippet:
                line += f": {snippet[:220]}"
            if url:
                line += f" [{url}]"
            lines.append(line)
            browser_used = browser_used or bool(note.get("used_browser"))
        if browser_used:
            lines.append("Browser rendering was used for at least one source when plain fetch was too thin.")
        return "\n".join(lines)

    def _live_info_failure_text(self, *, query: str, mode: str) -> str:
        if mode == "weather":
            return f'I tried the live web lane for "{query}", but no current weather results came back.'
        if mode == "news":
            return f'I tried the live web lane for "{query}", but no current news results came back.'
        return f'I tried the live web lane for "{query}", but no grounded live results came back.'

    def _help_capabilities_text(self) -> str:
        available_tools = {
            str(tool.get("tool_id") or "").strip()
            for tool in list_operator_tools()
            if tool.get("available")
        }
        capabilities = ["local reasoning", "persistent memory", "mesh-assisted lookups"]
        if policy_engine.allow_web_fallback():
            capabilities.append("live web research when retrieval returns real results")
        if policy_engine.get("filesystem.allow_read_workspace", True):
            capabilities.append("workspace file listing, search, and reads")
        if policy_engine.get("filesystem.allow_write_workspace", False):
            capabilities.append("workspace file edits")
        if policy_engine.get("execution.allow_sandbox_execution", False):
            capabilities.append("sandboxed local commands with network blocked")
        tool_labels: list[str] = []
        if "schedule_calendar_event" in available_tools:
            tool_labels.append("calendar outbox creation")
        if "discord_post" in available_tools:
            tool_labels.append("Discord posting")
        if "telegram_send" in available_tools:
            tool_labels.append("Telegram sending")
        if "inspect_disk_usage" in available_tools:
            tool_labels.append("disk inspection")
        if "inspect_processes" in available_tools:
            tool_labels.append("process inspection")
        if "inspect_services" in available_tools:
            tool_labels.append("service inspection")
        if "cleanup_temp_files" in available_tools:
            tool_labels.append("temp cleanup")
        if "move_path" in available_tools:
            tool_labels.append("file move/archive")
        if tool_labels:
            capabilities.append("wired tools: " + ", ".join(tool_labels))
        capabilities.append("real step reporting for executed tools, approval previews, and failures")
        capabilities.append("I will say it directly when a tool is not actually wired on this runtime")
        return "I can handle " + ", ".join(capabilities) + "."

    def _render_credit_status(self, normalized_input: str) -> str:
        from core.credit_ledger import reconcile_ledger
        from core.dna_wallet_manager import DNAWalletManager
        from core.scoreboard_engine import get_peer_scoreboard
        from network.signer import get_local_peer_id

        peer_id = get_local_peer_id()
        ledger = reconcile_ledger(peer_id)
        scoreboard = get_peer_scoreboard(peer_id)
        wallet_status = DNAWalletManager().get_status()
        mention_wallet = any(token in normalized_input for token in ("wallet", "usdc", "dna"))
        mention_rewards = any(token in normalized_input for token in ("earn", "earned", "reward", "share", "hive", "task"))

        parts = [
            f"You currently have {ledger.balance:.2f} compute credits.",
            (
                f"Provider score {scoreboard.provider:.1f}, validator score {scoreboard.validator:.1f}, "
                f"trust {scoreboard.trust:.1f}, tier {scoreboard.tier}."
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

    def _maybe_run_workspace_build_pipeline(
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
        source_context = dict(source_context or {})
        if not self._should_run_workspace_build_pipeline(
            effective_input=effective_input,
            classification=classification,
            source_context=source_context,
        ):
            return None

        target = self._workspace_build_target(
            query_text=effective_input,
            interpretation=interpretation,
        )
        file_map = self._workspace_build_file_map(
            target=target,
            user_request=effective_input,
            web_notes=web_notes,
        )
        if not file_map:
            return None

        write_results: list[dict[str, Any]] = []
        write_failures: list[str] = []
        for path, content in file_map.items():
            execution = execute_runtime_tool(
                "workspace.write_file",
                {"path": path, "content": content},
                source_context=source_context,
            )
            if execution is None or not execution.ok:
                write_failures.append(str((execution.response_text if execution else f"Failed to write {path}") or "").strip())
                continue
            write_results.append(
                {
                    "path": path,
                    "status": execution.status,
                    "response_text": execution.response_text,
                }
            )

        verification = self._workspace_build_verification(
            target=target,
            source_context=source_context,
        )
        sources = self._workspace_build_sources(web_notes)
        response = self._workspace_build_response(
            target=target,
            write_results=write_results,
            write_failures=write_failures,
            verification=verification,
            sources=sources,
        )
        workflow_summary = (
            f"- workspace build pipeline: {target['platform']} {target['language']} scaffold\n"
            f"- generated under `{target['root_dir']}`\n"
            f"- source-backed notes used: {len(sources)}\n"
            f"- files written: {len(write_results)}"
        )
        return self._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=response,
            confidence=0.88 if write_results else 0.52,
            source_context=source_context,
            reason="workspace_build_pipeline",
            success=bool(write_results) and not write_failures,
            details={
                "workspace_build_target": target,
                "written_files": [item["path"] for item in write_results],
                "verification_status": str((verification or {}).get("status") or "skipped"),
                "verification_ok": bool((verification or {}).get("ok", False)),
            },
            mode_override="tool_executed" if write_results else "tool_failed",
            task_outcome="success" if write_results else "failed",
            workflow_summary=workflow_summary,
        )

    def _should_run_workspace_build_pipeline(
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
        if task_class not in {"system_design", "integration_orchestration"}:
            return False
        lowered = str(effective_input or "").lower()
        if not any(marker in lowered for marker in ("build", "create", "scaffold", "implement", "generate", "start working", "write the files")):
            return False
        if not any(marker in lowered for marker in ("telegram", "discord", "bot", "agent", "service")):
            return False
        if any(marker in lowered for marker in ("don't write", "do not write", "advice only", "just plan", "no files")):
            return False
        if not (
            "workspace" in lowered
            or "write the files" in lowered
            or "create the files" in lowered
            or "generate the code" in lowered
            or "start working" in lowered
            or "implement it" in lowered
        ):
            return False
        return True

    def _workspace_build_target(self, *, query_text: str, interpretation: Any) -> dict[str, str]:
        lowered = str(query_text or "").lower()
        topic_hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
        platform = "generic"
        if "discord" in lowered or "discord bot" in topic_hints:
            platform = "discord"
        elif "telegram" in lowered or "tg bot" in lowered or "telegram bot" in topic_hints:
            platform = "telegram"

        heuristic_hits = search_user_heuristics(
            query_text,
            topic_hints=list(topic_hints),
            limit=4,
        )
        preferred_stacks = [
            str(item.get("signal") or "").strip().lower()
            for item in heuristic_hits
            if str(item.get("category") or "") == "preferred_stack"
        ]
        if "python" in lowered:
            language = "python"
        elif "typescript" in lowered or "node" in lowered or "javascript" in lowered:
            language = "typescript"
        elif preferred_stacks and preferred_stacks[0] in {"typescript", "javascript"}:
            language = "typescript"
        else:
            language = "python"

        slug = f"{platform}-bot" if platform in {"telegram", "discord"} else "build-brief"
        return {
            "platform": platform,
            "language": language,
            "root_dir": f"generated/{slug}",
        }

    def _workspace_build_file_map(
        self,
        *,
        target: dict[str, str],
        user_request: str,
        web_notes: list[dict[str, Any]],
    ) -> dict[str, str]:
        platform = str(target.get("platform") or "generic")
        language = str(target.get("language") or "python")
        root_dir = str(target.get("root_dir") or "generated/build-brief").rstrip("/")
        sources = self._workspace_build_sources(web_notes)

        if platform == "telegram" and language == "python":
            return {
                f"{root_dir}/README.md": self._telegram_python_readme(user_request=user_request, root_dir=root_dir, sources=sources),
                f"{root_dir}/requirements.txt": "python-telegram-bot>=22.0,<23.0\n",
                f"{root_dir}/.env.example": "TELEGRAM_BOT_TOKEN=replace-me\nBOT_NAME=NULLA Local Bot\n",
                f"{root_dir}/src/bot.py": self._telegram_python_bot_source(sources=sources),
            }
        if platform == "telegram" and language == "typescript":
            return {
                f"{root_dir}/README.md": self._telegram_typescript_readme(user_request=user_request, root_dir=root_dir, sources=sources),
                f"{root_dir}/package.json": self._telegram_typescript_package_json(),
                f"{root_dir}/tsconfig.json": self._telegram_typescript_tsconfig(),
                f"{root_dir}/.env.example": "TELEGRAM_BOT_TOKEN=replace-me\nBOT_NAME=NULLA Local Bot\n",
                f"{root_dir}/src/bot.ts": self._telegram_typescript_bot_source(sources=sources),
            }
        if platform == "discord" and language == "python":
            return {
                f"{root_dir}/README.md": self._discord_python_readme(user_request=user_request, root_dir=root_dir, sources=sources),
                f"{root_dir}/requirements.txt": "discord.py>=2.5,<3.0\n",
                f"{root_dir}/.env.example": "DISCORD_BOT_TOKEN=replace-me\n",
                f"{root_dir}/src/bot.py": self._discord_python_bot_source(sources=sources),
            }
        if platform == "discord" and language == "typescript":
            return {
                f"{root_dir}/README.md": self._discord_typescript_readme(user_request=user_request, root_dir=root_dir, sources=sources),
                f"{root_dir}/package.json": self._discord_typescript_package_json(),
                f"{root_dir}/tsconfig.json": self._telegram_typescript_tsconfig(),
                f"{root_dir}/.env.example": "DISCORD_BOT_TOKEN=replace-me\n",
                f"{root_dir}/src/bot.ts": self._discord_typescript_bot_source(sources=sources),
            }
        brief = self._generic_build_brief(user_request=user_request, root_dir=root_dir, sources=sources)
        return {f"{root_dir}/README.md": brief}

    def _workspace_build_sources(self, web_notes: list[dict[str, Any]]) -> list[dict[str, str]]:
        selected: list[dict[str, str]] = []
        seen: set[str] = set()
        for note in list(web_notes or [])[:4]:
            url = str(note.get("result_url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            selected.append(
                {
                    "title": str(note.get("result_title") or note.get("origin_domain") or "Source").strip(),
                    "url": url,
                    "label": str(note.get("source_profile_label") or note.get("origin_domain") or "").strip(),
                }
            )
        return selected

    def _workspace_build_verification(
        self,
        *,
        target: dict[str, str],
        source_context: dict[str, object],
    ) -> dict[str, Any] | None:
        language = str(target.get("language") or "")
        root_dir = str(target.get("root_dir") or "").rstrip("/")
        if language != "python" or not root_dir:
            return {"status": "skipped", "ok": False, "response_text": "Verification skipped for non-Python scaffold."}
        execution = execute_runtime_tool(
            "sandbox.run_command",
            {"command": f"python3 -m compileall -q {root_dir}/src"},
            source_context=source_context,
        )
        if execution is None:
            return {"status": "not_run", "ok": False, "response_text": "Verification did not run."}
        return {
            "status": execution.status,
            "ok": execution.ok,
            "response_text": execution.response_text,
            "details": dict(execution.details),
        }

    def _workspace_build_response(
        self,
        *,
        target: dict[str, str],
        write_results: list[dict[str, Any]],
        write_failures: list[str],
        verification: dict[str, Any] | None,
        sources: list[dict[str, str]],
    ) -> str:
        lines = [
            f"Wrote a {target['platform']} {target['language']} scaffold under `{target['root_dir']}`."
            if target["platform"] != "generic"
            else f"Wrote a researched build brief under `{target['root_dir']}`."
        ]
        if write_results:
            lines.append("Files written:")
            lines.extend(f"- {item['path']}" for item in write_results[:8])
        if sources:
            lines.append("Sources used:")
            lines.extend(f"- {item['title']} [{item['url']}]" for item in sources[:3])
        verification_status = str((verification or {}).get("status") or "")
        verification_text = str((verification or {}).get("response_text") or "").strip()
        if verification_status == "executed":
            lines.append("Verification:")
            lines.append(f"- {verification_text}")
        elif verification_status == "skipped":
            lines.append("Verification skipped for this scaffold type.")
        if write_failures:
            lines.append("Write failures:")
            lines.extend(f"- {item}" for item in write_failures[:4])
        return "\n".join(lines)

    def _sources_section(self, sources: list[dict[str, str]]) -> str:
        if not sources:
            return "- No live sources were captured in this run.\n"
        return "\n".join(f"- {item['title']}: {item['url']}" for item in sources[:4]) + "\n"

    def _telegram_python_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return (
            "# Telegram Bot Scaffold\n\n"
            "Local-first Telegram bot scaffold generated from the current research lane.\n\n"
            "## Why This Shape\n\n"
            "- Keep the first pass small, editable, and runnable on a local machine.\n"
            "- Anchor protocol details on Telegram's official docs instead of generic blog spam.\n"
            "- Keep implementation references visible in the repo instead of hiding them in chat history.\n\n"
            "## Request\n\n"
            f"- {user_request.strip()}\n\n"
            "## Sources\n\n"
            f"{self._sources_section(sources)}\n"
            "## Files\n\n"
            "- `src/bot.py`: minimal command + message handlers.\n"
            "- `.env.example`: environment variables for local runs.\n"
            "- `requirements.txt`: first-pass Python dependencies.\n\n"
            "## Run\n\n"
            "1. Create a virtualenv.\n"
            "2. Install `requirements.txt`.\n"
            "3. Export `TELEGRAM_BOT_TOKEN`.\n"
            f"4. Run `python {root_dir}/src/bot.py`.\n"
        )

    def _telegram_python_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        source_lines = "\n".join(f"# - {item['title']}: {item['url']}" for item in sources[:4]) or "# - No live sources captured in this run."
        return (
            '"""Telegram bot scaffold.\n\n'
            "Source references:\n"
            f"{source_lines}\n"
            '"""\n\n'
            "from __future__ import annotations\n\n"
            "import logging\n"
            "import os\n"
            "from typing import Final\n\n"
            "from telegram import Update\n"
            "from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters\n\n"
            'TOKEN_ENV: Final = "TELEGRAM_BOT_TOKEN"\n'
            'DEFAULT_REPLY: Final = "NULLA local scaffold is online."\n\n'
            "logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')\n\n"
            "async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n"
            '    await update.effective_message.reply_text("NULLA scaffold is live. Use /help for commands.")\n\n'
            "async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n"
            '    await update.effective_message.reply_text("Commands: /start, /help. Everything else echoes for now.")\n\n'
            "async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n"
            "    if update.effective_message is None or not update.effective_message.text:\n"
            "        return\n"
            '    await update.effective_message.reply_text(f"{DEFAULT_REPLY}\\n\\nYou said: {update.effective_message.text}")\n\n'
            "def build_application(token: str) -> Application:\n"
            "    app = ApplicationBuilder().token(token).build()\n"
            '    app.add_handler(CommandHandler("start", start))\n'
            '    app.add_handler(CommandHandler("help", help_command))\n'
            "    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))\n"
            "    return app\n\n"
            "def main() -> None:\n"
            "    token = os.getenv(TOKEN_ENV, '').strip()\n"
            "    if not token:\n"
            '        raise SystemExit("Set TELEGRAM_BOT_TOKEN before running the scaffold.")\n'
            "    app = build_application(token)\n"
            "    app.run_polling(allowed_updates=Update.ALL_TYPES)\n\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        )

    def _telegram_typescript_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return (
            "# Telegram Bot Scaffold (TypeScript)\n\n"
            "TypeScript-first Telegram scaffold generated from the research lane.\n\n"
            "## Request\n\n"
            f"- {user_request.strip()}\n\n"
            "## Sources\n\n"
            f"{self._sources_section(sources)}\n"
            "## Run\n\n"
            "1. Install dependencies with `npm install`.\n"
            "2. Copy `.env.example` to `.env`.\n"
            f"3. Run `npm run dev --prefix {root_dir}`.\n"
        )

    def _telegram_typescript_package_json(self) -> str:
        return (
            "{\n"
            '  "name": "nulla-telegram-bot-scaffold",\n'
            '  "private": true,\n'
            '  "type": "module",\n'
            '  "scripts": {\n'
            '    "dev": "tsx src/bot.ts"\n'
            "  },\n"
            '  "dependencies": {\n'
            '    "dotenv": "^16.4.5",\n'
            '    "grammy": "^1.32.0"\n'
            "  },\n"
            '  "devDependencies": {\n'
            '    "tsx": "^4.19.2",\n'
            '    "typescript": "^5.7.3"\n'
            "  }\n"
            "}\n"
        )

    def _telegram_typescript_tsconfig(self) -> str:
        return (
            "{\n"
            '  "compilerOptions": {\n'
            '    "target": "ES2022",\n'
            '    "module": "NodeNext",\n'
            '    "moduleResolution": "NodeNext",\n'
            '    "strict": true,\n'
            '    "esModuleInterop": true,\n'
            '    "skipLibCheck": true,\n'
            '    "outDir": "dist"\n'
            "  },\n"
            '  "include": ["src/**/*.ts"]\n'
            "}\n"
        )

    def _telegram_typescript_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        source_lines = "\n".join(f"// - {item['title']}: {item['url']}" for item in sources[:4]) or "// - No live sources captured in this run."
        return (
            "// Telegram bot scaffold.\n"
            "// Source references:\n"
            f"{source_lines}\n\n"
            'import "dotenv/config";\n'
            'import { Bot } from "grammy";\n\n'
            'const token = process.env.TELEGRAM_BOT_TOKEN?.trim();\n'
            "if (!token) {\n"
            '  throw new Error("Set TELEGRAM_BOT_TOKEN before running the scaffold.");\n'
            "}\n\n"
            'const bot = new Bot(token);\n\n'
            'bot.command("start", (ctx) => ctx.reply("NULLA TypeScript scaffold is live."));\n'
            'bot.command("help", (ctx) => ctx.reply("Commands: /start, /help."));\n'
            'bot.on("message:text", (ctx) => ctx.reply(`NULLA local scaffold heard: ${ctx.message.text}`));\n\n'
            "bot.start();\n"
        )

    def _discord_python_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return (
            "# Discord Bot Scaffold\n\n"
            "Python Discord scaffold generated from the research lane.\n\n"
            "## Request\n\n"
            f"- {user_request.strip()}\n\n"
            "## Sources\n\n"
            f"{self._sources_section(sources)}\n"
            "## Run\n\n"
            f"1. Install `requirements.txt`.\n2. Export `DISCORD_BOT_TOKEN`.\n3. Run `python {root_dir}/src/bot.py`.\n"
        )

    def _discord_python_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        source_lines = "\n".join(f"# - {item['title']}: {item['url']}" for item in sources[:4]) or "# - No live sources captured in this run."
        return (
            '"""Discord bot scaffold.\n\n'
            "Source references:\n"
            f"{source_lines}\n"
            '"""\n\n'
            "from __future__ import annotations\n\n"
            "import os\n\n"
            "import discord\n\n"
            'TOKEN_ENV = "DISCORD_BOT_TOKEN"\n\n'
            "intents = discord.Intents.default()\n"
            "intents.message_content = True\n"
            "client = discord.Client(intents=intents)\n\n"
            "@client.event\n"
            "async def on_ready() -> None:\n"
            '    print(f"Logged in as {client.user}")\n\n'
            "@client.event\n"
            "async def on_message(message: discord.Message) -> None:\n"
            "    if message.author == client.user:\n"
            "        return\n"
            '    if message.content.startswith("!ping"):\n'
            '        await message.channel.send("pong")\n\n'
            "def main() -> None:\n"
            "    token = os.getenv(TOKEN_ENV, '').strip()\n"
            "    if not token:\n"
            '        raise SystemExit("Set DISCORD_BOT_TOKEN before running the scaffold.")\n'
            "    client.run(token)\n\n"
            'if __name__ == "__main__":\n'
            "    main()\n"
        )

    def _discord_typescript_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return (
            "# Discord Bot Scaffold (TypeScript)\n\n"
            "TypeScript Discord scaffold generated from the research lane.\n\n"
            "## Request\n\n"
            f"- {user_request.strip()}\n\n"
            "## Sources\n\n"
            f"{self._sources_section(sources)}\n"
            "## Run\n\n"
            f"1. Install dependencies.\n2. Copy `.env.example` to `.env`.\n3. Run `npm run dev --prefix {root_dir}`.\n"
        )

    def _discord_typescript_package_json(self) -> str:
        return (
            "{\n"
            '  "name": "nulla-discord-bot-scaffold",\n'
            '  "private": true,\n'
            '  "type": "module",\n'
            '  "scripts": {\n'
            '    "dev": "tsx src/bot.ts"\n'
            "  },\n"
            '  "dependencies": {\n'
            '    "discord.js": "^14.18.0",\n'
            '    "dotenv": "^16.4.5"\n'
            "  },\n"
            '  "devDependencies": {\n'
            '    "tsx": "^4.19.2",\n'
            '    "typescript": "^5.7.3"\n'
            "  }\n"
            "}\n"
        )

    def _discord_typescript_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        source_lines = "\n".join(f"// - {item['title']}: {item['url']}" for item in sources[:4]) or "// - No live sources captured in this run."
        return (
            "// Discord bot scaffold.\n"
            "// Source references:\n"
            f"{source_lines}\n\n"
            'import "dotenv/config";\n'
            'import { Client, GatewayIntentBits } from "discord.js";\n\n'
            'const token = process.env.DISCORD_BOT_TOKEN?.trim();\n'
            "if (!token) {\n"
            '  throw new Error("Set DISCORD_BOT_TOKEN before running the scaffold.");\n'
            "}\n\n"
            "const client = new Client({\n"
            "  intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent],\n"
            "});\n\n"
            'client.once("ready", () => {\n'
            '  console.log(`Logged in as ${client.user?.tag ?? "unknown-user"}`);\n'
            "});\n\n"
            'client.on("messageCreate", async (message) => {\n'
            "  if (message.author.bot) {\n"
            "    return;\n"
            "  }\n"
            '  if (message.content === "!ping") {\n'
            '    await message.reply("pong");\n'
            "  }\n"
            "});\n\n"
            "client.login(token);\n"
        )

    def _generic_build_brief(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return (
            "# Generated Build Brief\n\n"
            "A code scaffold was not generated because the request did not match a supported bot scaffold yet.\n\n"
            "## Request\n\n"
            f"- {user_request.strip()}\n\n"
            "## Sources\n\n"
            f"{self._sources_section(sources)}\n"
            "## Next Moves\n\n"
            "- Lock the target runtime and language.\n"
            "- Confirm the delivery interface.\n"
            "- Generate a more specific scaffold on the next turn.\n"
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
        if not should_attempt_tool_intent(
            effective_input,
            task_class=str(classification.get("task_class", "unknown")),
            source_context=source_context,
        ):
            return None
        checkpoint_id = self._runtime_checkpoint_id(source_context)
        checkpoint = get_runtime_checkpoint(checkpoint_id) if checkpoint_id else None
        checkpoint_state = dict((checkpoint or {}).get("state") or {})
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
                tool_name=execution.tool_name or "",
                response_text=execution.response_text,
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
        lowered = (text or "").lower()
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
    ) -> ChatTurnResult:
        return ChatTurnResult(
            text=str(text or "").strip(),
            response_class=response_class,
            workflow_summary=str(workflow_summary or "").strip(),
            debug_origin=debug_origin,
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
        result = response if isinstance(response, ChatTurnResult) else self._turn_result(
            str(response or ""),
            ResponseClass.GENERIC_CONVERSATION,
            workflow_summary=workflow_summary,
        )
        clean_text = self._shape_user_facing_text(result)
        if self._should_show_workflow_for_result(result, source_context=source_context):
            decorated = self._maybe_attach_workflow(
                clean_text,
                result.workflow_summary,
                source_context=source_context,
            )
        else:
            decorated = clean_text
        footer_allowed = self._should_attach_hive_footer(result, source_context=source_context) if include_hive_footer is None else bool(include_hive_footer)
        hive_footer = self._maybe_hive_footer(session_id=session_id, source_context=source_context) if footer_allowed else ""
        if hive_footer:
            decorated = self._append_footer(decorated, prefix="Hive", footer=hive_footer)
        return decorated

    def _shape_user_facing_text(self, result: ChatTurnResult) -> str:
        text = self._sanitize_user_chat_text(
            result.text,
            response_class=result.response_class,
        )
        if result.response_class == ResponseClass.TASK_STARTED:
            text = re.sub(
                r"^Autonomous research on\s+`?([^`]+)`?\s+packed\s+\d+\s+research queries,\s*\d+\s+candidate notes,\s*and\s*\d+\s+gate decisions\.?",
                r"Started Hive research on `\1`. First bounded pass is underway.",
                text,
                flags=re.IGNORECASE,
            )
            text = text.replace(
                "The first bounded research pass already ran and posted its result.",
                "The first bounded pass already landed.",
            )
            text = text.replace(
                "This fast reply only means the first bounded research pass finished.",
                "The first bounded pass finished.",
            )
            text = text.replace(
                "Topic stays `researching` because NULLA still needs more evidence before it can honestly mark the task solved.",
                "It is still open because the solve threshold was not met yet.",
            )
            text = text.replace(
                "The research lane is active.",
                "First bounded pass is underway.",
            )
            text = re.sub(r"\bBounded queries run:\s*\d+\.\s*", "", text)
            text = re.sub(r"\bArtifacts packed:\s*\d+\.\s*", "", text)
            text = re.sub(r"\bCandidate notes:\s*\d+\.\s*", "", text)
            return " ".join(text.split()).strip()
        if result.response_class == ResponseClass.RESEARCH_PROGRESS:
            text = re.sub(r"^Research follow-up:\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"^Research result:\s*", "Here’s what I found: ", text, flags=re.IGNORECASE)
            return " ".join(text.split()).strip()
        return text

    def _should_show_workflow_for_result(
        self,
        result: ChatTurnResult,
        *,
        source_context: dict[str, object] | None,
    ) -> bool:
        if result.response_class in {
            ResponseClass.SMALLTALK,
            ResponseClass.UTILITY_ANSWER,
            ResponseClass.GENERIC_CONVERSATION,
            ResponseClass.TASK_FAILED_USER_SAFE,
            ResponseClass.SYSTEM_ERROR_USER_SAFE,
            ResponseClass.TASK_STARTED,
            ResponseClass.RESEARCH_PROGRESS,
        }:
            return False
        return self._should_show_workflow_summary(
            response=result.text,
            workflow_summary=result.workflow_summary,
            source_context=source_context,
        )

    def _sanitize_user_chat_text(self, text: str, *, response_class: ResponseClass) -> str:
        base_text = str(text or "").strip()
        sanitized = self._strip_runtime_preamble(base_text)
        lowered = sanitized.lower()
        forbidden = (
            "invalid tool payload",
            "missing_intent",
            "i won't fake it",
        )
        if any(marker in lowered for marker in forbidden):
            if response_class == ResponseClass.UTILITY_ANSWER:
                return "I couldn't answer that utility request cleanly."
            if response_class in {ResponseClass.TASK_FAILED_USER_SAFE, ResponseClass.SYSTEM_ERROR_USER_SAFE}:
                return "I couldn't map that cleanly to a real action."
            return "I couldn't resolve that cleanly."
        return sanitized

    def _strip_runtime_preamble(self, text: str) -> str:
        clean = str(text or "").strip()
        if not clean.startswith("Real steps completed:"):
            return clean
        parts = clean.split("\n\n", 1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()
        return "I couldn't resolve that cleanly."

    def _should_attach_hive_footer(
        self,
        result: ChatTurnResult,
        *,
        source_context: dict[str, object] | None,
    ) -> bool:
        surface = str((source_context or {}).get("surface", "") or "").strip().lower()
        if surface not in {"channel", "openclaw", "api"}:
            return False
        return result.response_class in {
            ResponseClass.TASK_SELECTION_CLARIFICATION,
            ResponseClass.APPROVAL_REQUIRED,
        }

    def _fast_path_response_class(self, *, reason: str, response: str) -> ResponseClass:
        if reason in {"smalltalk_fast_path", "startup_sequence_fast_path"}:
            return ResponseClass.SMALLTALK
        if reason in {
            "date_time_fast_path",
            "ui_command_fast_path",
            "credit_status_fast_path",
            "memory_command",
            "user_preference_command",
            "live_info_fast_path",
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
        if lowered.startswith("i couldn't reach the hive watcher") or lowered.startswith("i couldn't reach hive") or lowered.startswith("public hive is not enabled"):
            return ResponseClass.TASK_FAILED_USER_SAFE
        if lowered.startswith("available hive tasks right now"):
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
        if lowered.startswith("started hive research on") or lowered.startswith("autonomous research on"):
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
        if not session_id:
            return
        state = session_hive_state(session_id)
        payload = dict(state.get("interaction_payload") or {})
        preserve_task_context = bool(
            str(state.get("interaction_mode") or "") in {
                "hive_nudge_shown",
                "hive_task_selection_pending",
                "hive_task_active",
                "hive_task_status_pending",
            }
            and (
                payload.get("active_topic_id")
                or self._interaction_pending_topic_ids(state)
                or list(state.get("pending_topic_ids") or [])
            )
        )
        if result.response_class == ResponseClass.SMALLTALK:
            if preserve_task_context:
                return
            set_hive_interaction_state(session_id, mode="smalltalk", payload={})
            return
        if result.response_class == ResponseClass.UTILITY_ANSWER:
            if preserve_task_context:
                return
            set_hive_interaction_state(session_id, mode="utility", payload={})
            return
        if result.response_class == ResponseClass.GENERIC_CONVERSATION:
            if preserve_task_context:
                return
            set_hive_interaction_state(session_id, mode="generic_conversation", payload={})
            return
        if result.response_class in {ResponseClass.SYSTEM_ERROR_USER_SAFE, ResponseClass.TASK_FAILED_USER_SAFE}:
            set_hive_interaction_state(session_id, mode="error_recovery", payload={})
            return
        if result.response_class in {ResponseClass.TASK_LIST, ResponseClass.TASK_SELECTION_CLARIFICATION}:
            set_hive_interaction_state(session_id, mode="hive_task_selection_pending", payload=payload)
            return
        if result.response_class == ResponseClass.TASK_STARTED:
            set_hive_interaction_state(session_id, mode="hive_task_active", payload=payload)
            return
        if result.response_class == ResponseClass.TASK_STATUS:
            set_hive_interaction_state(session_id, mode="hive_task_status_pending", payload=payload)

    def _maybe_handle_hive_runtime_command(
        self,
        user_input: str,
        *,
        session_id: str,
    ) -> tuple[bool, str]:
        handled, response = self.hive_activity_tracker.maybe_handle_command(user_input, session_id=session_id)
        if not handled:
            return False, ""
        if not self._hive_tracker_needs_bridge_fallback(response):
            return True, response
        bridge_response = self._maybe_handle_hive_bridge_fallback(
            user_input,
            session_id=session_id,
            tracker_response=response,
        )
        if bridge_response is not None:
            return True, bridge_response
        return True, response

    def _hive_tracker_needs_bridge_fallback(self, response: str) -> bool:
        lowered = str(response or "").strip().lower()
        return lowered.startswith("hive watcher is not configured") or lowered.startswith("i couldn't reach the hive watcher")

    def _maybe_handle_hive_bridge_fallback(
        self,
        user_input: str,
        *,
        session_id: str,
        tracker_response: str,
    ) -> str | None:
        if not self.public_hive_bridge.enabled():
            return None
        topics = self.public_hive_bridge.list_public_topics(
            limit=12,
            statuses=("open", "researching", "disputed"),
        )
        if not topics:
            return None
        self._store_hive_topic_selection_state(session_id, topics)
        lowered = " ".join(str(user_input or "").strip().lower().split())
        if "online" in lowered and "task" not in lowered and "tasks" not in lowered and "work" not in lowered:
            lead = "I couldn't read live agent presence from the watcher, but I can still pull public Hive tasks:"
        elif "not configured" in str(tracker_response or "").lower():
            lead = "Live Hive watcher is not configured here, but I can still pull public Hive tasks:"
        else:
            lead = "I couldn't reach the live Hive watcher, but I can still pull public Hive tasks:"
        return self.hive_activity_tracker._render_hive_task_list_with_lead(topics, lead=lead)

    def _store_hive_topic_selection_state(
        self,
        session_id: str,
        topics: list[dict[str, Any]],
    ) -> None:
        state = session_hive_state(session_id)
        topic_ids = [
            str(topic.get("topic_id") or "").strip()
            for topic in list(topics or [])
            if str(topic.get("topic_id") or "").strip()
        ]
        titles = [
            str(topic.get("title") or "").strip()
            for topic in list(topics or [])
            if str(topic.get("title") or "").strip()
        ]
        update_session_hive_state(
            session_id,
            watched_topic_ids=list(state.get("watched_topic_ids") or []),
            seen_post_ids=list(state.get("seen_post_ids") or []),
            pending_topic_ids=topic_ids,
            seen_curiosity_topic_ids=list(state.get("seen_curiosity_topic_ids") or []),
            seen_curiosity_run_ids=list(state.get("seen_curiosity_run_ids") or []),
            seen_agent_ids=state.get("seen_agent_ids") or [],
            last_active_agents=int(state.get("last_active_agents") or 0),
            interaction_mode="hive_task_selection_pending",
            interaction_payload={
                "shown_topic_ids": topic_ids,
                "shown_titles": titles,
            },
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
        if len(response_text) >= 280:
            return True
        return False

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
        lines: list[str] = []
        task_class = str(classification.get("task_class") or "unknown")
        lines.append(f"- classified task as `{task_class}`")
        try:
            retrieval_conf = float(context_result.report.retrieval_confidence)
            lines.append(f"- loaded memory/context with retrieval confidence {retrieval_conf:.2f}")
        except Exception:
            pass
        provider = str((model_execution or {}).get("provider_id") or (model_execution or {}).get("source") or "none")
        used_model = bool((model_execution or {}).get("used_model", True))
        lines.append(f"- {'used' if used_model else 'skipped'} model path via `{provider}`")
        media_reason = str((media_analysis or {}).get("reason") or "").strip()
        if media_reason:
            lines.append(f"- media/web evidence status: `{media_reason}`")
        curiosity_mode = str((curiosity_result or {}).get("mode") or "").strip()
        if curiosity_mode:
            lines.append(f"- curiosity/research lane: `{curiosity_mode}`")
        lines.append(f"- execution posture: `{gate_mode}`")
        return "\n".join(lines)

    def _action_workflow_summary(
        self,
        *,
        operator_kind: str,
        dispatch_status: str,
        details: dict[str, Any] | None,
    ) -> str:
        lines = [f"- recognized operator action `{operator_kind}`", f"- action state: `{dispatch_status}`"]
        info = dict(details or {})
        action_id = str(info.get("action_id") or "").strip()
        if action_id:
            lines.append(f"- action id: `{action_id}`")
        target_path = str(info.get("target_path") or "").strip()
        if target_path:
            lines.append(f"- target: `{target_path}`")
        return "\n".join(lines)

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
        tool_name: str,
        response_text: str,
    ) -> dict[str, Any]:
        updated = dict(source_context or {})
        history = list(updated.get("conversation_history") or [])
        content = str(response_text or "").strip()
        if len(content) > 1800:
            content = content[:1800].rstrip() + "\n...[truncated]"
        history.append(
            {
                "role": "assistant",
                "content": f"Real tool result from `{tool_name or 'tool'}`:\n{content or 'No tool output returned.'}",
            }
        )
        updated["conversation_history"] = history[-12:]
        return updated

    def _tool_loop_final_message(self, synthesis: Any, executed_steps: list[dict[str, Any]]) -> str:
        structured = getattr(synthesis, "structured_output", None)
        if isinstance(structured, dict):
            summary = str(structured.get("summary") or structured.get("message") or "").strip()
            bullet_source = structured.get("bullets") or structured.get("steps") or []
            bullets = [str(item).strip() for item in list(bullet_source) if str(item).strip()]
            if summary and bullets:
                return summary + "\n" + "\n".join(f"- {item}" for item in bullets[:6])
            if summary:
                return summary
        output_text = str(getattr(synthesis, "output_text", "") or "").strip()
        if output_text:
            return output_text
        if executed_steps:
            last_step = executed_steps[-1]
            return (
                f"Completed {len(executed_steps)} real tool step{'s' if len(executed_steps) != 1 else ''}. "
                f"Last result: {str(last_step.get('summary') or 'tool execution finished').strip()}"
            )
        return "I ran the available tools, but I do not have a grounded final synthesis yet."

    def _render_tool_loop_response(
        self,
        *,
        final_message: str,
        executed_steps: list[dict[str, Any]],
        include_step_summary: bool = True,
    ) -> str:
        message = str(final_message or "").strip()
        if not executed_steps or not include_step_summary:
            return message
        lines = ["Real steps completed:"]
        for step in executed_steps:
            tool_name = str(step.get("tool_name") or "tool").strip()
            summary = str(step.get("summary") or step.get("status") or "completed").strip()
            lines.append(f"- {tool_name}: {summary}")
        if message:
            lines.extend(["", message])
        return "\n".join(lines).strip()

    def _tool_intent_loop_workflow_summary(
        self,
        *,
        executed_steps: list[dict[str, Any]],
        provider_id: str | None,
        validation_state: str,
    ) -> str:
        lines = [f"- model-driven tool loop executed {len(executed_steps)} real step{'s' if len(executed_steps) != 1 else ''}"]
        if executed_steps:
            step_chain = " -> ".join(str(step.get("tool_name") or "tool").strip() for step in executed_steps[:6])
            if step_chain:
                lines.append(f"- tool chain: `{step_chain}`")
        provider = str(provider_id or "").strip()
        if provider:
            lines.append(f"- tool intent provider: `{provider}`")
        validation = str(validation_state or "").strip()
        if validation:
            lines.append(f"- tool intent validation: `{validation}`")
        lines.append("- execution posture: `tool_executed`")
        return "\n".join(lines)

    def _tool_step_summary(self, response_text: str, *, fallback: str) -> str:
        for raw_line in str(response_text or "").splitlines():
            line = " ".join(raw_line.split()).strip(" -")
            if not line:
                continue
            return (line[:157] + "...") if len(line) > 160 else line
        clean_fallback = " ".join(str(fallback or "").split()).strip()
        return clean_fallback or "completed"

    def _runtime_preview(self, text: str, *, limit: int = 220) -> str:
        compact = " ".join(str(text or "").split()).strip()
        if len(compact) <= limit:
            return compact
        return compact[: max(1, limit - 3)].rstrip() + "..."

    def _emit_runtime_event(
        self,
        source_context: dict[str, Any] | None,
        *,
        event_type: str,
        message: str,
        **details: Any,
    ) -> None:
        checkpoint_id = self._runtime_checkpoint_id(source_context)
        if checkpoint_id and "checkpoint_id" not in details:
            details["checkpoint_id"] = checkpoint_id
        emit_runtime_event(
            source_context,
            event_type=event_type,
            message=message,
            details=details,
        )

    def _live_runtime_stream_enabled(self, source_context: dict[str, Any] | None) -> bool:
        return bool(str((source_context or {}).get("runtime_event_stream_id") or "").strip())

    def _sync_public_presence(
        self,
        *,
        status: str,
        source_context: dict[str, object] | None = None,
    ) -> None:
        effective_status = self._normalize_public_presence_status(status)
        with self._public_presence_lock:
            self._public_presence_status = effective_status
            if source_context is not None:
                self._public_presence_source_context = dict(source_context)
        try:
            if self._public_presence_registered:
                result = self.public_hive_bridge.heartbeat_presence(
                    agent_name=get_agent_display_name(),
                    capabilities=self._public_capabilities(),
                    status=effective_status,
                    transport_mode=self._public_transport_mode(source_context),
                )
                if not result.get("ok"):
                    result = self.public_hive_bridge.sync_presence(
                        agent_name=get_agent_display_name(),
                        capabilities=self._public_capabilities(),
                        status=effective_status,
                        transport_mode=self._public_transport_mode(source_context),
                    )
            else:
                result = self.public_hive_bridge.sync_presence(
                    agent_name=get_agent_display_name(),
                    capabilities=self._public_capabilities(),
                    status=effective_status,
                    transport_mode=self._public_transport_mode(source_context),
                )
            if result.get("ok"):
                self._public_presence_registered = True
        except Exception as exc:
            audit_logger.log(
                "public_hive_presence_sync_error",
                target_id=self.persona_id,
                target_type="agent",
                details={"error": str(exc), "status": effective_status},
            )
            return
        if not result.get("ok"):
            audit_logger.log(
                "public_hive_presence_sync_failed",
                target_id=self.persona_id,
                target_type="agent",
                details={"status": effective_status, **dict(result or {})},
            )

    def _start_public_presence_heartbeat(self) -> None:
        if self._public_presence_running:
            return
        self._public_presence_running = True
        self._public_presence_thread = threading.Thread(
            target=self._public_presence_heartbeat_loop,
            name="nulla-public-presence",
            daemon=True,
        )
        self._public_presence_thread.start()

    def _start_idle_commons_loop(self) -> None:
        if self._idle_commons_running:
            return
        self._idle_commons_running = True
        self._idle_commons_thread = threading.Thread(
            target=self._idle_commons_loop,
            name="nulla-idle-commons",
            daemon=True,
        )
        self._idle_commons_thread.start()

    def _public_presence_heartbeat_loop(self) -> None:
        while self._public_presence_running:
            time.sleep(120.0)
            with self._public_presence_lock:
                last_status = str(self._public_presence_status or "idle")
                source_context = dict(self._public_presence_source_context or {})
            self._sync_public_presence(
                status=self._normalize_public_presence_status(last_status),
                source_context=source_context,
            )

    def _idle_commons_loop(self) -> None:
        while self._idle_commons_running:
            time.sleep(90.0)
            try:
                self._maybe_run_idle_commons_once()
                self._maybe_run_autonomous_hive_research_once()
            except Exception as exc:
                audit_logger.log(
                    "idle_commons_loop_error",
                    target_id=self.persona_id,
                    target_type="agent",
                    details={"error": str(exc)},
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

        auto_session_id = f"auto-research:{str(signal.get('topic_id') or '')}"
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
            if result.ok:
                if result.topic_id:
                    try:
                        self.hive_activity_tracker.note_watched_topic(session_id=auto_session_id, topic_id=result.topic_id)
                    except Exception:
                        pass
        finally:
            self._sync_public_presence(
                status=self._idle_public_presence_status(),
                source_context={"surface": "background", "platform": "openclaw", "lane": "autonomous_research"},
            )

    def _mark_user_activity(self) -> None:
        with self._activity_lock:
            self._last_user_activity_ts = time.time()

    def _idle_commons_session_id(self) -> str:
        return f"agent-commons:{get_local_peer_id()}"

    def _normalize_public_presence_status(self, status: str) -> str:
        lowered = str(status or "idle").strip().lower()
        if lowered == "busy":
            return "busy"
        return self._idle_public_presence_status()

    def _idle_public_presence_status(self) -> str:
        prefs = load_preferences()
        return "idle" if bool(getattr(prefs, "accept_hive_tasks", True)) else "limited"

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

    def _maybe_handle_hive_research_followup(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        clean = " ".join(str(user_input or "").split()).strip()
        lowered = clean.lower()
        topic_hint = self._extract_hive_topic_hint(clean)
        hive_state = session_hive_state(session_id)
        history = list((source_context or {}).get("conversation_history") or [])
        pending_topic_ids = [
            str(item).strip()
            for item in list(hive_state.get("pending_topic_ids") or [])
            if str(item).strip()
        ]
        if not self._looks_like_hive_research_followup(
            lowered,
            topic_hint=topic_hint,
            has_pending_topics=bool(pending_topic_ids),
            history_has_task_list=self._history_mentions_hive_task_list(history)
            or str(hive_state.get("interaction_mode") or "") == "hive_task_selection_pending",
        ):
            return None
        if not self.public_hive_bridge.enabled():
            return self._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response="Public Hive is not enabled on this runtime, so I can't claim a live Hive task.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_research_followup",
            )
        if not self.public_hive_bridge.write_enabled():
            return self._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response="Hive task claiming is disabled here because public Hive auth is not configured for writes.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_research_followup",
            )

        queue_rows = self.public_hive_bridge.list_public_research_queue(limit=12)
        ambiguous_selection = self._looks_like_ambiguous_hive_selection_followup(
            lowered,
            has_pending_topics=bool(pending_topic_ids),
            history_has_task_list=self._history_mentions_hive_task_list(history)
            or str(hive_state.get("interaction_mode") or "") == "hive_task_selection_pending",
        )
        selection_scope = self._interaction_scoped_queue_rows(queue_rows, hive_state) or queue_rows
        allow_default_pick = not ambiguous_selection or len(selection_scope) <= 1
        signal = self._select_hive_research_signal(
            queue_rows,
            lowered=lowered,
            topic_hint=topic_hint,
            pending_topic_ids=self._interaction_pending_topic_ids(hive_state) or pending_topic_ids,
            allow_default_pick=allow_default_pick,
        )
        if signal is None:
            if queue_rows and ambiguous_selection:
                response = self._render_hive_research_queue_choices(
                    selection_scope,
                    lead="I still have multiple real Hive tasks open. Pick one by name or short `#id` and I’ll start there.",
                )
                return self._fast_path_result(
                    session_id=session_id,
                    user_input=clean,
                    response=response,
                    confidence=0.9,
                    source_context=source_context,
                    reason="hive_research_followup",
                )
            if topic_hint:
                response = f"I couldn't find an open Hive task matching `#{topic_hint}`."
            else:
                response = "I couldn't map that follow-up to a concrete open Hive task."
            return self._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
                confidence=0.84,
                source_context=source_context,
                reason="hive_research_followup",
            )

        topic_id = str(signal.get("topic_id") or "").strip()
        title = str(signal.get("title") or topic_id or "Hive topic").strip()
        clear_hive_interaction_state(session_id)
        self._sync_public_presence(status="busy", source_context=source_context)
        result = research_topic_from_signal(
            signal,
            public_hive_bridge=self.public_hive_bridge,
            curiosity=self.curiosity,
            hive_activity_tracker=self.hive_activity_tracker,
            session_id=session_id,
            auto_claim=True,
        )
        if not result.ok:
            response = str(result.response_text or f"Failed to start Hive research for `{topic_id}`.").strip()
            return self._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
                confidence=0.84,
                source_context=source_context,
                reason="hive_research_followup",
            )

        set_hive_interaction_state(
            session_id,
            mode="hive_task_active",
            payload={
                "active_topic_id": topic_id,
                "active_title": title,
                "claim_id": str(result.claim_id or "").strip(),
            },
        )

        summary = [
            f"Started Hive research on `{title}` (#{topic_id[:8]}).",
        ]
        if result.claim_id:
            summary.append(f"Claim `{result.claim_id[:8]}` is active.")
        query_count = len(list((result.details or {}).get("query_results") or []))
        if result.status == "completed":
            summary.append("The first bounded research pass already ran and posted its result.")
        else:
            summary.append("The research lane is active.")
        if query_count:
            summary.append(f"Bounded queries run: {query_count}.")
        if result.artifact_ids:
            summary.append(f"Artifacts packed: {len(result.artifact_ids)}.")
        if result.candidate_ids:
            summary.append(f"Candidate notes: {len(result.candidate_ids)}.")
        if str(result.result_status or "").strip().lower() == "researching":
            summary.append(
                "This fast reply only means the first bounded research pass finished."
            )
            summary.append(
                "Topic stays `researching` because NULLA still needs more evidence before it can honestly mark the task solved."
            )
        return self._fast_path_result(
            session_id=session_id,
            user_input=clean,
            response=" ".join(summary),
            confidence=0.9,
            source_context=source_context,
            reason="hive_research_followup",
        )

    def _extract_hive_topic_hint(self, text: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""
        full_match = _HIVE_TOPIC_FULL_ID_RE.search(clean)
        if full_match:
            return str(full_match.group(1) or "").strip().lower()
        short_match = _HIVE_TOPIC_SHORT_ID_RE.search(clean)
        if short_match:
            return str(short_match.group(1) or "").strip().lower()
        return ""

    def _maybe_handle_hive_topic_create_request(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        draft = self._extract_hive_topic_create_draft(user_input)
        if draft is None:
            return None

        if not self.public_hive_bridge.enabled():
            return self._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="Public Hive is not enabled on this runtime, so I can't create a live Hive task.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_topic_create_disabled",
                success=False,
                details={"status": "disabled"},
                mode_override="tool_failed",
                task_outcome="failed",
                workflow_summary=self._action_workflow_summary(
                    operator_kind="hive.create_topic",
                    dispatch_status="disabled",
                    details={"action_id": ""},
                ),
            )
        if not self.public_hive_bridge.write_enabled():
            return self._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="Hive task creation is disabled here because public Hive auth is not configured for writes.",
                confidence=0.9,
                source_context=source_context,
                reason="hive_topic_create_missing_auth",
                success=False,
                details={"status": "missing_auth"},
                mode_override="tool_failed",
                task_outcome="failed",
                workflow_summary=self._action_workflow_summary(
                    operator_kind="hive.create_topic",
                    dispatch_status="missing_auth",
                    details={"action_id": ""},
                ),
            )

        title = str(draft.get("title") or "").strip()
        summary = str(draft.get("summary") or "").strip() or title
        topic_tags = [
            str(item).strip()
            for item in list(draft.get("topic_tags") or [])
            if str(item).strip()
        ][:8]
        if len(title) < 4:
            return self._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response=(
                    "I can create the Hive task, but I still need a concrete title. "
                    'Use a format like: create new task in Hive: "better watcher task UX".'
                ),
                confidence=0.42,
                source_context=source_context,
                reason="hive_topic_create_missing_title",
                success=False,
                details={"status": "missing_title"},
                mode_override="tool_failed",
                task_outcome="failed",
                workflow_summary=self._action_workflow_summary(
                    operator_kind="hive.create_topic",
                    dispatch_status="missing_title",
                    details={"action_id": ""},
                ),
            )

        result = self.public_hive_bridge.create_public_topic(
            title=title,
            summary=summary,
            topic_tags=topic_tags,
            linked_task_id=task.task_id,
            idempotency_key=f"{task.task_id}:hive_create",
        )
        topic_id = str(result.get("topic_id") or "").strip()
        if not result.get("ok") or not topic_id:
            status = str(result.get("status") or "topic_failed").strip() or "topic_failed"
            return self._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response=self._hive_topic_create_failure_text(status),
                confidence=0.46,
                source_context=source_context,
                reason=f"hive_topic_create_{status}",
                success=False,
                details={"status": status, **dict(result)},
                mode_override="tool_failed",
                task_outcome="failed",
                workflow_summary=self._action_workflow_summary(
                    operator_kind="hive.create_topic",
                    dispatch_status=status,
                    details={"action_id": ""},
                ),
            )

        try:
            self.hive_activity_tracker.note_watched_topic(session_id=session_id, topic_id=topic_id)
        except Exception:
            pass
        tag_suffix = f" Tags: {', '.join(topic_tags[:6])}." if topic_tags else ""
        response = f"Created Hive task `{title}` (#{topic_id[:8]}).{tag_suffix}"
        return self._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.95,
            source_context=source_context,
            reason="hive_topic_create_created",
            success=True,
            details={"status": "created", "topic_id": topic_id, "topic_tags": topic_tags},
            mode_override="tool_executed",
            task_outcome="success",
            workflow_summary=self._action_workflow_summary(
                operator_kind="hive.create_topic",
                dispatch_status="created",
                details={"action_id": topic_id},
            ),
        )

    def _extract_hive_topic_create_draft(self, text: str) -> dict[str, Any] | None:
        clean = " ".join(str(text or "").split()).strip()
        lowered = clean.lower()
        if not self._looks_like_hive_topic_create_request(lowered):
            return None

        sections = {
            "title": re.search(r"\b(?:name it|title|call it|called)\b\s*[:=-]?\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
            "summary": re.search(r"\bsummary\b\s*[:=-]\s*(.+?)(?=(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
            "tags": re.search(r"\b(?:topic tags?|tags?)\b\s*[:=-]\s*(.+)$", clean, re.IGNORECASE),
        }
        title = ""
        if sections["title"] is not None:
            title = str(sections["title"].group(1) or "")
        elif ":" in clean:
            title = clean.rsplit(":", 1)[-1]
        else:
            title = re.sub(r"^.*?\bhive\b[?!.,:;-]*\s*", "", clean, flags=re.IGNORECASE)
        title = re.sub(r"^(?:name it|title|call it|called)\b\s*[:=-]?\s*", "", title, flags=re.IGNORECASE)
        title = self._strip_wrapping_quotes(" ".join(title.split()).strip().strip("."))

        summary = ""
        if sections["summary"] is not None:
            summary = self._strip_wrapping_quotes(" ".join(str(sections["summary"].group(1) or "").split()).strip().strip("."))
        if not summary and title:
            summary = title

        topic_tags: list[str] = []
        if sections["tags"] is not None:
            raw_tags = str(sections["tags"].group(1) or "")
            topic_tags = [
                normalized
                for normalized in (
                    self._normalize_hive_topic_tag(item)
                    for item in re.split(r"[,;|/]+", raw_tags)
                )
                if normalized
            ][:8]
        if not topic_tags and title:
            topic_tags = self._infer_hive_topic_tags(title)

        return {
            "title": title[:180],
            "summary": summary[:4000],
            "topic_tags": topic_tags[:8],
        }

    def _looks_like_hive_topic_create_request(self, lowered: str) -> bool:
        text = str(lowered or "").strip().lower()
        if not text or "hive" not in text:
            return False
        if not any(marker in text for marker in ("create", "make", "start", "new task", "new topic", "open a", "open new")):
            return False
        if not any(marker in text for marker in ("task", "topic", "thread")):
            return False
        if any(
            marker in text
            for marker in (
                "claim task",
                "pull hive tasks",
                "open hive tasks",
                "open tasks",
                "show me",
                "what do we have",
                "any tasks",
                "list tasks",
                "ignore hive",
                "research complete",
                "status",
            )
        ):
            return False
        return True

    def _infer_hive_topic_tags(self, title: str) -> list[str]:
        stopwords = {
            "a",
            "an",
            "and",
            "best",
            "build",
            "building",
            "create",
            "fast",
            "fastest",
            "for",
            "from",
            "future",
            "human",
            "improving",
            "in",
            "into",
            "it",
            "lets",
            "new",
            "on",
            "or",
            "our",
            "preserving",
            "pure",
            "reuse",
            "self",
            "task",
            "the",
            "to",
            "ux",
            "with",
        }
        raw_tokens = re.findall(r"[a-z0-9]+", str(title or "").lower())
        tags: list[str] = []
        seen: set[str] = set()
        for token in raw_tokens:
            if len(token) < 3 and token not in {"ai", "ux", "ui", "vm", "os"}:
                continue
            if token in stopwords:
                continue
            normalized = self._normalize_hive_topic_tag(token)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            tags.append(normalized)
            if len(tags) >= 6:
                break
        return tags

    def _normalize_hive_topic_tag(self, raw: str) -> str:
        clean = re.sub(r"[^a-z0-9]+", "-", str(raw or "").strip().lower()).strip("-")
        if len(clean) < 2 or len(clean) > 32:
            return ""
        return clean

    def _strip_wrapping_quotes(self, text: str) -> str:
        clean = str(text or "").strip()
        if len(clean) >= 2 and clean[0] == clean[-1] and clean[0] in {'"', "'", "`"}:
            return clean[1:-1].strip()
        return clean

    def _hive_topic_create_failure_text(self, status: str) -> str:
        normalized = str(status or "").strip().lower()
        if normalized == "privacy_blocked_topic":
            return "I won't create that Hive task because it looks like it contains private or secret material."
        if normalized == "missing_target":
            return "Hive topic creation is configured incompletely on this runtime, so I can't post the task yet."
        if normalized == "disabled":
            return "Public Hive is not enabled on this runtime, so I can't create a live Hive task."
        if normalized == "missing_auth":
            return "Hive task creation is disabled here because public Hive auth is not configured for writes."
        if normalized == "empty_topic":
            return "I can create the Hive task, but I still need a concrete title and summary."
        return "I couldn't create that Hive task."

    def _maybe_handle_hive_status_followup(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        clean = " ".join(str(user_input or "").split()).strip()
        lowered = clean.lower()
        if not self._looks_like_hive_status_followup(lowered):
            return None
        if not self.public_hive_bridge.enabled():
            return None

        hive_state = session_hive_state(session_id)
        history = list((source_context or {}).get("conversation_history") or [])
        topic_hint = self._extract_hive_topic_hint(clean)
        watched_topic_ids = [
            str(item).strip()
            for item in list(hive_state.get("watched_topic_ids") or [])
            if str(item).strip()
        ]
        resolved_topic_id = self._resolve_hive_status_topic_id(
            topic_hint=topic_hint,
            watched_topic_ids=watched_topic_ids,
            history=history,
            interaction_state=hive_state,
        )
        if not resolved_topic_id:
            return None

        packet = self.public_hive_bridge.get_public_research_packet(resolved_topic_id)
        topic = dict(packet.get("topic") or {})
        state = dict(packet.get("execution_state") or {})
        counts = dict(packet.get("counts") or {})
        posts = [dict(item) for item in list(packet.get("posts") or [])]
        title = str(topic.get("title") or resolved_topic_id).strip()
        status = str(topic.get("status") or state.get("topic_status") or "").strip().lower()
        execution_state = str(state.get("execution_state") or "").strip().lower()
        active_claim_count = int(state.get("active_claim_count") or counts.get("active_claim_count") or 0)
        artifact_count = int(state.get("artifact_count") or 0)
        post_count = int(counts.get("post_count") or len(posts))

        if status in {"solved", "closed"}:
            lead = f"Yes. `{title}` (#{resolved_topic_id[:8]}) is `{status}`."
        elif status:
            lead = f"No. `{title}` (#{resolved_topic_id[:8]}) is still `{status}`."
        else:
            lead = f"`{title}` (#{resolved_topic_id[:8]}) is still in progress."

        summary: list[str] = [lead]
        if execution_state == "claimed" or active_claim_count > 0:
            summary.append(f"Active claims: {active_claim_count}.")
        if post_count:
            summary.append(f"Posts: {post_count}.")
        if artifact_count:
            summary.append(f"Artifacts: {artifact_count}.")
        if status == "researching" and artifact_count > 0:
            summary.append("The first bounded pass landed, but the topic did not clear the solve threshold yet.")
        latest_post = posts[0] if posts else {}
        latest_post_kind = str(latest_post.get("post_kind") or "").strip().lower()
        latest_post_body = " ".join(str(latest_post.get("body") or "").split()).strip()
        if latest_post_kind or latest_post_body:
            label = latest_post_kind or "post"
            if latest_post_body:
                summary.append(f"Latest {label}: {latest_post_body[:220]}.")
        return self._fast_path_result(
            session_id=session_id,
            user_input=clean,
            response=" ".join(part for part in summary if part),
            confidence=0.92,
            source_context=source_context,
            reason="hive_status_followup",
        )

    def _resolve_hive_status_topic_id(
        self,
        *,
        topic_hint: str,
        watched_topic_ids: list[str],
        history: list[dict[str, Any]],
        interaction_state: dict[str, Any] | None = None,
    ) -> str:
        interaction_payload = dict((interaction_state or {}).get("interaction_payload") or {})
        active_topic = str(interaction_payload.get("active_topic_id") or "").strip().lower()
        if active_topic and (not topic_hint or active_topic == topic_hint or active_topic.startswith(topic_hint)):
            return active_topic
        watched = [str(item).strip().lower() for item in list(watched_topic_ids or []) if str(item).strip()]
        if topic_hint:
            for topic_id in reversed(watched):
                if topic_id == topic_hint or topic_id.startswith(topic_hint):
                    return topic_id
        history_hints = self._history_hive_topic_hints(history)
        for hint in [topic_hint, *history_hints]:
            clean_hint = str(hint or "").strip().lower()
            if not clean_hint:
                continue
            for topic_id in reversed(watched):
                if topic_id == clean_hint or topic_id.startswith(clean_hint):
                    return topic_id
        if watched:
            return watched[-1]

        lookup_rows = self.public_hive_bridge.list_public_topics(
            limit=32,
            statuses=("open", "researching", "disputed", "solved", "closed"),
        )
        for hint in [topic_hint, *history_hints]:
            clean_hint = str(hint or "").strip().lower()
            if not clean_hint:
                continue
            for row in lookup_rows:
                topic_id = str(row.get("topic_id") or "").strip().lower()
                if topic_id == clean_hint or topic_id.startswith(clean_hint):
                    return topic_id
        return ""

    def _looks_like_hive_status_followup(self, lowered: str) -> bool:
        text = str(lowered or "").strip().lower()
        if not text:
            return False
        if not any(marker in text for marker in ("research", "hive", "topic", "task", "done", "complete", "status", "finish", "finished")):
            return False
        for phrase in (
            "is research complete",
            "is the research complete",
            "is it complete",
            "is it done",
            "is research done",
            "did it finish",
            "did research finish",
            "is the task complete",
            "what is the status",
            "status?",
            "what's the status",
            "is that solved",
            "is it solved",
        ):
            if phrase in text:
                return True
        return False

    def _history_hive_topic_hints(self, history: list[dict[str, Any]] | None) -> list[str]:
        hints: list[str] = []
        for message in reversed(list(history or [])[-8:]):
            content = str(message.get("content") or "").strip()
            hint = self._extract_hive_topic_hint(content)
            if hint:
                hints.append(hint)
        return hints

    def _looks_like_hive_research_followup(
        self,
        lowered: str,
        *,
        topic_hint: str,
        has_pending_topics: bool,
        history_has_task_list: bool,
    ) -> bool:
        text = str(lowered or "").strip().lower()
        if topic_hint:
            bare_hint = f"#{topic_hint}"
            if text.rstrip(".!?") in {topic_hint, bare_hint}:
                return True
            return any(
                phrase in text
                for phrase in (
                    "this one",
                    "that one",
                    "go with this one",
                    "lets go with this one",
                    "let's go with this one",
                    "start this",
                    "start that",
                    "start #",
                    "claim #",
                    "take this",
                    "take #",
                    "claim this",
                    "pick this",
                    "pick #",
                    "work on #",
                    "research #",
                    "do #",
                )
            )
        if (has_pending_topics or history_has_task_list) and any(
            phrase in text
            for phrase in (
                "yes",
                "ok",
                "okay",
                "ok let's go",
                "ok lets go",
                "lets go",
                "let's go",
                "go ahead",
                "do it",
                "do one",
                "start it",
                "take it",
                "claim it",
                "work on it",
                "review it",
                "review this",
                "look into it",
                "research it",
                "pick one",
            )
        ):
            return True
        if (has_pending_topics or history_has_task_list) and any(
            phrase in text
            for phrase in (
                "first one",
                "1st one",
                "second one",
                "2nd one",
                "third one",
                "3rd one",
                "take the first one",
                "take the second one",
                "review the first one",
                "review the second one",
                "review the problem",
                "check the problem",
                "help with this",
                "help with that",
            )
        ):
            return True
        if any(
            phrase in text
            for phrase in (
                "go with this one",
                "lets go with this one",
                "let's go with this one",
                "start this one",
                "start that one",
                "take this one",
                "take that one",
                "claim this one",
                "claim that one",
            )
        ) and any(marker in text for marker in ("[researching", "[open", "[disputed", "topic", "task", "hive", "#")):
            return True
        if "hive" in text and any(phrase in text for phrase in ("pick one", "start the hive research", "start hive research", "pick a task", "choose one")):
            return True
        if "research" in text and "pick one" in text:
            return True
        return False

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
        tool_ids = [str(tool.get("tool_id") or "").strip() for tool in list_operator_tools() if tool.get("available")]
        prefs = load_preferences()
        capabilities = [
            "persistent_memory",
            "chat_continuity",
            "web_research",
            "tool_router",
            *(
                ["agent_commons", "idle_curiosity"]
                if bool(getattr(prefs, "social_commons", True))
                else []
            ),
            *[tool_id for tool_id in tool_ids if tool_id],
        ]
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
        try:
            sync_local_learning_shards()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(prog="nulla-agent")
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--persona", default="default")
    parser.add_argument("--input", default="")
    parser.add_argument("--json", action="store_true", help="Print full response payload as JSON.")
    args = parser.parse_args()

    backend_name = str(args.backend)
    device = str(args.device)
    if backend_name == "auto" or device == "auto":
        from core.backend_manager import BackendManager

        manager = BackendManager()
        hw = manager.detect_hardware()
        selection = manager.select_backend(hw)
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
