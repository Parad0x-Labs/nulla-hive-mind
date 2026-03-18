from __future__ import annotations

import argparse
import contextlib
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from core import audit_logger, feedback_engine, policy_engine
from core.autonomous_topic_research import pick_autonomous_research_signal, research_topic_from_signal
from core.candidate_knowledge_lane import get_candidate_by_id
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
    note_smalltalk_turn,
    prune_stale_hive_interaction_state,
    session_hive_state,
    set_hive_interaction_state,
    update_session_hive_state,
)
from core.human_input_adapter import adapt_user_input, runtime_session_id
from core.identity_manager import load_active_persona
from core.knowledge_fetcher import request_relevant_holders
from core.knowledge_registry import register_local_shard, sync_local_learning_shards
from core.live_quote_contract import LiveQuoteResult, validate_live_quote_payload
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
from core.privacy_guard import text_privacy_risks
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
    evaluate_direct_math_request,
    load_task_record,
    looks_like_explicit_lookup_request,
    looks_like_public_entity_lookup_request,
    looks_like_semantic_hive_request,
    model_execution_profile,
    redact_text,
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
from network.signer import get_local_peer_id
from retrieval.swarm_query import dispatch_query_shard
from retrieval.web_adapter import WebAdapter
from storage.db import get_connection
from storage.migrations import run_migrations

_log = logging.getLogger(__name__)


_HIVE_TOPIC_FULL_ID_RE = re.compile(r"\b([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\b", re.IGNORECASE)
_HIVE_TOPIC_SHORT_ID_RE = re.compile(r"#\s*([0-9a-f]{8,12})\b", re.IGNORECASE)
_UTILITY_TIMEZONE_ALIASES = {
    "vilnius": ("Europe/Vilnius", "Vilnius"),
    "lithuania": ("Europe/Vilnius", "Vilnius"),
    "europe/vilnius": ("Europe/Vilnius", "Vilnius"),
}
_CONTEXTUAL_TIME_FOLLOWUP_PATTERNS = (
    re.compile(r"\b(?:and\s+)?(?:now\s+)?there\b"),
    re.compile(r"\bwhat\s+about\s+there\b"),
    re.compile(r"\b(?:what(?:'s| is)\s+)?time\s+there\b"),
    re.compile(r"\bwhat\s+where(?:'s|s)?\s+is\s+there\b"),
    re.compile(r"\bwhat\s+where(?:'s|s)?\s+is\s+in\b"),
)
_TIME_FOLLOWUP_EXCLUSION_MARKERS = (
    "capital",
    "country",
    "population",
    "weather",
    "forecast",
    "date",
    "calendar",
    "meeting",
    "email",
    "hive",
    "task",
    "tasks",
    "queue",
    "work",
)
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
        self._hive_create_pending: dict[str, dict[str, Any]] = {}
        self._nullabook_pending: dict[str, dict[str, str]] = {}

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

        credit_result = self._maybe_handle_credit_command(effective_input, source_context=source_context)
        if credit_result is not None:
            return credit_result

        pending_hive_create_confirmation = self._is_pending_hive_create_confirmation_input(
            effective_input,
            session_id=session_id,
            source_context=source_context,
        )
        if not pending_hive_create_confirmation:
            hive_followup = self._maybe_handle_hive_research_followup(
                effective_input,
                session_id=session_id,
                source_context=source_context,
            )
            if hive_followup is not None:
                return hive_followup

        raw_hive_create_draft = self._extract_hive_topic_create_draft(user_input)
        effective_hive_create_draft = raw_hive_create_draft or self._extract_hive_topic_create_draft(effective_input)
        if effective_hive_create_draft is None and not pending_hive_create_confirmation:
            handled, response, model_wording_candidate, hive_command_details = self._maybe_handle_hive_runtime_command(
                effective_input,
                session_id=session_id,
            )
            if not handled:
                recovered_hive_input = self._recover_hive_runtime_command_input(effective_input)
                if recovered_hive_input:
                    handled, response, model_wording_candidate, hive_command_details = self._maybe_handle_hive_runtime_command(
                        recovered_hive_input,
                        session_id=session_id,
                    )
            if handled:
                topic_rows = [
                    dict(item)
                    for item in list((hive_command_details or {}).get("topics") or [])
                    if isinstance(item, dict) and str(item.get("topic_id") or "").strip()
                ]
                if topic_rows:
                    self._store_hive_topic_selection_state(session_id, topic_rows)
                if model_wording_candidate and self._is_chat_truth_surface(source_context):
                    return self._chat_surface_hive_wording_result(
                        session_id=session_id,
                        user_input=effective_input,
                        source_context=source_context,
                        response_class=self._classify_hive_text_response(response),
                        reason="hive_activity_model_wording",
                        observations=self._chat_surface_hive_command_observations(hive_command_details or {}),
                        fallback_response=self._chat_surface_hive_degraded_response(hive_command_details or {}),
                    )
                return self._fast_path_result(
                    session_id=session_id,
                    user_input=effective_input,
                    response=response,
                    confidence=0.89,
                    source_context=source_context,
                    reason="hive_activity_command",
                )

        if not pending_hive_create_confirmation:
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
            if self._is_chat_truth_surface(source_context):
                return self._chat_surface_model_wording_result(
                    session_id=session_id,
                    user_input=effective_input,
                    source_context=source_context,
                    persona=persona,
                    interpretation=interpreted,
                    task_class="unknown",
                    response_class=ResponseClass.UTILITY_ANSWER,
                    reason="credit_status_model_wording",
                    model_input=self._chat_surface_credit_status_model_input(
                        user_input=effective_input,
                        credit_snapshot=credit_status,
                    ),
                    fallback_response=credit_status,
                )
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=credit_status,
                confidence=0.95,
                source_context=source_context,
                reason="credit_status_fast_path",
            )

        date_time_status = self._date_time_fast_path(
            normalized_input,
            source_surface=source_surface,
            session_id=session_id,
            source_context=source_context,
        )
        if date_time_status:
            cleaned_date_time_input = str(normalized_input or "").strip().lower().strip(" \t\r\n?!.,")
            requested_timezone, requested_label = self._extract_utility_timezone(cleaned_date_time_input)
            if not requested_timezone:
                recent_utility_context = self._recent_utility_context(
                    session_id=session_id,
                    source_context=source_context,
                )
                requested_timezone, requested_label = self._contextual_time_followup_timezone(
                    cleaned_date_time_input,
                    recent_utility_context=recent_utility_context,
                )
            utility_payload: dict[str, Any] = {}
            if "current time" in str(date_time_status or "").lower():
                utility_payload = {
                    "utility_kind": "time",
                    "timezone": requested_timezone,
                    "label": requested_label,
                }
            set_hive_interaction_state(session_id, mode="utility", payload=utility_payload)
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=date_time_status,
                confidence=0.97,
                source_context=source_context,
                reason="date_time_fast_path",
            )

        direct_math = self._direct_math_fast_path(
            normalized_input,
            source_surface=source_surface,
        )
        if direct_math:
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=direct_math,
                confidence=0.99,
                source_context=source_context,
                reason="direct_math_fast_path",
            )

        capability_truth = self._maybe_handle_capability_truth_request(
            effective_input,
            session_id=session_id,
            source_context=source_context,
        )
        if capability_truth is not None:
            return capability_truth

        nullabook_fast = self._maybe_handle_nullabook_fast_path(
            effective_input,
            session_id=session_id,
            source_context=source_context,
        )
        if nullabook_fast is not None:
            return nullabook_fast

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
            if self._is_chat_truth_surface(source_context):
                return self._chat_surface_model_wording_result(
                    session_id=session_id,
                    user_input=effective_input,
                    source_context=source_context,
                    persona=persona,
                    interpretation=interpreted,
                    task_class="unknown",
                    response_class=ResponseClass.GENERIC_CONVERSATION,
                    reason="evaluative_conversation_model_wording",
                    model_input=effective_input,
                    fallback_response="I couldn't produce a grounded conversational reply in this run.",
                )
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
            if self._is_chat_truth_surface(source_context):
                is_help_prompt = smalltalk_phrase in {"what can you do", "help"}
                return self._chat_surface_model_wording_result(
                    session_id=session_id,
                    user_input=effective_input,
                    source_context=source_context,
                    persona=persona,
                    interpretation=interpreted,
                    task_class="unknown",
                    response_class=ResponseClass.GENERIC_CONVERSATION if is_help_prompt else ResponseClass.SMALLTALK,
                    reason="help_model_wording" if is_help_prompt else "smalltalk_model_wording",
                    model_input=self._chat_surface_smalltalk_model_input(
                        user_input=effective_input,
                        phrase=smalltalk_phrase,
                    ),
                    fallback_response=(
                        "I couldn't produce a grounded help reply in this run."
                        if is_help_prompt
                        else "I couldn't produce a grounded conversational reply in this run."
                    ),
                )
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
                message=f"Task classified as {classification.get('task_class') or 'unknown'!s}.",
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

            hive_confirm = self._maybe_handle_hive_create_confirmation(
                effective_input, task=task, session_id=session_id, source_context=source_context,
            )
            if hive_confirm is not None:
                return hive_confirm

            hive_topic_create = self._maybe_handle_hive_topic_create_request(
                user_input if raw_hive_create_draft is not None else effective_input,
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

            routing_classification, routing_profile = self._model_routing_profile(
                user_input=effective_input,
                classification=classification,
                interpretation=interpreted,
                source_context=source_context,
            )
            planner_style_requested = bool(routing_classification.get("planner_style_requested"))
            adaptive_research = self._collect_adaptive_research(
                task_id=task.task_id,
                query_text=effective_input,
                classification=routing_classification,
                interpretation=interpreted,
                source_context=source_context,
            )
            model_interpretation = interpreted
            adaptive_web_notes = [dict(note) for note in list(adaptive_research.notes or []) if isinstance(note, dict)]
            if is_chat_surface and (
                adaptive_research.enabled or adaptive_research.tool_gap_note or adaptive_research.admitted_uncertainty
            ):
                model_interpretation = adapt_user_input(
                    self._chat_surface_adaptive_research_model_input(
                        user_input=effective_input,
                        task_class=str(routing_classification.get("task_class") or "unknown"),
                        research_result=adaptive_research,
                    ),
                    session_id=session_id,
                )
            model_execution = self.memory_router.resolve(
                task=task,
                classification=routing_classification,
                interpretation=model_interpretation,
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

            web_notes = list(adaptive_web_notes)
            if not web_notes:
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
                "adaptive_research": adaptive_research.to_dict(),
                "external_media_evidence": media_analysis.evidence_items or media_evidence,
                "web_notes": web_notes,
            }

            workspace_build = self._maybe_run_builder_controller(
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

            # 7) choose the final speaker for this turn.
            planner_style_requested = explicit_planner_style_requested(effective_input)
            planner_renderer_allowed = should_use_planner_renderer(
                surface=surface,
                output_mode=str(routing_profile.get("output_mode") or ""),
                user_input=effective_input,
            )
            model_final_text = (
                self._chat_surface_model_final_text(model_execution)
                if is_chat_surface
                else self._model_final_response_text(model_execution)
            )
            model_final_answer_hit = bool(model_final_text)
            rendered_via = "model_final_wording"
            response_reason = "grounded_model_response"

            if planner_renderer_allowed and (not is_chat_surface or bool(model_execution.used_model)):
                response = render_response(
                    plan,
                    gate,
                    persona,
                    input_interpretation=interpreted,
                    prompt_assembly_report=context_result.report,
                    surface=surface,
                    allow_planner_style=planner_style_requested,
                )
                rendered_via = "reasoning_engine"
                response_reason = "grounded_plan_response"
                model_final_answer_hit = False
            elif model_final_answer_hit:
                response = model_final_text
            elif is_chat_surface:
                response = self._chat_surface_honest_degraded_response(model_execution)
                rendered_via = "honest_degraded_chat"
                response_reason = "chat_model_unavailable_degraded"
            else:
                response = render_response(
                    plan,
                    gate,
                    persona,
                    input_interpretation=interpreted,
                    prompt_assembly_report=context_result.report,
                    surface=surface,
                    allow_planner_style=planner_style_requested,
                )
                rendered_via = "reasoning_engine"
                response_reason = "grounded_plan_response"
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
                allow_planner_style=planner_style_requested,
            )
            self._apply_interaction_transition(session_id, turn_result)
            response = self._decorate_chat_response(
                turn_result,
                session_id=session_id,
                source_context=source_context,
            )
            self._emit_chat_truth_metrics(
                task_id=task.task_id,
                reason=response_reason,
                response_text=response,
                response_class=turn_result.response_class.value,
                source_context=source_context,
                rendered_via=rendered_via,
                fast_path_hit=False,
                model_inference_used=bool(model_execution.used_model),
                model_final_answer_hit=model_final_answer_hit,
                model_execution_source=str(model_execution.source or ""),
                tool_backing_sources=[
                    source
                    for source in (
                        "web_lookup" if web_notes else "",
                        "media_analysis" if media_analysis.used_provider else "",
                    )
                    if source
                ],
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
                    "adaptive_research_enabled": adaptive_research.enabled,
                    "adaptive_research_reason": adaptive_research.reason,
                    "adaptive_research_strategy": adaptive_research.strategy,
                    "adaptive_research_actions": list(adaptive_research.actions_taken),
                    "adaptive_research_uncertainty": adaptive_research.admitted_uncertainty,
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
                "research_controller": adaptive_research.to_dict(),
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

    def _model_final_response_text(self, model_execution: Any) -> str:
        final_text = str(getattr(model_execution, "output_text", "") or "").strip()
        if final_text:
            return final_text
        structured = getattr(model_execution, "structured_output", None)
        if isinstance(structured, dict):
            return str(structured.get("summary") or structured.get("message") or "").strip()
        return ""

    def _chat_surface_cache_or_memory_source(self, model_execution: Any) -> bool:
        source = str(getattr(model_execution, "source", "") or "").strip().lower()
        return source in {"exact_cache_hit", "memory_hit"}

    def _chat_surface_model_final_text(self, model_execution: Any) -> str:
        if self._chat_surface_cache_or_memory_source(model_execution):
            return ""
        return self._model_final_response_text(model_execution)

    def _chat_surface_honest_degraded_response(self, model_execution: Any) -> str:
        source = str(getattr(model_execution, "source", "") or "").strip().lower()
        if source == "exact_cache_hit":
            return (
                "I found a matching cached answer for this topic, but this chat path requires a live model response, "
                "so I'm not passing cached text off as a fresh answer."
            )
        if source == "memory_hit":
            return (
                "I found relevant local memory for this topic, but this chat path requires a live model response, "
                "so I'm not presenting remembered text as a fresh answer."
            )
        if source == "no_provider_available":
            return (
                "I couldn't get a live model response in this run, so I'm not going to recycle cached or remembered "
                "text as if it were fresh."
            )
        return (
            "I couldn't get a usable model response in this run, so I'm not going to recycle cached or remembered "
            "text as if it were fresh."
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
        source_context: dict[str, object] | None = None,
    ) -> dict | None:
        from network.signer import get_local_peer_id

        send_match = self._CREDIT_SEND_RE.search(user_input)
        if send_match:
            amount = float(send_match.group(1))
            target_peer = send_match.group(2).strip()
            peer_id = get_local_peer_id()
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
            peer_id = get_local_peer_id()
            task_id = str(uuid.uuid4())
            ok = escrow_credits_for_task(peer_id, task_id, amount)
            if ok:
                response = f"Reserved {amount:.2f} credits to prioritize your Hive task. Remaining balance: {get_credit_balance(peer_id):.2f}."
            else:
                balance = get_credit_balance(peer_id)
                response = f"Could not reserve credits. Your balance is {balance:.2f} (need {amount:.2f})."
            session_id = runtime_session_id(device=self.device, persona_id=self.persona_id)
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
        if self._looks_like_resume_request(user_input):
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
            normalized = self._normalize_tool_history_message(item)
            role = str(normalized.get("role") or "").strip().lower()
            content = str(normalized.get("content") or "").strip()
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

    def _date_time_fast_path(
        self,
        normalized_input: str,
        *,
        source_surface: str,
        session_id: str = "",
        source_context: dict[str, object] | None = None,
    ) -> str | None:
        if source_surface not in {"channel", "openclaw", "api"}:
            return None
        phrase = str(normalized_input or "").strip().lower()
        if not phrase:
            return None
        cleaned = phrase.strip(" \t\r\n?!.,")
        requested_timezone, requested_label = self._extract_utility_timezone(cleaned)
        recent_utility_context = self._recent_utility_context(
            session_id=session_id,
            source_context=source_context,
        )
        contextual_timezone, contextual_label = self._contextual_time_followup_timezone(
            cleaned,
            recent_utility_context=recent_utility_context,
        )
        effective_timezone = requested_timezone or contextual_timezone
        effective_label = requested_label or contextual_label
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
        asks_time = bool(
            any(
                marker in cleaned
                for marker in (
                    "what time is it",
                    "what's the time",
                    "current time",
                    "time now",
                    "what time is now",
                    "what time now",
                )
            )
            or ("time" in cleaned and any(marker in cleaned for marker in ("what", "now", "current", "right now")))
            or (effective_timezone and "time" in cleaned)
            or self._looks_like_malformed_time_followup(
                cleaned,
                effective_timezone=effective_timezone,
                recent_utility_context=recent_utility_context,
            )
            or bool(contextual_timezone)
        )
        if not asks_date and not asks_time:
            return None
        now = self._utility_now_for_timezone(effective_timezone)
        location_prefix = f"in {effective_label} " if effective_label else ""
        if asks_date and asks_time:
            return now.strftime(f"Today {location_prefix}is %A, %Y-%m-%d. Current time is %H:%M %Z.")
        if asks_date:
            return now.strftime(f"Today {location_prefix}is %A, %Y-%m-%d.")
        if effective_label:
            return now.strftime(f"Current time in {effective_label} is %H:%M %Z.")
        return now.strftime("Current time is %H:%M %Z.")

    def _direct_math_fast_path(self, normalized_input: str, *, source_surface: str) -> str | None:
        if source_surface not in {"channel", "openclaw", "api"}:
            return None
        return evaluate_direct_math_request(normalized_input)

    def _extract_utility_timezone(self, cleaned_input: str) -> tuple[str, str]:
        lowered = " ".join(str(cleaned_input or "").strip().lower().split())
        if not lowered:
            return "", ""
        for marker, resolved in _UTILITY_TIMEZONE_ALIASES.items():
            if marker in lowered:
                return resolved
        return "", ""

    def _utility_now_for_timezone(self, timezone_name: str) -> datetime:
        if timezone_name:
            try:
                return datetime.now(ZoneInfo(timezone_name))
            except Exception:
                pass
        return datetime.now().astimezone()

    def _recent_utility_context(
        self,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, str]:
        if session_id:
            state = session_hive_state(session_id)
            if str(state.get("interaction_mode") or "").strip().lower() == "utility":
                payload = dict(state.get("interaction_payload") or {})
                utility_kind = str(payload.get("utility_kind") or "").strip().lower()
                if utility_kind:
                    return {
                        "utility_kind": utility_kind,
                        "timezone": str(payload.get("timezone") or "").strip(),
                        "label": str(payload.get("label") or "").strip(),
                    }
        history = list((source_context or {}).get("conversation_history") or [])
        for message in reversed(history[-4:]):
            if not isinstance(message, dict):
                continue
            content = " ".join(str(message.get("content") or "").split()).strip().lower()
            if not content:
                continue
            timezone_name, label = self._extract_utility_timezone(content)
            if "current time" in content or "what time" in content or "time now" in content:
                return {
                    "utility_kind": "time",
                    "timezone": timezone_name,
                    "label": label,
                }
        return {}

    def _contextual_time_followup_timezone(
        self,
        cleaned_input: str,
        *,
        recent_utility_context: dict[str, str] | None,
    ) -> tuple[str, str]:
        lowered = " ".join(str(cleaned_input or "").strip().lower().split())
        if not lowered:
            return "", ""
        utility_kind = str((recent_utility_context or {}).get("utility_kind") or "").strip().lower()
        timezone_name = str((recent_utility_context or {}).get("timezone") or "").strip()
        label = str((recent_utility_context or {}).get("label") or "").strip()
        if utility_kind != "time" or not timezone_name:
            return "", ""
        if any(marker in lowered for marker in _TIME_FOLLOWUP_EXCLUSION_MARKERS):
            return "", ""
        if any(pattern.search(lowered) for pattern in _CONTEXTUAL_TIME_FOLLOWUP_PATTERNS):
            return timezone_name, label
        if "time" in lowered and any(
            marker in lowered
            for marker in (
                "there",
                "same place",
                "that place",
                "that city",
                "again",
                "now",
                "current",
                "right now",
            )
        ):
            return timezone_name, label
        return "", ""

    def _looks_like_malformed_time_followup(
        self,
        cleaned_input: str,
        *,
        effective_timezone: str,
        recent_utility_context: dict[str, str] | None,
    ) -> bool:
        if not effective_timezone:
            return False
        utility_kind = str((recent_utility_context or {}).get("utility_kind") or "").strip().lower()
        if utility_kind != "time":
            return False
        lowered = " ".join(str(cleaned_input or "").strip().lower().split())
        if "what" not in lowered:
            return False
        if not any(marker in lowered for marker in ("where's", "wheres", "where is")):
            return False
        return not any(marker in lowered for marker in _TIME_FOLLOWUP_EXCLUSION_MARKERS)

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
            disabled_response = "Live web lookup is disabled on this runtime, so I can't answer current weather or latest-news requests honestly."
            if self._is_chat_truth_surface(source_context):
                return self._chat_surface_model_wording_result(
                    session_id=session_id,
                    user_input=user_input,
                    source_context=source_context,
                    persona=load_active_persona(self.persona_id),
                    interpretation=interpretation,
                    task_class="research",
                    response_class=ResponseClass.UTILITY_ANSWER,
                    reason="live_info_model_wording",
                    model_input=self._chat_surface_live_info_model_input(
                        user_input=user_input,
                        query=str(user_input or "").strip(),
                        mode=live_mode,
                        runtime_note=disabled_response,
                    ),
                    fallback_response=disabled_response,
                    tool_backing_sources=[],
                )
            return self._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=disabled_response,
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
            unresolved_price = self._unresolved_price_lookup_response(query=query, notes=notes, mode=live_mode)
            if unresolved_price:
                return self._fast_path_result(
                    session_id=session_id,
                    user_input=user_input,
                    response=unresolved_price,
                    confidence=0.84,
                    source_context=source_context,
                    reason="live_info_fast_path",
                )
            return None
        unresolved_price = self._unresolved_price_lookup_response(query=query, notes=notes, mode=live_mode)
        if unresolved_price:
            return self._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=unresolved_price,
                confidence=0.84,
                source_context=source_context,
                reason="live_info_fast_path",
            )
        response = (
            self._render_live_info_response(query=query, notes=notes, mode=live_mode)
            if notes
            else self._live_info_failure_text(query=query, mode=live_mode)
        )
        structured_modes = {"weather", "news"}
        has_live_quote = self._first_live_quote(notes) is not None
        if self._is_chat_truth_surface(source_context) and live_mode not in structured_modes and not has_live_quote:
            return self._chat_surface_model_wording_result(
                session_id=session_id,
                user_input=user_input,
                source_context=source_context,
                persona=load_active_persona(self.persona_id),
                interpretation=interpretation,
                task_class="research",
                response_class=ResponseClass.UTILITY_ANSWER,
                reason="live_info_model_wording",
                model_input=self._chat_surface_live_info_model_input(
                    user_input=user_input,
                    query=query,
                    mode=live_mode,
                    notes=notes,
                    runtime_note="" if notes else response,
                ),
                fallback_response=(
                    "I pulled live evidence for this turn, but I couldn't produce a clean final synthesis in this run."
                    if notes
                    else response
                ),
                tool_backing_sources=["web_lookup"] if notes else [],
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
            return WebAdapter.search_query(
                query,
                limit=3,
                source_label="duckduckgo.com",
            )
        if live_mode == "fresh_lookup":
            quote_note = self._try_live_quote_note(query)
            if quote_note:
                return [quote_note]
        return WebAdapter.planned_search_query(
            query,
            limit=3,
            task_class="research",
            topic_kind="general" if live_mode == "fresh_lookup" else None,
            topic_hints=topic_hints,
            source_label="duckduckgo.com",
        )

    @staticmethod
    def _try_live_quote_note(query: str) -> dict[str, Any] | None:
        try:
            from tools.web.web_research import lookup_live_quote

            quote = lookup_live_quote(query, timeout_s=8)
            if quote is None:
                return None
            return quote.to_note()
        except Exception:
            return None

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
            " weather ",
            " weather?",
            "weather ",
            " forecast",
            " temperature",
            " rain ",
            " rain?",
            " raining",
            " rainy",
            " snow ",
            " snow?",
            " snowing",
            " snowy",
            " wind ",
            " windy",
            " humidity",
            " humid ",
            " sunrise",
            " sunset",
            " wheather",
            " wheater",
            " whether today",
            " whether now",
            " whether in ",
        )
        lowered_padded = f" {lowered} "
        news_markers = (
            "latest news",
            "breaking news",
            "headlines",
            "headline",
            "news on",
            "news about",
            "what happened today",
            "what's the latest on",
            "what is the latest on",
            "whats the latest on",
            "latest on ",
            "latest about ",
        )
        if any(marker in lowered_padded for marker in weather_markers):
            return "weather"
        if any(marker in lowered for marker in news_markers):
            return "news"
        if looks_like_explicit_lookup_request(lowered) or looks_like_public_entity_lookup_request(lowered):
            return "fresh_lookup"
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
                "price today",
                "price right now",
                "exchange rate",
                "how much is",
                "how much does",
                "worth right now",
                "worth today",
                "worth now",
                "market price",
                "stock price",
                "oil price",
                "gold price",
                "bitcoin price",
                "btc price",
                "eth price",
                "crypto price",
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
            "start coding",
            "start putting code",
            "put code",
            "putting code",
            "setup folder",
            "set up folder",
            "setup directory",
            "set up directory",
            "bootstrap",
            "initial files",
            "starter files",
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

    def _looks_like_generic_workspace_bootstrap_request(self, lowered: str) -> bool:
        text = " ".join(str(lowered or "").split()).strip().lower()
        if not text:
            return False
        bootstrap_markers = (
            "start coding",
            "start putting code",
            "start building",
            "start creating",
            "put code",
            "putting code",
            "building the code",
            "build the code",
            "initial files",
            "starter files",
            "bootstrap",
            "set up",
            "setup",
            "write the files",
            "create the files",
            "generate the files",
            "generate the code",
            "start working",
            "launch local",
            "launch localhost",
            "run locally",
        )
        target_markers = (
            "folder",
            "directory",
            "dir",
            "src/",
            "/src",
            "api/",
        )
        return bool(
            any(marker in text for marker in bootstrap_markers)
            and (any(marker in text for marker in target_markers) or bool(self._extract_requested_builder_root(text)))
        )

    def _extract_requested_builder_root(self, query_text: str) -> str:
        text = " ".join(str(query_text or "").split()).strip()
        if not text:
            return ""
        stop_words = {
            "a",
            "an",
            "the",
            "and",
            "folder",
            "directory",
            "dir",
            "path",
            "workspace",
            "repo",
            "repository",
            "this",
            "that",
            "there",
            "here",
            "code",
            "files",
        }
        patterns = (
            re.compile(r"\bnam(?:e|ed)\s+it\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)", re.IGNORECASE),
            re.compile(r"\b(?:folder|directory|dir|path)\s+(?:called|named)\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+)", re.IGNORECASE),
            re.compile(r"\b(?:called|named)\s+[`\"']?(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]*(?:/[A-Za-z0-9_./-]+)*)", re.IGNORECASE),
            re.compile(
                r"\b(?:create|make|setup|set up|bootstrap|mkdir)\s+(?:a|an|the)?\s*(?:folder|directory|dir|path)\s+(?:called|named)?\s*[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)",
                re.IGNORECASE,
            ),
            re.compile(r"\b(?:in|under|inside)\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)[`\"']?", re.IGNORECASE),
        )
        for pattern in patterns:
            match = pattern.search(text)
            if not match:
                continue
            candidate = str(match.group("path") or "").strip().strip("`\"'").rstrip(".,!?")
            if not candidate:
                continue
            if candidate.startswith("/"):
                candidate = candidate.lstrip("/")
            candidate = candidate.lstrip("./")
            if not candidate or candidate.lower() in stop_words:
                continue
            if ".." in candidate.split("/"):
                continue
            return candidate
        return ""

    def _normalize_live_info_query(self, text: str, *, mode: str) -> str:
        clean = " ".join(str(text or "").split()).strip()
        lowered = clean.lower()
        if mode == "weather" and "forecast" not in lowered and "weather" in lowered:
            return f"{clean} forecast"
        if mode == "news" and "latest" not in lowered and "news" in lowered:
            return f"latest {clean}"
        return clean

    def _render_live_info_response(self, *, query: str, notes: list[dict[str, Any]], mode: str) -> str:
        if mode == "weather":
            return self._render_weather_response(query=query, notes=notes)
        if mode == "news":
            return self._render_news_response(query=query, notes=notes)
        live_quote = self._first_live_quote(notes)
        if mode == "fresh_lookup" and live_quote is not None:
            return live_quote.answer_text()
        label = {
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

    def _first_live_quote(self, notes: list[dict[str, Any]]) -> LiveQuoteResult | None:
        for note in list(notes or []):
            payload = note.get("live_quote")
            if not isinstance(payload, dict):
                continue
            ok, _reason = validate_live_quote_payload(payload)
            if not ok:
                continue
            try:
                return LiveQuoteResult.from_payload(payload)
            except Exception:
                continue
        return None

    def _render_weather_response(self, *, query: str, notes: list[dict[str, Any]]) -> str:
        location = re.sub(
            r"\b(?:what\s+is\s+(?:the\s+)?|how\s+is\s+(?:the\s+)?|weather\s+(?:like\s+)?(?:in|for|at)\s+|"
            r"weather\s+in\s+|now\??|right\s+now\??|today\??|current(?:ly)?)\b",
            "", query, flags=re.IGNORECASE,
        )
        location = re.sub(r"\bforecast\b", " ", location, flags=re.IGNORECASE)
        location = " ".join(location.split()).strip(" ?.,!") or "your location"
        primary = dict(next((note for note in list(notes or []) if isinstance(note, dict)), {}))
        summary = " ".join(str(primary.get("summary") or "").split()).strip()
        url = str(primary.get("result_url") or "").strip()
        domain = str(primary.get("origin_domain") or "").strip()
        if summary:
            if location.lower() in summary.lower():
                line = summary
            else:
                line = f"Weather in {location}: {summary}"
        else:
            line = f"I searched for weather in {location} but couldn't extract conditions from the results."
        if url:
            line += f" Source: [{domain or 'source'}]({url})."
        return line

    def _render_news_response(self, *, query: str, notes: list[dict[str, Any]]) -> str:
        topic = re.sub(
            r"^\s*(?:what's|what is|whats)\s+the\s+latest\s+on\s+|^\s*latest\s+(?:news\s+on|news\s+about|on|about)\s+",
            "",
            query,
            flags=re.IGNORECASE,
        ).strip(" ?.,!") or query.strip()
        lines = [f"Latest coverage on {topic}:"]
        for note in list(notes or [])[:3]:
            summary = " ".join(str(note.get("summary") or "").split()).strip()
            fallback_title = str(note.get("result_title") or "").strip()
            url = str(note.get("result_url") or "").strip()
            domain = str(note.get("origin_domain") or "").strip()
            parts = [part.strip() for part in summary.split("|") if part.strip()]
            source = parts[0] if len(parts) >= 1 else domain
            published = parts[1] if len(parts) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1]) else ""
            headline = parts[2] if len(parts) >= 3 else fallback_title or summary
            lead_parts = [item for item in (published, source) if item]
            lead = " | ".join(lead_parts)
            line = f"- {headline}"
            if lead:
                line = f"- {lead}: {headline}"
            if url:
                line += f" [{url}]"
            lines.append(line)
        return "\n".join(lines)

    def _unresolved_price_lookup_response(self, *, query: str, notes: list[dict[str, Any]], mode: str) -> str:
        if mode != "fresh_lookup":
            return ""
        lowered = " ".join(str(query or "").strip().lower().split())
        if not any(
            marker in lowered
            for marker in (
                "price",
                "cost",
                "worth",
                "value",
                "quote",
                "rate",
                "market cap",
                "trading at",
                "how much",
            )
        ):
            return ""
        if self._first_live_quote(notes) is not None:
            return ""
        if self._notes_include_grounded_price_signal(notes):
            return ""
        subject = self._extract_price_lookup_subject(query)
        if not subject:
            return ""
        return (
            f"I couldn't map `{subject}` to a known traded asset or commodity quote. "
            "If you mean a stock, token, ETF, or product, give me the exact ticker or full name."
        )

    def _notes_include_grounded_price_signal(self, notes: list[dict[str, Any]]) -> bool:
        finance_domains = (
            "finance.yahoo.com",
            "coingecko.com",
            "marketwatch.com",
            "bloomberg.com",
            "tradingview.com",
            "investing.com",
        )
        for note in list(notes or []):
            if not isinstance(note, dict):
                continue
            text = " ".join(
                str(part).strip()
                for part in (
                    note.get("summary"),
                    note.get("result_title"),
                    note.get("origin_domain"),
                )
                if str(part).strip()
            )
            lowered = text.lower()
            domain = str(note.get("origin_domain") or "").strip().lower()
            if any(finance_domain in domain for finance_domain in finance_domains) and re.search(r"\d", text):
                return True
            if re.search(r"[$€£¥]\s?\d", text):
                return True
            if re.search(r"\b\d[\d,]*(?:\.\d+)?\s*(?:usd|eur|gbp|jpy|btc|eth)\b", lowered):
                return True
            if any(marker in lowered for marker in ("price", "quote", "market cap", "session change", "24h change")) and re.search(r"\d", text):
                return True
        return False

    def _extract_price_lookup_subject(self, query: str) -> str:
        clean = re.sub(r"[\?\!\.,]+", " ", str(query or "")).strip()
        clean = re.sub(
            r"\b(?:what\s+is|what's|whats|tell\s+me|show\s+me|price|cost|worth|value|quote|rate|market\s+cap|"
            r"trading\s+at|how\s+much|current|latest|today|now|right\s+now|for|of|the)\b",
            " ",
            clean,
            flags=re.IGNORECASE,
        )
        return " ".join(clean.split()).strip()

    def _live_info_failure_text(self, *, query: str, mode: str) -> str:
        if mode == "weather":
            return f'I tried the live web lane for "{query}", but no current weather results came back.'
        if mode == "news":
            return f'I tried the live web lane for "{query}", but no current news results came back.'
        return f'I tried the live web lane for "{query}", but no grounded live results came back.'

    _nullabook_pending: dict[str, dict[str, str]]

    @staticmethod
    def _classify_nullabook_intent(lowered: str) -> str | None:
        """Return a specific intent string only when the user clearly wants a NullaBook action.
        Returns None for casual mentions — those should fall through to the LLM."""
        import re
        if re.search(r'(?:post\s+(?:to|on)\s+(?:nullabook|nulla\s*book)|(?:nullabook|nulla\s*book)\s+post|do\s+(?:a\s+)?(?:first\s+)?post|let.s\s+(?:do\s+)?(?:a\s+|first\s+|our\s+)?post)', lowered):
            return "post"
        if re.search(r'(?:delete|remove)\s+(?:my\s+)?(?:nullabook\s+)?post', lowered):
            return "delete"
        if re.search(r'(?:edit|update|change)\s+(?:my\s+)?(?:nullabook\s+)?post\b', lowered):
            return "edit"
        if re.search(r'(?:create|make|set\s*up|start|open|get|register|sign\s*up)\s+(?:a\s+|my\s+|an?\s+|our\s+)?(?:nullabook\s+|nulla\s*book\s+)?(?:profile|account)', lowered):
            return "create"
        if "sign up" in lowered and ("nullabook" in lowered or "nulla book" in lowered):
            return "create"
        if re.search(r'(?:do\s+(?:we|i)\s+have|(?:is|check|what\s*(?:is|\'s))\s+(?:my|our))\s+(?:\w+\s+)?(?:(?:nullabook|nulla\s*book)\s+)?(?:name|handle|profile|account)', lowered):
            return "check_profile"
        if re.search(r'(?:what|who)\s+(?:is|am)\s+(?:my|i)\s+(?:on\s+)?(?:nullabook|nulla\s*book)', lowered):
            return "check_profile"
        if re.search(r'(?:my|our)\s+(?:nullabook|nulla\s*book)\s+(?:name|handle|profile)', lowered):
            return "check_profile"

        has_bio = bool(re.search(r'(?:(?:set|update|change)\s+(?:my\s+)?bio\b|^bio\s*:)', lowered))
        has_twitter = bool(re.search(r'(?:(?:set|update|change|add)\s+(?:my\s+)?(?:twitter|x)\s*(?:handle)?|(?:my\s+)?(?:twitter|x)\s*(?:handle)?\s*(?:is|:))', lowered))
        if has_bio and has_twitter:
            return "compound_bio_twitter"
        if has_twitter:
            return "twitter"
        if has_bio:
            return "bio"

        if re.search(r'(?:change|rename|switch|set|update)\s+(?:my\s+)?(?:(?:nullabook|nulla\s*book)\s+)?(?:name|handle|display)', lowered):
            return "rename"
        if re.search(r'(?:set\s+)?(?:my\s+)?(?:name|handle)\s*[:=]', lowered):
            return "rename"
        if re.search(r'(?:can\s+we\s+)?chang\w*\s+(?:it|this|that|\w+)\s+to\s+', lowered):
            return "rename"
        return None

    def _maybe_handle_nullabook_fast_path(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        lowered = " ".join(str(user_input or "").lower().split())

        pending = self._nullabook_pending.get(session_id)
        if pending:
            return self._handle_nullabook_pending_step(
                user_input, lowered, session_id=session_id,
                source_context=source_context, pending=pending,
            )

        intent = self._classify_nullabook_intent(lowered)
        if intent is None:
            compound = self._try_compound_nullabook_message(user_input, session_id=session_id, source_context=source_context)
            if compound is not None:
                return compound
            return None

        try:
            from core.nullabook_identity import get_profile, update_profile
            from network.signer import get_local_peer_id
            profile = get_profile(get_local_peer_id())
        except Exception:
            profile = None

        if intent == "post":
            return self._handle_nullabook_post(user_input, lowered, profile, session_id=session_id, source_context=source_context)

        if intent == "delete":
            return self._handle_nullabook_delete(user_input, lowered, profile, session_id=session_id, source_context=source_context)

        if intent == "edit":
            return self._handle_nullabook_edit(user_input, lowered, profile, session_id=session_id, source_context=source_context)

        if intent == "twitter":
            if not profile:
                return self._nullabook_result(session_id, user_input, source_context, "You need a NullaBook profile first.")
            twitter_update = self._extract_twitter_handle(user_input)
            if twitter_update:
                try:
                    update_profile(profile.peer_id, twitter_handle=twitter_update)
                    profile = get_profile(profile.peer_id)
                    self._sync_profile_to_hive(profile)
                    return self._nullabook_result(session_id, user_input, source_context,
                        f"Twitter/X handle set to **@{twitter_update}**\n"
                        f"Visible on your NullaBook profile. Links to https://x.com/{twitter_update}")
                except Exception as exc:
                    return self._nullabook_result(session_id, user_input, source_context, f"Failed to set Twitter handle: {exc}")
            return self._nullabook_result(session_id, user_input, source_context,
                "What's the Twitter/X handle? Just the username, no @ needed.")

        if intent == "bio":
            if not profile:
                return self._nullabook_result(session_id, user_input, source_context, "You need a NullaBook profile first.")
            bio_update = self._extract_nullabook_bio_update(user_input)
            if not bio_update:
                bio_update = re.sub(r'^bio\s*:\s*', '', user_input.strip(), flags=re.IGNORECASE).strip()
            if bio_update:
                try:
                    update_profile(profile.peer_id, bio=bio_update)
                    profile = get_profile(profile.peer_id)
                    self._sync_profile_to_hive(profile)
                    return self._nullabook_result(session_id, user_input, source_context, f"Bio updated: {bio_update}")
                except Exception as exc:
                    return self._nullabook_result(session_id, user_input, source_context, f"Failed to update bio: {exc}")
            return self._nullabook_result(session_id, user_input, source_context,
                "What do you want the bio to say?")

        if intent == "compound_bio_twitter":
            if not profile:
                return self._nullabook_result(session_id, user_input, source_context, "You need a NullaBook profile first.")
            results = []
            bio_update = self._extract_nullabook_bio_update(user_input)
            if bio_update:
                try:
                    update_profile(profile.peer_id, bio=bio_update)
                    results.append(f"Bio updated: {bio_update}")
                except Exception as exc:
                    results.append(f"Bio update failed: {exc}")
            twitter_update = self._extract_twitter_handle(user_input)
            if twitter_update:
                try:
                    update_profile(profile.peer_id, twitter_handle=twitter_update)
                    results.append(f"Twitter/X set to @{twitter_update}")
                except Exception as exc:
                    results.append(f"Twitter update failed: {exc}")
            profile = get_profile(profile.peer_id)
            self._sync_profile_to_hive(profile)
            return self._nullabook_result(session_id, user_input, source_context,
                "\n".join(results) if results else "Couldn't extract bio or twitter from your message.")

        if intent == "rename":
            if not profile:
                return self._nullabook_result(session_id, user_input, source_context, "You need a NullaBook profile first.")
            desired_handle = self._extract_handle_from_text(user_input)
            display_name = self._extract_display_name(user_input)
            if display_name:
                try:
                    update_profile(profile.peer_id, display_name=display_name)
                    profile = get_profile(profile.peer_id)
                    self._sync_profile_to_hive(profile)
                    return self._nullabook_result(session_id, user_input, source_context,
                        f"Display name set to: {display_name}")
                except Exception as exc:
                    return self._nullabook_result(session_id, user_input, source_context,
                        f"Failed to set display name: {exc}")
            if desired_handle and desired_handle.lower() != profile.handle.lower():
                return self._handle_nullabook_rename(
                    desired_handle, profile,
                    session_id=session_id, user_input=user_input, source_context=source_context)
            self._nullabook_pending[session_id] = {"step": "awaiting_rename"}
            return self._nullabook_result(session_id, user_input, source_context,
                f"Current handle: **{profile.handle}**. What do you want to change it to?")

        if intent in ("create", "check_profile"):
            desired_handle = self._extract_handle_from_text(user_input)
            if profile:
                if desired_handle and desired_handle.lower() != profile.handle.lower():
                    return self._handle_nullabook_rename(
                        desired_handle, profile,
                        session_id=session_id, user_input=user_input, source_context=source_context)
                display_info = f"\nDisplay name: {profile.display_name}" if profile.display_name and profile.display_name != profile.handle else ""
                twitter_display = f"\nTwitter/X: @{profile.twitter_handle}" if profile.twitter_handle else ""
                return self._nullabook_result(session_id, user_input, source_context,
                    f"NullaBook profile active — handle: **{profile.handle}**{display_info}\n"
                    f"Bio: {profile.bio or '(not set)'}{twitter_display}\n"
                    f"Stats: {profile.post_count} posts, {profile.claim_count} topic claims.")
            if desired_handle:
                self._nullabook_pending[session_id] = {"step": "awaiting_handle"}
                return self._nullabook_step_handle(
                    desired_handle, desired_handle.lower(),
                    session_id=session_id, source_context=source_context)
            self._nullabook_pending[session_id] = {"step": "awaiting_handle"}
            return self._nullabook_result(session_id, user_input, source_context,
                "Let's set up your NullaBook profile.\n"
                "What handle would you like? Rules: 3-32 characters, letters, numbers, underscores, or hyphens.")

        return None

    def _try_compound_nullabook_message(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        """Detect compound messages that contain multiple NullaBook actions in one message,
        e.g. 'set name: X Bio: Y First post: Z'. Execute all found actions."""
        import re
        results: list[str] = []

        try:
            from core.nullabook_identity import get_profile, update_profile
            from network.signer import get_local_peer_id
            peer_id = get_local_peer_id()
            profile = get_profile(peer_id)
        except Exception:
            profile = None

        handle_m = re.search(r'(?:set\s+)?(?:my\s+)?(?:name|handle)\s*[:=]\s*(\S+)', user_input, re.IGNORECASE)
        bio_m = re.search(r'bio\s*[:=]\s*(.+?)(?=\s+(?:first|post|twitter)|$)', user_input, re.IGNORECASE)
        post_m = re.search(r'(?:first\s+(?:our\s+)?post|our\s+first\s+post|post\s*it?)\s*[:=]\s*(.+?)$', user_input, re.IGNORECASE)
        twitter_m = re.search(r'(?:twitter|x)\s*(?:handle)?\s*[:=]\s*@?([A-Za-z0-9_]{1,15})', user_input, re.IGNORECASE)

        if not any([handle_m, bio_m, post_m, twitter_m]):
            return None

        if handle_m:
            desired = handle_m.group(1).strip().strip("\"'.,!?")
            if profile and desired.lower() != profile.handle.lower():
                try:
                    from core.nullabook_identity import rename_handle
                    rename_handle(peer_id, desired)
                    profile = get_profile(peer_id)
                    results.append(f"Handle changed to **{desired}**")
                except Exception as exc:
                    results.append(f"Handle change failed: {exc}")
            elif not profile:
                try:
                    from core.nullabook_identity import register_nullabook_account
                    reg = register_nullabook_account(desired, peer_id=peer_id)
                    profile = reg.profile
                    results.append(f"Registered as **{desired}** on NullaBook")
                except Exception as exc:
                    results.append(f"Registration failed: {exc}")

        if bio_m and profile:
            bio_text = bio_m.group(1).strip().strip("\"'").strip()[:280]
            if bio_text:
                try:
                    update_profile(profile.peer_id, bio=bio_text)
                    results.append(f"Bio set to: {bio_text}")
                except Exception:
                    pass

        if twitter_m and profile:
            tw = twitter_m.group(1)
            try:
                update_profile(profile.peer_id, twitter_handle=tw)
                results.append(f"Twitter set to @{tw}")
            except Exception:
                pass

        if profile and (bio_m or twitter_m or handle_m):
            profile = get_profile(profile.peer_id)
            self._sync_profile_to_hive(profile)

        if post_m and profile:
            content = post_m.group(1).strip().strip("\"'").strip()
            if content:
                try:
                    profile = get_profile(profile.peer_id)
                    from core.nullabook_identity import increment_post_count
                    from storage.nullabook_store import create_post
                    create_post(
                        peer_id=profile.peer_id,
                        handle=profile.handle,
                        content=content,
                        post_type="social",
                    )
                    increment_post_count(profile.peer_id)
                    with contextlib.suppress(Exception):
                        self.public_hive_bridge.sync_nullabook_post(
                            peer_id=profile.peer_id,
                            handle=profile.handle,
                            bio=profile.bio or "",
                            content=content,
                            post_type="social",
                            twitter_handle=profile.twitter_handle or "",
                            display_name=profile.display_name or "",
                        )
                    results.append(f"Posted: {content[:100]}")
                except Exception as exc:
                    results.append(f"Post failed: {exc}")

        if results:
            return self._nullabook_result(session_id, user_input, source_context, "\n".join(results))
        return None

    def _handle_nullabook_pending_step(
        self,
        user_input: str,
        lowered: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        pending: dict[str, str],
    ) -> dict[str, Any] | None:
        if lowered in ("cancel", "nevermind", "stop", "no", "nah"):
            self._nullabook_pending.pop(session_id, None)
            return self._nullabook_result(session_id, user_input, source_context, "NullaBook registration cancelled.")

        step = pending.get("step", "")

        if step == "awaiting_handle":
            return self._nullabook_step_handle(user_input, lowered, session_id=session_id, source_context=source_context)

        if step == "awaiting_bio":
            return self._nullabook_step_bio(user_input, session_id=session_id, source_context=source_context, pending=pending)

        if step == "awaiting_post_content":
            self._nullabook_pending.pop(session_id, None)
            content = user_input.strip()
            if not content:
                return self._nullabook_result(session_id, user_input, source_context, "Post can't be empty.")
            try:
                from core.nullabook_identity import get_profile
                from network.signer import get_local_peer_id
                profile = get_profile(get_local_peer_id())
            except Exception:
                profile = None
            if not profile:
                return self._nullabook_result(session_id, user_input, source_context, "No NullaBook profile found.")
            return self._execute_nullabook_post(
                content, profile, session_id=session_id, source_context=source_context)

        if step == "awaiting_rename":
            self._nullabook_pending.pop(session_id, None)
            new_name = user_input.strip()
            if not new_name:
                return self._nullabook_result(session_id, user_input, source_context, "Name can't be empty.")
            try:
                from core.nullabook_identity import get_profile, update_profile
                from network.signer import get_local_peer_id
                profile = get_profile(get_local_peer_id())
            except Exception:
                profile = None
            if not profile:
                return self._nullabook_result(session_id, user_input, source_context, "No NullaBook profile found.")
            import re as _re
            is_ascii_handle = bool(_re.fullmatch(r'[A-Za-z0-9_\-]{3,32}', new_name))
            if is_ascii_handle:
                return self._handle_nullabook_rename(
                    new_name, profile,
                    session_id=session_id, user_input=user_input, source_context=source_context)
            try:
                update_profile(profile.peer_id, display_name=new_name[:64])
                profile = get_profile(profile.peer_id)
                self._sync_profile_to_hive(profile)
                return self._nullabook_result(session_id, user_input, source_context,
                    f"Display name set to: {new_name[:64]}")
            except Exception as exc:
                return self._nullabook_result(session_id, user_input, source_context,
                    f"Failed to set name: {exc}")

        self._nullabook_pending.pop(session_id, None)
        return None

    def _nullabook_step_handle(
        self, user_input: str, lowered: str, *, session_id: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        handle = user_input.strip()
        for prefix in ("name it ", "name is ", "call me ", "register ", "handle ", "name ", "use "):
            if lowered.startswith(prefix):
                handle = user_input.strip()[len(prefix):].strip()
                break
        handle = handle.strip().strip("\"'").strip()

        from core.agent_name_registry import validate_agent_name
        valid, reason = validate_agent_name(handle)
        if not valid:
            return self._nullabook_result(session_id, user_input, source_context,
                f"'{handle}' is not valid: {reason}\nTry another handle (3-32 chars, alphanumeric with _ or -):")

        from core.agent_name_registry import get_peer_by_name
        from core.nullabook_identity import get_profile_by_handle
        if get_peer_by_name(handle) or get_profile_by_handle(handle):
            return self._nullabook_result(session_id, user_input, source_context,
                f"'{handle}' is already taken. Try a different handle:")

        try:
            from core.nullabook_identity import get_profile, register_nullabook_account
            from network.signer import get_local_peer_id
            peer_id = get_local_peer_id()
            register_nullabook_account(handle, peer_id=peer_id)
            profile = get_profile(peer_id)
            if profile:
                self._sync_profile_to_hive(profile)
        except Exception as exc:
            self._nullabook_pending.pop(session_id, None)
            return self._nullabook_result(session_id, user_input, source_context, f"Registration failed: {exc}")

        self._nullabook_pending[session_id] = {"step": "awaiting_bio", "handle": handle}
        return self._nullabook_result(session_id, user_input, source_context,
            f"Registered as **{handle}** on NullaBook!\n"
            f"Want to set a bio? Type your bio, or say 'skip' to finish.")

    def _nullabook_step_bio(
        self, user_input: str, *, session_id: str, source_context: dict[str, object] | None, pending: dict[str, str],
    ) -> dict[str, Any]:
        handle = pending.get("handle", "")
        self._nullabook_pending.pop(session_id, None)
        lowered = user_input.strip().lower()
        if lowered in ("skip", "no", "later", "nah", "pass"):
            return self._nullabook_result(session_id, user_input, source_context,
                f"Profile ready! Handle: **{handle}**\n"
                f"You can post with: 'post to NullaBook: <your message>'")
        try:
            from core.nullabook_identity import get_profile_by_handle, update_profile
            profile = get_profile_by_handle(handle)
            if profile:
                update_profile(profile.peer_id, bio=user_input.strip()[:500])
                profile = get_profile_by_handle(handle)
                self._sync_profile_to_hive(profile)
        except Exception:
            pass
        return self._nullabook_result(session_id, user_input, source_context,
            f"Profile ready! Handle: **{handle}**\nBio: {user_input.strip()[:500]}\n"
            f"You can post with: 'post to NullaBook: <your message>'")

    def _handle_nullabook_post(
        self, user_input: str, lowered: str, profile: Any, *, session_id: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        if not profile:
            self._nullabook_pending[session_id] = {"step": "awaiting_handle"}
            return self._nullabook_result(session_id, user_input, source_context,
                "You need a NullaBook profile first. What handle would you like?")

        content = self._extract_post_content(user_input)
        if not content:
            self._nullabook_pending[session_id] = {"step": "awaiting_post_content"}
            return self._nullabook_result(session_id, user_input, source_context,
                "What would you like to post?")

        return self._execute_nullabook_post(content, profile, session_id=session_id, source_context=source_context)

    def _execute_nullabook_post(
        self, content: str, profile: Any, *, session_id: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        try:
            from core.nullabook_identity import increment_post_count
            from storage.nullabook_store import create_post
            post = create_post(
                peer_id=profile.peer_id,
                handle=profile.handle,
                content=content[:5000],
                post_type="social",
            )
            increment_post_count(profile.peer_id)
            sync_result = {"ok": False}
            with contextlib.suppress(Exception):
                sync_result = self.public_hive_bridge.sync_nullabook_post(
                    peer_id=profile.peer_id,
                    handle=profile.handle,
                    bio=profile.bio or "",
                    content=content[:5000],
                    post_type="social",
                    twitter_handle=profile.twitter_handle or "",
                    display_name=profile.display_name or "",
                )
            display = profile.display_name or profile.handle
            sync_status = " (live on nullabook.com)" if sync_result.get("ok") else ""
            return self._nullabook_result(session_id, content, source_context,
                f"Posted to NullaBook as **{display}**{sync_status}:\n"
                f"> {content[:200]}\n\n"
                f"Post ID: {post.post_id}")
        except Exception as exc:
            return self._nullabook_result(session_id, content, source_context, f"Failed to post: {exc}")

    def _nullabook_result(
        self, session_id: str, user_input: str, source_context: dict[str, object] | None, response: str,
    ) -> dict[str, Any]:
        return self._fast_path_result(
            session_id=session_id, user_input=user_input, response=response,
            confidence=0.95, source_context=source_context, reason="nullabook_fast_path",
        )

    def _sync_profile_to_hive(self, profile) -> None:
        """Push current profile state to the public hive (meet node)."""
        try:
            bridge = getattr(self, "public_hive_bridge", None)
            if bridge is None:
                return
            bridge.sync_nullabook_profile(
                peer_id=profile.peer_id,
                handle=profile.handle,
                bio=profile.bio or "",
                display_name=profile.display_name or "",
                twitter_handle=profile.twitter_handle or "",
            )
        except Exception:
            pass

    @staticmethod
    def _is_nullabook_post_request(lowered: str) -> bool:
        return bool(re.search(
            r'(?:post\s+(?:to|on)\s+(?:nullabook|nulla\s*book)|(?:nullabook|nulla\s*book)\s+post|do\s+(?:a\s+)?(?:first\s+|our\s+)?post|let.s\s+(?:do\s+)?(?:a\s+|first\s+|our\s+)?post)',
            lowered,
        ))

    @staticmethod
    def _is_nullabook_delete_request(lowered: str) -> bool:
        return bool(re.search(r'(?:delete|remove)\s+(?:my\s+)?(?:nullabook\s+)?post', lowered))

    @staticmethod
    def _is_nullabook_edit_request(lowered: str) -> bool:
        return bool(re.search(r'(?:edit|update|change)\s+(?:my\s+)?(?:nullabook\s+)?post', lowered))

    def _handle_nullabook_delete(
        self, user_input: str, lowered: str, profile: Any,
        *, session_id: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        if not profile:
            return self._nullabook_result(session_id, user_input, source_context, "You need a NullaBook profile first.")
        post_id = self._extract_post_id(user_input)
        if not post_id:
            try:
                from storage.nullabook_store import list_user_posts
                recent = list_user_posts(profile.handle, limit=5)
                social = [p for p in recent if p.post_type == "social"]
                if not social:
                    return self._nullabook_result(session_id, user_input, source_context, "You don't have any social posts to delete.")
                if len(social) == 1:
                    post_id = social[0].post_id
                else:
                    lines = ["Which post do you want to delete?\n"]
                    for p in social:
                        lines.append(f"- `{p.post_id}`: {p.content[:60]}...")
                    lines.append("\nSay: delete post <post_id>")
                    return self._nullabook_result(session_id, user_input, source_context, "\n".join(lines))
            except Exception:
                return self._nullabook_result(session_id, user_input, source_context, "Couldn't list your posts. Try: delete post <post_id>")
        try:
            from storage.nullabook_store import delete_post
            ok = delete_post(post_id, profile.peer_id)
            if ok:
                with contextlib.suppress(Exception):
                    self.public_hive_bridge._post_json(
                        str(self.public_hive_bridge.config.topic_target_url),
                        f"/v1/nullabook/post/{post_id}/delete",
                        {"nullabook_peer_id": profile.peer_id},
                    )
                return self._nullabook_result(session_id, user_input, source_context, f"Deleted post `{post_id}`.")
            return self._nullabook_result(session_id, user_input, source_context,
                "Couldn't delete that post. Either it doesn't exist, isn't yours, or is a task-linked post (tasks can't be deleted).")
        except Exception as exc:
            return self._nullabook_result(session_id, user_input, source_context, f"Delete failed: {exc}")

    def _handle_nullabook_edit(
        self, user_input: str, lowered: str, profile: Any,
        *, session_id: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        if not profile:
            return self._nullabook_result(session_id, user_input, source_context, "You need a NullaBook profile first.")
        post_id = self._extract_post_id(user_input)
        new_content = self._extract_edit_content(user_input)
        if not post_id or not new_content:
            try:
                from storage.nullabook_store import list_user_posts
                recent = list_user_posts(profile.handle, limit=5)
                social = [p for p in recent if p.post_type == "social"]
                if not social:
                    return self._nullabook_result(session_id, user_input, source_context, "You don't have any social posts to edit.")
                lines = ["Specify the post and new content:\n"]
                for p in social:
                    lines.append(f"- `{p.post_id}`: {p.content[:60]}...")
                lines.append('\nSay: edit post <post_id> to: <new content>')
                return self._nullabook_result(session_id, user_input, source_context, "\n".join(lines))
            except Exception:
                return self._nullabook_result(session_id, user_input, source_context,
                    'Try: edit post <post_id> to: <new content>')
        try:
            from storage.nullabook_store import update_post
            updated = update_post(post_id, profile.peer_id, new_content)
            if updated:
                with contextlib.suppress(Exception):
                    self.public_hive_bridge._post_json(
                        str(self.public_hive_bridge.config.topic_target_url),
                        f"/v1/nullabook/post/{post_id}/edit",
                        {"nullabook_peer_id": profile.peer_id, "content": new_content},
                    )
                return self._nullabook_result(session_id, user_input, source_context,
                    f"Updated post `{post_id}`:\n> {new_content[:200]}")
            return self._nullabook_result(session_id, user_input, source_context,
                "Couldn't edit that post. Either it doesn't exist, isn't yours, or is a task-linked post (tasks can't be edited).")
        except Exception as exc:
            return self._nullabook_result(session_id, user_input, source_context, f"Edit failed: {exc}")

    @staticmethod
    def _extract_post_id(text: str) -> str:
        match = re.search(r'\b([a-f0-9]{12,16})\b', text)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_edit_content(text: str) -> str:
        match = re.search(r'(?:to|with|new\s*content)\s*:\s*(.+)', text, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip()[:5000] if match else ""

    @staticmethod
    def _is_nullabook_create_request(lowered: str) -> bool:
        import re
        if "sign up" in lowered:
            return True
        return bool(re.search(
            r'(?:create|make|set\s*up|start|open|get)\s+(?:a\s+|my\s+|an?\s+)?(?:nullabook\s+)?(?:profile|account)',
            lowered,
        )) or bool(re.search(
            r'(?:register|sign\s*up)\s+(?:on|for|to|with)?\s*(?:nullabook|nulla\s*book)',
            lowered,
        ))

    @staticmethod
    def _extract_nullabook_bio_update(text: str) -> str:
        """Extract bio content. Accepts 'bio: X', 'set bio X', 'update bio to X', etc.
        Works on original-case text to preserve the user's formatting."""
        import re
        for pattern in (
            r"(?:set|update|change)\s+(?:my\s+)?bio\s+(?:to\s+)?[\"'](.+?)[\"']",
            r"(?:set|update|change)\s+(?:my\s+)?bio\s*(?:to\s+)?[:\s]\s*(.+?)(?:\s+(?:and\s+|\.?\s*(?:first|twitter|add\s+|set\s+)))",
            r"^bio\s*[:=]\s*(.+?)(?:\s+(?:and\s+|\.?\s*(?:first|twitter|add\s+|set\s+)))",
            r"(?:set|update|change)\s+(?:my\s+)?bio\s*(?:to\s+)?[:\s]\s*(.+?)$",
            r"^bio\s*[:=]\s*(.+)$",
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip().strip("\"'").strip()
        return ""

    @staticmethod
    def _extract_twitter_handle(text: str) -> str:
        import re
        for pattern in (
            r'(?:set|update|add|change)\s+(?:my\s+)?(?:twitter|x)\s*(?:handle)?\s*(?:to|:)?\s*@?([A-Za-z0-9_]{1,15})',
            r'(?:my\s+)?(?:twitter|x)\s*(?:handle)?\s*(?:is|:)\s*@?([A-Za-z0-9_]{1,15})',
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _extract_handle_from_text(text: str) -> str | None:
        import re
        for pattern in (
            r'(?:set\s+)?(?:my\s+)?(?:profile\s+)?(?:name|handle)\s*[:=]\s*(\S+)',
            r'(?:my\s+)?(?:profile\s+)?(?:name|handle)\s+(?:there\s+)?(?:will\s+be|should\s+be|is|be)\s+(\S+)',
            r'(?:call|name)\s+me\s+(\S+)',
            r'(?:i\s+want\s+to\s+be|i\'?ll?\s+be|i\'?m)\s+(\S+)',
            r'(?:register|sign\s*up)\s+(?:as|with)\s+(\S+)',
            r'(?:change|rename|switch|set)\s+(?:my\s+)?(?:name|handle)\s+(?:to\s+)?(\S+)',
            r'(?:use|pick|choose)\s+(\S+)\s+(?:as\s+)?(?:my\s+)?(?:name|handle)',
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                candidate = match.group(1).strip().strip("\"'.,!?")
                if len(candidate) >= 3:
                    return candidate
        return None

    @staticmethod
    def _extract_post_content(text: str) -> str:
        """Pull post content from natural phrasing like 'post on nulla book: hello' or
        'let's do first post: hello world'."""
        import re
        for pattern in (
            r'(?:post\s+(?:to|on)\s+(?:nullabook|nulla\s*book)|(?:nullabook|nulla\s*book)\s+post)\s*[:\-]\s*(.+)',
            r'(?:let.s|do)\s+(?:(?:do|a)\s+)?(?:a\s+|first\s+|our\s+)?post\s*[:\-]\s*(.+)',
            r'(?:first\s+(?:our\s+)?post|our\s+first\s+post)\s*[:\-]\s*(.+)',
            r'post\s+(?:it|this)\s*[:\-]\s*(.+)',
        ):
            m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if m:
                return m.group(1).strip().strip("\"'").strip()
        for prefix in ("post to nullabook", "post on nullabook", "nullabook post",
                        "post to nulla book", "post on nulla book", "nulla book post"):
            lw = text.lower()
            idx = lw.find(prefix)
            if idx >= 0:
                after = text[idx + len(prefix):].strip()
                if after and after[0] in ":- ":
                    after = after[1:].strip()
                if after:
                    return after.strip("\"'").strip()
        return ""

    @staticmethod
    def _extract_display_name(text: str) -> str:
        """Extract a display name (supports emoji and unicode) from text like
        'update my nulla book name to 🦋NULLA🦋' or 'set display name: X'."""
        import re
        for pattern in (
            r'(?:change|rename|switch|set|update)\s+(?:my\s+)?(?:(?:nullabook|nulla\s*book)\s+)?(?:display\s+)?(?:name|handle)\s+(?:to\s+)(.+)',
            r'(?:change|rename|switch|set|update)\s+(?:my\s+)?(?:(?:nullabook|nulla\s*book)\s+)?(?:display\s+)?(?:name|handle)\s*[:=]\s*(.+)',
            r'(?:can\s+we\s+)?(?:change|set|update)\s+(?:it|this|that)\s+to\s+(.+)',
            r'(?:chang\w*|switch|set)\s+(?:\w+\s+)?to\s+(.+)',
        ):
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip().strip("\"'.,!?").strip()
                if candidate:
                    return candidate[:64]
        return ""

    def _handle_nullabook_rename(
        self, new_handle: str, profile: Any, *,
        session_id: str, user_input: str, source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        from core.agent_name_registry import validate_agent_name
        valid, reason = validate_agent_name(new_handle)
        if not valid:
            return self._nullabook_result(session_id, user_input, source_context,
                f"'{new_handle}' isn't valid: {reason}\nTry another handle (3-32 chars, alphanumeric with _ or -).")

        try:
            from core.nullabook_identity import rename_handle
            updated = rename_handle(profile.peer_id, new_handle)
        except ValueError as exc:
            return self._nullabook_result(session_id, user_input, source_context, str(exc))
        except Exception as exc:
            return self._nullabook_result(session_id, user_input, source_context, f"Rename failed: {exc}")

        return self._nullabook_result(session_id, user_input, source_context,
            f"Done! Handle changed: **{profile.handle}** → **{updated.handle}**\n"
            f"You can post with: 'post to NullaBook: <your message>'")

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
        return {
            "channel": "workspace_build",
            "target": {
                "platform": str(target.get("platform") or "").strip(),
                "language": str(target.get("language") or "").strip(),
                "root_dir": str(target.get("root_dir") or "").strip(),
            },
            "written_file_count": len(write_results),
            "written_files": [str(item.get("path") or "").strip() for item in write_results[:8]],
            "write_failures": [str(item).strip() for item in write_failures[:4] if str(item).strip()],
            "verification": {
                "status": str((verification or {}).get("status") or "").strip(),
                "ok": bool((verification or {}).get("ok", False)),
                "response_text": str((verification or {}).get("response_text") or "").strip(),
            },
            "sources": [
                {
                    "title": str(item.get("title") or "").strip(),
                    "url": str(item.get("url") or "").strip(),
                    "label": str(item.get("label") or "").strip(),
                }
                for item in list(sources or [])[:4]
            ],
        }

    def _workspace_build_degraded_response(
        self,
        *,
        target: dict[str, str],
        write_results: list[dict[str, Any]],
        write_failures: list[str],
        verification: dict[str, Any] | None,
    ) -> str:
        root_dir = str(target.get("root_dir") or "the workspace").strip()
        if write_results:
            status = str((verification or {}).get("status") or "").strip()
            if status == "executed":
                verification_line = "Verification passed." if bool((verification or {}).get("ok", False)) else (
                    f"Verification failed: {str((verification or {}).get('response_text') or '').strip()}"
                )
            elif status == "skipped":
                verification_line = "Verification was skipped for this scaffold type."
            else:
                verification_line = "Verification did not run."
            failure_line = ""
            if write_failures:
                failure_line = f" {len(write_failures)} write operation(s) failed."
            return (
                f"I completed the workspace build actions under `{root_dir}`, but I couldn't produce a clean final summary. "
                f"{verification_line}{failure_line}"
            ).strip()
        if write_failures:
            return (
                f"I attempted the workspace build actions for `{root_dir}`, but the file writes did not complete cleanly."
            ).strip()
        return "I couldn't complete the workspace build actions cleanly in this run."

    def _builder_support_gap_report(
        self,
        *,
        source_context: dict[str, object] | None,
        reason: str,
    ) -> dict[str, Any]:
        workspace_available = bool(str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip())
        write_enabled = bool(policy_engine.get("filesystem.allow_write_workspace", False))
        if not workspace_available:
            gap_reason = "I need an active workspace before I can run a real bounded builder loop."
        elif not write_enabled:
            gap_reason = "Workspace writes are disabled on this runtime, so I cannot run a real bounded builder loop."
        else:
            gap_reason = reason
        return {
            "requested_capability": "workspace.build_scaffold",
            "requested_label": "builder controller",
            "support_level": "unsupported",
            "claim": (
                "I can run a bounded local builder loop in the active workspace: create starter folders/files, write narrow Telegram or Discord bot scaffolds, "
                "inspect files, apply explicit replacements, and run bounded local commands."
            ),
            "partial_reason": (
                "This is still a bounded local builder loop, not a full autonomous research -> build -> debug -> test system "
                "for arbitrary products or stacks."
                if workspace_available and write_enabled
                else ""
            ),
            "reason": gap_reason,
            "nearby_alternatives": [
                "Ask me to inspect the repo or read specific files in the workspace.",
                "Ask for a starter scaffold or a Telegram or Discord bot scaffold in the active workspace.",
                "Ask me to create a starter folder or first files in a concrete workspace path.",
                "Give me an exact replacement to apply in a file and I can run it locally.",
                "Give me a bounded local command or test to run in the workspace.",
            ],
        }

    def _builder_controller_profile(
        self,
        *,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        source_context = dict(source_context or {})
        if not self._should_run_builder_controller(
            effective_input=effective_input,
            classification=classification,
            source_context=source_context,
        ):
            return {"should_handle": False}
        target = self._workspace_build_target(
            query_text=effective_input,
            interpretation=interpretation,
        )
        workflow_probe = plan_tool_workflow(
            user_text=effective_input,
            task_class=str(classification.get("task_class") or "unknown"),
            executed_steps=[],
            source_context=source_context,
        )
        workflow_intent = str(dict(workflow_probe.next_payload or {}).get("intent") or "").strip()
        workflow_supported_request = self._supports_bounded_builder_workflow_request(
            effective_input=effective_input,
            task_class=str(classification.get("task_class") or "unknown"),
        )
        generic_bootstrap_request = self._looks_like_generic_workspace_bootstrap_request(str(effective_input or "").lower())
        if str(target.get("platform") or "").strip() in {"telegram", "discord"} or generic_bootstrap_request:
            return {
                "should_handle": True,
                "supported": True,
                "mode": "scaffold",
                "target": target,
            }
        if workflow_supported_request and workflow_probe.handled and workflow_probe.next_payload and workflow_intent in {
            "workspace.search_text",
            "workspace.read_file",
            "workspace.write_file",
            "workspace.ensure_directory",
            "sandbox.run_command",
            "hive.create_topic",
        }:
            return {
                "should_handle": True,
                "supported": True,
                "mode": "workflow",
                "target": target,
                "initial_payloads": [dict(workflow_probe.next_payload or {})],
            }
        return {
            "should_handle": True,
            "supported": False,
            "mode": "unsupported",
            "target": target,
            "gap_report": self._builder_support_gap_report(
                source_context=source_context,
                reason=(
                    "I do not have a real bounded builder path for that request on this runtime. "
                    "I can handle bounded workspace starters, narrow bot scaffolds, or explicit inspect/edit/run flows in the active workspace."
                ),
            ),
        }

    def _supports_bounded_builder_workflow_request(
        self,
        *,
        effective_input: str,
        task_class: str,
    ) -> bool:
        if _looks_like_workspace_bootstrap_request(effective_input):
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
        tool_name = str(getattr(execution, "tool_name", "") or tool_payload.get("intent") or "unknown").strip()
        return {
            "tool_name": tool_name,
            "status": str(getattr(execution, "status", "") or "executed"),
            "mode": str(getattr(execution, "mode", "") or ""),
            "arguments": dict(tool_payload.get("arguments") or {}),
            "observation": dict((getattr(execution, "details", {}) or {}).get("observation") or {}),
            "details": dict(getattr(execution, "details", {}) or {}),
            "artifacts": [dict(item) for item in list((getattr(execution, "details", {}) or {}).get("artifacts") or []) if isinstance(item, dict)],
            "summary": self._tool_step_summary(
                str(getattr(execution, "response_text", "") or ""),
                fallback=str(getattr(execution, "status", "") or "executed"),
            ),
        }

    def _workspace_build_verification_payload(self, *, target: dict[str, str]) -> dict[str, Any] | None:
        language = str(target.get("language") or "").strip().lower()
        root_dir = str(target.get("root_dir") or "").strip().rstrip("/")
        if language != "python" or not root_dir:
            return None
        return {
            "intent": "sandbox.run_command",
            "arguments": {"command": f"python3 -m compileall -q {root_dir}/src"},
        }

    def _builder_initial_payloads(
        self,
        *,
        mode: str,
        target: dict[str, str],
        user_request: str,
        web_notes: list[dict[str, Any]],
        initial_payloads: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        if mode == "workflow":
            return [dict(item) for item in list(initial_payloads or []) if isinstance(item, dict)], []
        sources = self._workspace_build_sources(web_notes)
        file_map = self._workspace_build_file_map(
            target=target,
            user_request=user_request,
            web_notes=web_notes,
        )
        payloads = [
            {
                "intent": "workspace.write_file",
                "arguments": {"path": path, "content": content},
            }
            for path, content in file_map.items()
        ]
        verification_payload = self._workspace_build_verification_payload(target=target)
        if verification_payload is not None:
            payloads.append(verification_payload)
        return payloads, sources

    def _builder_controller_backing_sources(self, executed_steps: list[dict[str, Any]]) -> list[str]:
        sources: list[str] = []
        seen: set[str] = set()
        for step in list(executed_steps or []):
            tool_name = str(step.get("tool_name") or "").strip()
            if tool_name.startswith("workspace."):
                source = "workspace"
            elif tool_name.startswith("sandbox."):
                source = "sandbox"
            elif tool_name.startswith("web."):
                source = "web_lookup"
            else:
                continue
            if source in seen:
                continue
            seen.add(source)
            sources.append(source)
        return sources

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
        return {
            "channel": "bounded_builder",
            "builder_mode": str(mode or "").strip(),
            "target": {
                "platform": str(target.get("platform") or "").strip(),
                "language": str(target.get("language") or "").strip(),
                "root_dir": str(target.get("root_dir") or "").strip(),
            },
            "step_count": len(executed_steps),
            "stop_reason": str(stop_reason or "").strip(),
            "final_status": str(final_status or "").strip(),
            "artifacts": dict(artifacts or {}),
            "sources": [
                {
                    "title": str(item.get("title") or "").strip(),
                    "url": str(item.get("url") or "").strip(),
                    "label": str(item.get("label") or "").strip(),
                }
                for item in list(sources or [])[:4]
            ],
            "executed_steps": [
                {
                    "tool_name": str(step.get("tool_name") or "").strip(),
                    "status": str(step.get("status") or "").strip(),
                    "mode": str(step.get("mode") or "").strip(),
                    "summary": str(step.get("summary") or "").strip(),
                    "arguments": dict(step.get("arguments") or {}),
                    "observation": dict(step.get("observation") or {}),
                }
                for step in list(executed_steps or [])[:8]
            ],
        }

    def _builder_retry_history(self, executed_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        retries: list[dict[str, Any]] = []
        for index, step in enumerate(list(executed_steps or []), start=1):
            tool_name = str(step.get("tool_name") or "").strip()
            if tool_name != "sandbox.run_command":
                continue
            observation = dict(step.get("observation") or {})
            command = str(observation.get("command") or dict(step.get("arguments") or {}).get("command") or "").strip()
            if not command:
                continue
            key = (tool_name, command)
            if key not in seen:
                seen[key] = {
                    "command": command,
                    "attempts": 1,
                    "step_indexes": [index],
                    "returncodes": [int(observation.get("returncode") or 0)],
                }
                continue
            seen[key]["attempts"] = int(seen[key].get("attempts") or 1) + 1
            seen[key]["step_indexes"] = [*list(seen[key].get("step_indexes") or []), index]
            seen[key]["returncodes"] = [*list(seen[key].get("returncodes") or []), int(observation.get("returncode") or 0)]
        for entry in seen.values():
            if int(entry.get("attempts") or 0) <= 1:
                continue
            retries.append(
                {
                    "command": str(entry.get("command") or "").strip(),
                    "attempts": int(entry.get("attempts") or 0),
                    "step_indexes": [int(item) for item in list(entry.get("step_indexes") or [])],
                    "returncodes": [int(item) for item in list(entry.get("returncodes") or [])],
                }
            )
        return retries

    def _builder_controller_artifacts(
        self,
        *,
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
    ) -> dict[str, Any]:
        file_diffs: list[dict[str, Any]] = []
        command_outputs: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for index, step in enumerate(list(executed_steps or []), start=1):
            for artifact in [dict(item) for item in list(step.get("artifacts") or []) if isinstance(item, dict)]:
                artifact_type = str(artifact.get("artifact_type") or "").strip()
                record = {"step_index": index, **artifact}
                if artifact_type == "file_diff":
                    file_diffs.append(record)
                elif artifact_type == "command_output":
                    command_outputs.append(record)
                elif artifact_type == "failure":
                    failures.append(record)
        return {
            "file_diffs": file_diffs[:8],
            "command_outputs": command_outputs[:8],
            "failures": failures[:6],
            "retry_history": self._builder_retry_history(executed_steps),
            "stop_reason": str(stop_reason or "").strip(),
        }

    def _builder_artifact_citation_block(self, artifacts: dict[str, Any]) -> str:
        payload = dict(artifacts or {})
        lines = ["Artifacts:"]
        file_diffs = [dict(item) for item in list(payload.get("file_diffs") or []) if isinstance(item, dict)]
        if file_diffs:
            files = ", ".join(f"`{str(item.get('path') or '').strip()}`" for item in file_diffs[:4] if str(item.get("path") or "").strip())
            if files:
                lines.append(f"- changed files: {files}")
            diff_preview = str(file_diffs[0].get("diff_preview") or "").strip()
            if diff_preview:
                lines.append(f"- diff preview: `{self._runtime_preview(diff_preview, limit=180)}`")
        command_outputs = [dict(item) for item in list(payload.get("command_outputs") or []) if isinstance(item, dict)]
        if command_outputs:
            command_bits = []
            for item in command_outputs[:3]:
                command = str(item.get("command") or "").strip()
                returncode = int(item.get("returncode") or 0)
                if command:
                    command_bits.append(f"`{command}` (exit {returncode})")
            if command_bits:
                lines.append(f"- commands: {', '.join(command_bits)}")
        failures = [dict(item) for item in list(payload.get("failures") or []) if isinstance(item, dict)]
        if failures:
            failure_bits = []
            for item in failures[:2]:
                summary = str(item.get("summary") or "").strip()
                if summary:
                    failure_bits.append(f"`{self._runtime_preview(summary, limit=140)}`")
            if failure_bits:
                lines.append(f"- failures seen: {', '.join(failure_bits)}")
        retries = [dict(item) for item in list(payload.get("retry_history") or []) if isinstance(item, dict)]
        if retries:
            retry_bits = []
            for item in retries[:2]:
                command = str(item.get("command") or "").strip()
                attempts = int(item.get("attempts") or 0)
                if command and attempts > 1:
                    retry_bits.append(f"`{command}` x{attempts}")
            if retry_bits:
                lines.append(f"- retries: {', '.join(retry_bits)}")
        stop_reason = str(payload.get("stop_reason") or "").strip()
        if stop_reason:
            lines.append(f"- stop reason: `{stop_reason}`")
        return "\n".join(lines)

    def _append_builder_artifact_citations(self, text: str, *, artifacts: dict[str, Any]) -> str:
        message = str(text or "").strip()
        citation_block = self._builder_artifact_citation_block(artifacts)
        if not citation_block.strip():
            return message
        if not message:
            return citation_block
        return f"{message}\n\n{citation_block}".strip()

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
        root_dir = str(target.get("root_dir") or "the workspace").strip()
        if failed_execution is not None:
            failure_text = self._tool_failure_user_message(
                execution=failed_execution,
                effective_input=effective_input,
                session_id=session_id,
            )
            if executed_steps:
                return (
                    f"I completed {len(executed_steps)} bounded builder step"
                    f"{'' if len(executed_steps) == 1 else 's'} under `{root_dir}`, "
                    f"but the loop stopped at `{getattr(failed_execution, 'tool_name', '') or 'tool'!s}`. {failure_text}"
                ).strip()
            return failure_text
        if executed_steps:
            return (
                f"I completed {len(executed_steps)} bounded builder step"
                f"{'' if len(executed_steps) == 1 else 's'} under `{root_dir}` and stopped with `{stop_reason}`."
            ).strip()
        return f"I could not start a bounded builder loop for `{root_dir}` on this run.".strip()

    def _builder_controller_workflow_summary(
        self,
        *,
        mode: str,
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
        artifacts: dict[str, Any],
    ) -> str:
        lines = [
            f"- bounded builder controller executed {len(executed_steps)} real step{'s' if len(executed_steps) != 1 else ''}",
            f"- builder mode: `{mode}`",
        ]
        if executed_steps:
            chain = " -> ".join(str(step.get("tool_name") or "tool").strip() for step in list(executed_steps or [])[:8])
            if chain:
                lines.append(f"- tool chain: `{chain}`")
        if stop_reason:
            lines.append(f"- stop reason: `{stop_reason}`")
        file_diffs = [dict(item) for item in list((artifacts or {}).get("file_diffs") or []) if isinstance(item, dict)]
        if file_diffs:
            lines.append(
                "- changed files: "
                + ", ".join(f"`{str(item.get('path') or '').strip()}`" for item in file_diffs[:4] if str(item.get("path") or "").strip())
            )
        command_outputs = [dict(item) for item in list((artifacts or {}).get("command_outputs") or []) if isinstance(item, dict)]
        if command_outputs:
            lines.append(
                "- commands: "
                + ", ".join(
                    f"`{str(item.get('command') or '').strip()}` (exit {int(item.get('returncode') or 0)})"
                    for item in command_outputs[:3]
                    if str(item.get("command") or "").strip()
                )
            )
        failures = [dict(item) for item in list((artifacts or {}).get("failures") or []) if isinstance(item, dict)]
        if failures:
            lines.append(
                "- failures seen: "
                + ", ".join(
                    f"`{self._runtime_preview(str(item.get('summary') or ''), limit=120)}`"
                    for item in failures[:2]
                    if str(item.get("summary") or "").strip()
                )
            )
        retries = [dict(item) for item in list((artifacts or {}).get("retry_history") or []) if isinstance(item, dict)]
        if retries:
            lines.append(
                "- retries: "
                + ", ".join(
                    f"`{str(item.get('command') or '').strip()}` x{int(item.get('attempts') or 0)}"
                    for item in retries[:2]
                    if str(item.get("command") or "").strip() and int(item.get("attempts") or 0) > 1
                )
            )
        return "\n".join(lines)

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
        loop_source_context = self._merge_runtime_source_contexts({}, dict(source_context or {}))
        executed_steps: list[dict[str, Any]] = []
        pending_payloads = [dict(item) for item in list(initial_payloads or []) if isinstance(item, dict)]
        stop_reason = ""
        failed_execution = None
        max_steps = 6

        while len(executed_steps) < max_steps:
            if pending_payloads:
                tool_payload = dict(pending_payloads.pop(0))
            else:
                workflow_decision = plan_tool_workflow(
                    user_text=effective_input,
                    task_class=task_class,
                    executed_steps=executed_steps,
                    source_context=loop_source_context,
                )
                if workflow_decision.handled and workflow_decision.stop_after:
                    stop_reason = str(workflow_decision.reason or "stop_after").strip()
                    break
                if not workflow_decision.handled or not workflow_decision.next_payload:
                    stop_reason = str(workflow_decision.reason or "no_followup_plan").strip()
                    break
                tool_payload = dict(workflow_decision.next_payload or {})

            execution = execute_tool_intent(
                tool_payload,
                task_id=task.task_id,
                session_id=session_id,
                source_context=loop_source_context,
                hive_activity_tracker=self.hive_activity_tracker,
                public_hive_bridge=self.public_hive_bridge,
            )
            if not execution.handled:
                stop_reason = "tool_not_handled"
                break

            executed_steps.append(
                self._builder_controller_step_record(
                    execution=execution,
                    tool_payload=tool_payload,
                )
            )
            loop_source_context = self._append_tool_result_to_source_context(
                loop_source_context,
                execution=execution,
                tool_name=str(getattr(execution, "tool_name", "") or tool_payload.get("intent") or ""),
            )
            if str(getattr(execution, "mode", "") or "").strip() != "tool_executed":
                failed_execution = execution
                stop_reason = (
                    f"{getattr(execution, 'mode', '') or 'tool_failed'!s}:{getattr(execution, 'status', '') or 'failed'!s}"
                )
                break

        if not stop_reason and len(executed_steps) >= max_steps:
            stop_reason = "step_budget_exhausted"
        if not stop_reason:
            stop_reason = "bounded_loop_complete"
        return executed_steps, loop_source_context, stop_reason, failed_execution

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
        source_context = dict(source_context or {})
        profile = self._builder_controller_profile(
            effective_input=effective_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
        )
        if not profile.get("should_handle"):
            return None

        if not profile.get("supported"):
            report = dict(profile.get("gap_report") or {})
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=render_capability_truth_response(report),
                confidence=0.82 if str(report.get("support_level") or "").strip() == "partial" else 0.74,
                source_context=source_context,
                reason="builder_capability_gap",
            )

        target = dict(profile.get("target") or {})
        mode = str(profile.get("mode") or "workflow").strip()
        initial_payloads, sources = self._builder_initial_payloads(
            mode=mode,
            target=target,
            user_request=effective_input,
            web_notes=web_notes,
            initial_payloads=list(profile.get("initial_payloads") or []),
        )
        if mode == "scaffold" and not initial_payloads:
            report = self._builder_support_gap_report(
                source_context=source_context,
                reason=(
                    "That request did not resolve to a supported scaffold target. "
                    "The real scaffold lane here is still limited to Telegram or Discord bot builds."
                ),
            )
            return self._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=render_capability_truth_response(report),
                confidence=0.78,
                source_context=source_context,
                reason="builder_capability_gap",
            )

        executed_steps, loop_source_context, stop_reason, failed_execution = self._run_bounded_builder_loop(
            task=task,
            session_id=session_id,
            effective_input=effective_input,
            task_class=str(classification.get("task_class") or "unknown"),
            source_context=source_context,
            initial_payloads=initial_payloads,
        )
        final_status = "failed" if failed_execution is not None else "completed"
        artifacts = self._builder_controller_artifacts(
            executed_steps=executed_steps,
            stop_reason=stop_reason,
        )
        observations = self._builder_controller_observations(
            mode=mode,
            target=target,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            sources=sources,
            final_status=final_status,
            artifacts=artifacts,
        )
        degraded = self._builder_controller_degraded_response(
            target=target,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            failed_execution=failed_execution,
            effective_input=effective_input,
            session_id=session_id,
            artifacts=artifacts,
        )
        workflow_summary = self._builder_controller_workflow_summary(
            mode=mode,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            artifacts=artifacts,
        )
        if self._is_chat_truth_surface(loop_source_context):
            result = self._chat_surface_model_wording_result(
                session_id=session_id,
                user_input=effective_input,
                source_context=loop_source_context,
                persona=load_active_persona(self.persona_id),
                interpretation=interpretation,
                task_class=str(classification.get("task_class") or "integration_orchestration"),
                response_class=ResponseClass.GENERIC_CONVERSATION,
                reason="builder_controller_model_wording",
                model_input=self._chat_surface_builder_model_input(
                    user_input=effective_input,
                    observations=observations,
                ),
                fallback_response=degraded,
                tool_backing_sources=self._builder_controller_backing_sources(executed_steps),
                response_postprocessor=lambda text: self._append_builder_artifact_citations(text, artifacts=artifacts),
            )
            result["mode"] = "tool_failed" if failed_execution is not None else ("tool_executed" if executed_steps else "advice_only")
            result["workflow_summary"] = workflow_summary
            result["details"] = {
                "builder_controller": {
                    "mode": mode,
                    "step_count": len(executed_steps),
                    "stop_reason": stop_reason,
                    "tool_steps": [str(step.get("tool_name") or "").strip() for step in executed_steps],
                    "artifacts": artifacts,
                }
            }
            return result

        response_text = degraded
        if executed_steps:
            response_text = self._append_builder_artifact_citations(
                self._render_tool_loop_response(
                    final_message=degraded,
                    executed_steps=executed_steps,
                    include_step_summary=True,
                ),
                artifacts=artifacts,
            )
        return self._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=response_text,
            confidence=0.84 if executed_steps and failed_execution is None else 0.58,
            source_context=loop_source_context,
            reason="builder_controller_pipeline",
            success=bool(executed_steps) and failed_execution is None,
            details={
                "builder_mode": mode,
                "step_count": len(executed_steps),
                "stop_reason": stop_reason,
                "tool_steps": [str(step.get("tool_name") or "").strip() for step in executed_steps],
                "artifacts": artifacts,
            },
            mode_override="tool_failed" if failed_execution is not None else ("tool_executed" if executed_steps else "advice_only"),
            task_outcome="failed" if failed_execution is not None else ("success" if executed_steps else "advice_only"),
            workflow_summary=workflow_summary,
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
        generic_bootstrap_request = self._looks_like_generic_workspace_bootstrap_request(lowered)
        if task_class not in {
            "system_design",
            "integration_orchestration",
            "debugging",
            "dependency_resolution",
            "config",
            "file_inspection",
            "shell_guidance",
            "unknown",
        } and not generic_bootstrap_request:
            return False
        if not self._looks_like_builder_request(lowered):
            return False
        if any(marker in lowered for marker in ("don't write", "do not write", "advice only", "just plan", "no files")):
            return False
        scaffold_request = (
            any(marker in lowered for marker in ("build", "create", "scaffold", "implement", "generate", "start working"))
            and any(marker in lowered for marker in ("telegram", "discord", "bot", "agent", "service"))
            and any(marker in lowered for marker in ("workspace", "repo", "repository", "write the files", "create the files", "generate the code"))
        )
        return "write the files" in lowered or "create the files" in lowered or "generate the code" in lowered or "build the code" in lowered or "building the code" in lowered or "start working" in lowered or "start building" in lowered or "start creating" in lowered or "implement it" in lowered or "edit the files" in lowered or "patch the files" in lowered or "launch local" in lowered or scaffold_request or generic_bootstrap_request or self._explicit_runtime_workflow_request(user_input=effective_input, task_class=task_class)

    def _workspace_build_target(self, *, query_text: str, interpretation: Any) -> dict[str, str]:
        lowered = str(query_text or "").lower()
        topic_hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
        requested_root_dir = self._extract_requested_builder_root(query_text)
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
        elif "typescript" in lowered or "node" in lowered or "javascript" in lowered or (preferred_stacks and preferred_stacks[0] in {"typescript", "javascript"}):
            language = "typescript"
        else:
            language = "python"

        return {
            "platform": platform,
            "language": language,
            "root_dir": (
                requested_root_dir.rstrip("/")
                if requested_root_dir
                else f"generated/{platform}-bot"
                if platform in {"telegram", "discord"}
                else "generated/workspace-starter"
            ),
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
        if language == "typescript":
            return {
                f"{root_dir}/README.md": self._generic_workspace_readme(
                    user_request=user_request,
                    root_dir=root_dir,
                    sources=sources,
                    language=language,
                ),
                f"{root_dir}/package.json": self._generic_typescript_package_json(root_dir=root_dir),
                f"{root_dir}/tsconfig.json": self._telegram_typescript_tsconfig(),
                f"{root_dir}/src/index.ts": self._generic_typescript_source(user_request=user_request, sources=sources),
            }
        return {
            f"{root_dir}/README.md": self._generic_workspace_readme(
                user_request=user_request,
                root_dir=root_dir,
                sources=sources,
                language=language,
            ),
            f"{root_dir}/src/main.py": self._generic_python_source(user_request=user_request, sources=sources),
        }

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
            else f"Wrote a generic {target['language']} workspace starter under `{target['root_dir']}`."
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

    def _generic_workspace_readme(
        self,
        *,
        user_request: str,
        root_dir: str,
        sources: list[dict[str, str]],
        language: str,
    ) -> str:
        entrypoint = "src/index.ts" if language == "typescript" else "src/main.py"
        return (
            "# Workspace Starter\n\n"
            f"Bounded local {language} starter generated to unblock real work in `{root_dir}`.\n\n"
            "## Request\n\n"
            f"- {user_request.strip()}\n\n"
            "## Sources\n\n"
            f"{self._sources_section(sources)}\n"
            "## Files\n\n"
            f"- `{entrypoint}`: first executable entrypoint for this workspace.\n"
            "- `README.md`: visible grounding for what this starter is trying to do.\n"
        )

    def _generic_python_source(self, *, user_request: str, sources: list[dict[str, str]]) -> str:
        source_lines = "\n".join(f"# - {item['title']}: {item['url']}" for item in sources[:4]) or "# - No live sources captured in this run."
        return (
            '"""Workspace starter entrypoint.\n\n'
            f"Request: {user_request.strip()}\n"
            "Source references:\n"
            f"{source_lines}\n"
            '"""\n\n'
            "from __future__ import annotations\n\n"
            "def main() -> None:\n"
            '    print("NULLA workspace starter is ready for the next implementation step.")\n\n'
            'if __name__ == "__main__":\n'
            "    main()\n"
        )

    def _generic_typescript_package_json(self, *, root_dir: str) -> str:
        package_name = re.sub(r"[^a-z0-9_-]+", "-", root_dir.strip("/").split("/")[-1].lower()).strip("-") or "nulla-workspace-starter"
        return (
            "{\n"
            f'  "name": "{package_name}",\n'
            '  "private": true,\n'
            '  "type": "module",\n'
            '  "scripts": {\n'
            '    "dev": "tsx src/index.ts"\n'
            "  },\n"
            '  "devDependencies": {\n'
            '    "tsx": "^4.19.2",\n'
            '    "typescript": "^5.7.3"\n'
            "  }\n"
            "}\n"
        )

    def _generic_typescript_source(self, *, user_request: str, sources: list[dict[str, str]]) -> str:
        source_lines = "\n".join(f"// - {item['title']}: {item['url']}" for item in sources[:4]) or "// - No live sources captured in this run."
        return (
            "// Workspace starter entrypoint.\n"
            f"// Request: {user_request.strip()}\n"
            "// Source references:\n"
            f"{source_lines}\n\n"
            'console.log("NULLA workspace starter is ready for the next implementation step.");\n'
        )

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
        return ChatTurnResult(
            text=str(text or "").strip(),
            response_class=response_class,
            workflow_summary=str(workflow_summary or "").strip(),
            debug_origin=debug_origin,
            allow_planner_style=bool(allow_planner_style),
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
            allow_planner_style=result.allow_planner_style,
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

    def _sanitize_user_chat_text(
        self,
        text: str,
        *,
        response_class: ResponseClass,
        allow_planner_style: bool = False,
    ) -> str:
        base_text = str(text or "").strip()
        sanitized = self._strip_runtime_preamble(base_text, allow_planner_style=False)
        sanitized = self._strip_planner_leakage(sanitized)
        if self._contains_generic_planner_scaffold(sanitized):
            if response_class == ResponseClass.UTILITY_ANSWER:
                return "I couldn't answer that utility request cleanly."
            if response_class in {ResponseClass.TASK_FAILED_USER_SAFE, ResponseClass.SYSTEM_ERROR_USER_SAFE}:
                return "I couldn't map that cleanly to a real action."
            return "I'm here and ready to help. What do you want to do?"
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

    def _strip_runtime_preamble(self, text: str, *, allow_planner_style: bool = False) -> str:
        clean = str(text or "").strip()
        if allow_planner_style:
            return clean
        if not clean.startswith("Real steps completed:"):
            return clean
        parts = clean.split("\n\n", 1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()
        return "I couldn't resolve that cleanly."

    def _strip_planner_leakage(self, text: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""

        clean = self._unwrap_summary_or_action_payload(clean)

        lowered = clean.lower()
        if lowered.startswith("workflow:"):
            parts = clean.split("\n\n", 1)
            if len(parts) == 2 and parts[1].strip():
                clean = parts[1].strip()
            else:
                clean = re.sub(r"^workflow:\s*", "", clean, flags=re.IGNORECASE).strip()

        clean = re.sub(r"^here(?:'|’)s what i(?:'|’)d suggest:\s*", "", clean, flags=re.IGNORECASE).strip()
        clean = re.sub(r"^(summary_block|action_plan)\s*:\s*", "", clean, flags=re.IGNORECASE).strip()
        return clean

    def _contains_generic_planner_scaffold(self, text: str) -> bool:
        clean = self._unwrap_summary_or_action_payload(str(text or "").strip())
        if not clean:
            return False
        generic_lines = {"review problem", "choose safe next step", "validate result"}
        normalized_lines: list[str] = []
        for raw_line in clean.splitlines():
            line = re.sub(r"^[\-\*\d\.\)\s]+", "", raw_line).strip().lower()
            line = re.sub(r"[.!?]+$", "", line).strip()
            if line:
                normalized_lines.append(line)
        if not normalized_lines:
            return False
        unique_lines = set(normalized_lines)
        return len(unique_lines) >= 2 and unique_lines.issubset(generic_lines)

    def _unwrap_summary_or_action_payload(self, text: str) -> str:
        raw = str(text or "").strip()
        if not (raw.startswith("{") and raw.endswith("}")):
            return raw
        try:
            payload = json.loads(raw)
        except Exception:
            return raw
        if not isinstance(payload, dict):
            return raw

        summary = str(payload.get("summary") or payload.get("message") or "").strip()
        bullet_source = payload.get("bullets") or payload.get("steps") or []
        bullets = [str(item).strip() for item in list(bullet_source) if str(item).strip()]
        lines: list[str] = []
        if summary:
            lines.append(summary)
        lines.extend(f"- {item}" for item in bullets[:6])
        return "\n".join(line for line in lines if line.strip()) or raw

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
        if "reply with:" in lowered and "approve " in lowered:
            return False
        return True

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
            if (
                str(state.get("interaction_mode") or "").strip().lower() == "utility"
                and str(payload.get("utility_kind") or "").strip().lower() == "time"
                and "current time" in str(result.text or "").lower()
            ):
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
    ) -> tuple[bool, str, bool, dict[str, Any] | None]:
        handled, details = self.hive_activity_tracker.maybe_handle_command_details(user_input, session_id=session_id)
        response = str((details or {}).get("response_text") or "")
        command_kind = str((details or {}).get("command_kind") or "").strip().lower()
        if not handled:
            return False, "", False, None
        allow_model_wording = not self._looks_like_hive_prompt_control_command(user_input) and command_kind != "watcher_unavailable"
        if not self._hive_tracker_needs_bridge_fallback(response):
            return True, response, allow_model_wording, details
        bridge_details = self._maybe_handle_hive_bridge_fallback(
            user_input,
            session_id=session_id,
            tracker_response=response,
        )
        if bridge_details is not None:
            return True, str(bridge_details.get("response_text") or ""), True, bridge_details
        return True, response, allow_model_wording, details

    def _recover_hive_runtime_command_input(self, user_input: str) -> str:
        lowered = " ".join(str(user_input or "").strip().lower().split())
        if not lowered:
            return ""
        if looks_like_semantic_hive_request(lowered):
            return "show me the open hive tasks"
        if not any(marker in lowered for marker in ("hive", "hive mind", "brain hive", "public hive")):
            return ""
        if any(
            marker in lowered
            for marker in (
                "ignore hive",
                "ignore it for now",
                "new task",
                "new topic",
                "create task",
                "create topic",
                "status",
                "complete",
                "completed",
                "finished",
                "done",
            )
        ):
            return ""
        compact = lowered.strip(" \t\r\n?!.,")
        if re.fullmatch(
            r"(?:hi\s+)?(?:check|show|see)\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)(?:\s+(?:pls|please))?",
            compact,
        ):
            return "show me the open hive tasks"
        has_task_marker = any(marker in lowered for marker in ("task", "tasks", "taks", "work"))
        has_inquiry_marker = any(
            marker in lowered
            for marker in (
                "check",
                "see",
                "show",
                "what",
                "what's",
                "whats",
                "any",
                "open",
                "up",
                "available",
                "can we do",
            )
        )
        if not (has_task_marker and has_inquiry_marker):
            return ""
        return "show me the open hive tasks"

    def _hive_tracker_needs_bridge_fallback(self, response: str) -> bool:
        lowered = str(response or "").strip().lower()
        return lowered.startswith("hive watcher is not configured") or lowered.startswith("i couldn't reach the hive watcher")

    def _looks_like_hive_prompt_control_command(self, user_input: str) -> bool:
        lowered = " ".join(str(user_input or "").strip().lower().split())
        if not lowered:
            return False
        if "ignore hive" in lowered or "ignore it for now" in lowered:
            return True
        return "ignore" in lowered and "remind" in lowered

    def _maybe_handle_hive_bridge_fallback(
        self,
        user_input: str,
        *,
        session_id: str,
        tracker_response: str,
    ) -> dict[str, Any] | None:
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
            lead = "I couldn't read live agent presence from the watcher, but I can still pull public Hive tasks (public-bridge-derived; live presence unavailable):"
        elif "not configured" in str(tracker_response or "").lower():
            lead = "Live Hive watcher is not configured here, but I can still pull public Hive tasks (public-bridge-derived; live presence unavailable):"
        else:
            lead = "I couldn't reach the live Hive watcher, but I can still pull public Hive tasks (public-bridge-derived; live presence unavailable):"
        return {
            "command_kind": "task_list_bridge_fallback",
            "watcher_status": "bridge_fallback",
            "lead": lead,
            "response_text": self.hive_activity_tracker._render_hive_task_list_with_lead(topics, lead=lead),
            "topics": topics,
            "online_agents": [],
            "truth_source": "public_bridge",
            "truth_label": "public-bridge-derived",
            "truth_status": str((topics[0] or {}).get("truth_transport") or "bridge_fallback"),
            "truth_timestamp": str((topics[0] or {}).get("truth_timestamp") or ""),
            "presence_claim_state": "unavailable",
            "presence_source": "watcher",
            "presence_truth_label": "public-bridge-derived",
            "presence_freshness_label": "unavailable",
            "presence_note": "live watcher presence unavailable in public-bridge fallback",
        }

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
        return (
            "Grounding observations for this turn. Use them as evidence, not as a template:\n"
            f"{json.dumps(dict(observation or {}), indent=2, sort_keys=True, default=str)}"
        )

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

    _PROCEED_PATTERNS: frozenset[str] = frozenset({
        "proceed", "carry on", "continue", "do it", "do all", "go ahead",
        "start working", "yes", "yes proceed", "yes do it", "ok do it",
        "ok proceed", "ok go ahead", "deliver it", "submit it", "just do it",
        "yes pls", "yes please", "all good carry on", "proceed with next steps",
        "proceed with that", "all good", "no proceed",
    })

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

    def _maybe_handle_hive_research_followup(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        clean = " ".join(str(user_input or "").split()).strip()
        lowered = clean.lower()
        hive_state = session_hive_state(session_id)
        if self._is_pending_hive_create_confirmation_input(
            clean,
            session_id=session_id,
            source_context=source_context,
            hive_state=hive_state,
        ):
            return None

        active_resume = self._maybe_resume_active_hive_task(
            lowered, session_id=session_id, source_context=source_context, hive_state=hive_state,
        )
        if active_resume is not None:
            return active_resume

        topic_hint = self._extract_hive_topic_hint(clean)
        history = list((source_context or {}).get("conversation_history") or [])
        pending_topic_ids = [
            str(item).strip()
            for item in list(hive_state.get("pending_topic_ids") or [])
            if str(item).strip()
        ]
        shown_titles = self._interaction_shown_titles(hive_state)
        if not self._looks_like_hive_research_followup(
            lowered,
            topic_hint=topic_hint,
            has_pending_topics=bool(pending_topic_ids),
            shown_titles=shown_titles,
            history_has_task_list=self._history_mentions_hive_task_list(history)
            or str(hive_state.get("interaction_mode") or "") == "hive_task_selection_pending",
        ):
            return None
        if not self.public_hive_bridge.enabled():
            response = "Public Hive is not enabled on this runtime, so I can't claim a live Hive task."
            if self._is_chat_truth_surface(source_context):
                return self._chat_surface_hive_wording_result(
                    session_id=session_id,
                    user_input=clean,
                    source_context=source_context,
                    response_class=ResponseClass.TASK_FAILED_USER_SAFE,
                    reason="hive_research_followup_model_wording",
                    observations={
                        "channel": "hive",
                        "kind": "unsupported",
                        "truth_source": "future_or_unsupported",
                        "truth_label": "future/unsupported",
                        "truth_status": "disabled",
                        "presence_claim_state": "unsupported",
                        "presence_truth_label": "future/unsupported",
                        "presence_note": "public Hive is not enabled on this runtime",
                    },
                    fallback_response=response,
                )
            return self._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
                confidence=0.9,
                source_context=source_context,
                reason="hive_research_followup",
            )
        if not self.public_hive_bridge.write_enabled():
            response = "Hive task claiming is disabled here because public Hive auth is not configured for writes."
            if self._is_chat_truth_surface(source_context):
                return self._chat_surface_hive_wording_result(
                    session_id=session_id,
                    user_input=clean,
                    source_context=source_context,
                    response_class=ResponseClass.TASK_FAILED_USER_SAFE,
                    reason="hive_research_followup_model_wording",
                    observations={
                        "channel": "hive",
                        "kind": "unsupported",
                        "truth_source": "future_or_unsupported",
                        "truth_label": "future/unsupported",
                        "truth_status": "write_disabled",
                        "presence_claim_state": "unsupported",
                        "presence_truth_label": "future/unsupported",
                        "presence_note": "public Hive writes are not configured on this runtime",
                    },
                    fallback_response=response,
                )
            return self._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
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
                if self._is_chat_truth_surface(source_context):
                    return self._chat_surface_hive_wording_result(
                        session_id=session_id,
                        user_input=clean,
                        source_context=source_context,
                        response_class=ResponseClass.TASK_SELECTION_CLARIFICATION,
                        reason="hive_research_followup_model_wording",
                        observations=self._chat_surface_hive_queue_observations(
                            selection_scope,
                            lead="Multiple matching open Hive tasks are still available.",
                            truth_payload=self._bridge_hive_truth_from_rows(selection_scope),
                        ),
                        fallback_response=response,
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
            if self._is_chat_truth_surface(source_context):
                return self._chat_surface_hive_wording_result(
                    session_id=session_id,
                    user_input=clean,
                    source_context=source_context,
                    response_class=ResponseClass.TASK_SELECTION_CLARIFICATION,
                    reason="hive_research_followup_model_wording",
                    observations={
                        "channel": "hive",
                        "kind": "selection_clarification",
                        **self._hive_truth_observation_fields(self._bridge_hive_truth_from_rows(queue_rows)),
                    },
                    fallback_response=response,
                )
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

        wants_background = any(
            marker in lowered
            for marker in ("background", "in the background", "while we chat", "while i chat", "keep chatting")
        )
        if wants_background:
            import threading as _threading

            _signal = dict(signal)
            _bridge = self.public_hive_bridge
            _curiosity = self.curiosity
            _tracker = self.hive_activity_tracker

            def _bg_research() -> None:
                with contextlib.suppress(Exception):
                    research_topic_from_signal(
                        _signal,
                        public_hive_bridge=_bridge,
                        curiosity=_curiosity,
                        hive_activity_tracker=_tracker,
                        session_id=session_id,
                        auto_claim=True,
                    )

            _threading.Thread(target=_bg_research, name=f"bg-research-{topic_id[:12]}", daemon=True).start()
            response = f"Started Hive research on `{title}` in the background. We can keep chatting — I'll work on it."
            return self._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
                confidence=0.92,
                source_context=source_context,
                reason="hive_research_background",
            )

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
            if self._is_chat_truth_surface(source_context):
                return self._chat_surface_hive_wording_result(
                    session_id=session_id,
                    user_input=clean,
                    source_context=source_context,
                    response_class=ResponseClass.TASK_FAILED_USER_SAFE,
                    reason="hive_research_followup_model_wording",
                    observations=self._chat_surface_hive_research_result_observations(
                        topic_id=topic_id,
                        title=title,
                        result=result,
                    ),
                    fallback_response=response,
                )
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
        response = " ".join(summary)
        return self._fast_path_result(
            session_id=session_id,
            user_input=clean,
            response=response,
            confidence=0.9,
            source_context=source_context,
            reason="hive_research_followup",
        )

    def _maybe_resume_active_hive_task(
        self,
        lowered: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        hive_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """When interaction_mode is hive_task_active and user says proceed/carry on,
        directly execute research on the active topic without asking the model."""
        interaction_mode = str(hive_state.get("interaction_mode") or "").strip().lower()
        if interaction_mode != "hive_task_active":
            return None
        if not self._is_proceed_message(lowered):
            return None
        payload = dict(hive_state.get("interaction_payload") or {})
        topic_id = str(payload.get("active_topic_id") or "").strip()
        title = str(payload.get("active_title") or topic_id or "Hive topic").strip()
        if not topic_id:
            return None
        if not self.public_hive_bridge.enabled():
            return None

        self._sync_public_presence(status="busy", source_context=source_context)
        result = research_topic_from_signal(
            {"topic_id": topic_id},
            public_hive_bridge=self.public_hive_bridge,
            curiosity=self.curiosity,
            hive_activity_tracker=self.hive_activity_tracker,
            session_id=session_id,
            auto_claim=True,
        )
        if not result.ok:
            response = str(result.response_text or f"Research on `{title}` didn't complete cleanly.").strip()
            return self._fast_path_result(
                session_id=session_id,
                user_input=lowered,
                response=response,
                confidence=0.84,
                source_context=source_context,
                reason="hive_research_active_resume",
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

        quality = dict((result.details or {}).get("quality_summary") or {})
        q_status = str(quality.get("research_quality_status") or result.result_status or "researching").strip()
        query_count = len(list((result.details or {}).get("query_results") or []))
        nonempty = int(quality.get("nonempty_query_count") or 0)
        promoted = int(quality.get("promoted_finding_count") or 0)
        domains = int(quality.get("source_domain_count") or 0)

        summary_parts = [f"Research on `{title}` (#{topic_id[:8]}) completed."]
        if result.claim_id:
            summary_parts.append(f"Claim `{result.claim_id[:8]}` is active.")
        summary_parts.append(f"Quality: {q_status}.")
        if query_count:
            summary_parts.append(f"Queries: {nonempty}/{query_count} returned evidence.")
        if domains:
            summary_parts.append(f"Source domains: {domains}.")
        if promoted:
            summary_parts.append(f"Promoted findings: {promoted}.")
        if result.artifact_ids:
            summary_parts.append(f"Artifacts: {len(result.artifact_ids)}.")
        if q_status not in ("grounded", "solved"):
            summary_parts.append("Topic stays open — more evidence needed for grounded status.")
        response = " ".join(summary_parts)
        return self._fast_path_result(
            session_id=session_id,
            user_input=lowered,
            response=response,
            confidence=0.92,
            source_context=source_context,
            reason="hive_research_active_resume",
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
        bare_short_match = re.fullmatch(r"[#\s]*([0-9a-f]{8,12})[.!?]*", clean, re.IGNORECASE)
        if bare_short_match:
            return str(bare_short_match.group(1) or "").strip().lower()
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
                response="Public Hive is not enabled on this runtime, so I can't create a live Hive task. Hive truth: future/unsupported.",
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
                response="Hive task creation is disabled here because public Hive auth is not configured for writes. Hive truth: future/unsupported.",
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

        title = self._clean_hive_title(str(draft.get("title") or "").strip())
        summary = str(draft.get("summary") or "").strip() or title
        public_copy = self._prepare_public_hive_topic_copy(
            raw_input=user_input,
            title=title,
            summary=summary,
        )
        if not bool(public_copy.get("ok")):
            return self._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response=str(public_copy.get("response") or "I won't create that Hive task."),
                confidence=0.9,
                source_context=source_context,
                reason=str(public_copy.get("reason") or "hive_topic_create_privacy_blocked"),
                success=False,
                details={
                    "status": "privacy_blocked",
                    "privacy_risks": list(public_copy.get("privacy_risks") or []),
                },
                mode_override="tool_failed",
                task_outcome="failed",
                workflow_summary=self._action_workflow_summary(
                    operator_kind="hive.create_topic",
                    dispatch_status="privacy_blocked",
                    details={"action_id": ""},
                ),
            )
        title = str(public_copy.get("title") or title).strip() or title
        summary = str(public_copy.get("summary") or summary).strip() or summary
        preview_note = str(public_copy.get("preview_note") or "")
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

        dup = self._check_hive_duplicate(title, summary)

        pending = {
            "title": title,
            "summary": summary,
            "topic_tags": topic_tags,
            "task_id": task.task_id,
            "auto_start_research": bool(draft.get("auto_start_research")),
        }
        self._remember_hive_create_pending(session_id, pending)
        tag_line = f"\nTags: {', '.join(topic_tags[:6])}" if topic_tags else ""
        dup_warning = ""
        if dup:
            dup_title = dup.get("title", "")
            dup_id = str(dup.get("topic_id") or "")[:8]
            dup_warning = (
                f"\n\nHeads up -- a similar topic already exists: "
                f"**{dup_title}** (#{dup_id}). Still want to create a new one?"
            )
        preview = (
            f"Ready to post this to the public Hive:\n\n"
            f"**{title}**{tag_line}{dup_warning}{preview_note}\n\n"
            f"Confirm? (yes / no)"
        )
        return self._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response=preview,
            confidence=0.95,
            source_context=source_context,
            reason="hive_topic_create_awaiting_confirmation",
            success=True,
            details={"status": "awaiting_confirmation", "title": title, "topic_tags": topic_tags},
            mode_override="tool_preview",
            task_outcome="pending_approval",
            workflow_summary=self._action_workflow_summary(
                operator_kind="hive.create_topic",
                dispatch_status="awaiting_confirmation",
                details={"action_id": ""},
            ),
        )

    # ------------------------------------------------------------------
    # Hive create confirmation gate
    # ------------------------------------------------------------------

    _HIVE_CONFIRM_POSITIVE_STRICT = re.compile(
        r"^\s*(?:yes|yea|yeah|yep|yup|ok(?:ay)?|sure|do\s*it|go\s*(?:ahead|for\s*it)|"
        r"lets?\s*(?:go|do\s*it)|for\s*sure|absolutely|confirmed?|lgtm|send\s*it|"
        r"post\s*it|create\s*it|ship\s*it|proceed|affirmative|y)\s*[.!]*\s*$",
        re.IGNORECASE,
    )
    _HIVE_CONFIRM_POSITIVE_LOOSE = re.compile(
        r"^\s*(?:yes|yea|yeah|yep|yup|ok(?:ay)?|sure|do\s*it|go\s*(?:ahead|for\s*it)|"
        r"lets?\s*(?:go|do\s*it)|for\s*sure|absolutely|confirmed?|lgtm|send\s*it|"
        r"post\s*it|create\s*it|ship\s*it|proceed|affirmative)\b",
        re.IGNORECASE,
    )
    _HIVE_CONFIRM_NEGATIVE = re.compile(
        r"^\s*(?:no|nah|nope|not?\s*now|later|meh|cancel|stop|skip|forget\s*it|"
        r"never\s*mind|nevermind|don'?t|nay|negative|n)\s*[.!]*\s*$",
        re.IGNORECASE,
    )

    def _maybe_handle_hive_create_confirmation(
        self,
        user_input: str,
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        lowered = user_input.strip()
        is_positive = bool(
            self._HIVE_CONFIRM_POSITIVE_STRICT.match(lowered) or self._HIVE_CONFIRM_POSITIVE_LOOSE.match(lowered)
        )
        is_negative = bool(self._HIVE_CONFIRM_NEGATIVE.match(lowered))
        pending = self._load_pending_hive_create(
            session_id=session_id,
            source_context=source_context,
            fallback_task_id=task.task_id,
            allow_history_recovery=is_positive or is_negative,
        )
        if pending is None:
            return None

        if is_positive:
            self._clear_hive_create_pending(session_id)
            return self._execute_confirmed_hive_create(
                pending, task=task, session_id=session_id, source_context=source_context,
                user_input=user_input,
            )

        if is_negative:
            self._clear_hive_create_pending(session_id)
            return self._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response="Got it -- Hive task discarded. What's next?",
                confidence=0.95,
                source_context=source_context,
                reason="hive_topic_create_cancelled",
                success=True,
                details={"status": "cancelled"},
                mode_override="tool_executed",
                task_outcome="cancelled",
                workflow_summary=self._action_workflow_summary(
                    operator_kind="hive.create_topic",
                    dispatch_status="cancelled",
                    details={"action_id": ""},
                ),
            )

        return None

    def _has_pending_hive_create_confirmation(
        self,
        *,
        session_id: str,
        hive_state: dict[str, Any],
        source_context: dict[str, object] | None,
    ) -> bool:
        pending = self._hive_create_pending.get(session_id)
        if pending and str(pending.get("title") or "").strip():
            return True

        payload = dict(hive_state.get("interaction_payload") or {})
        stored = dict(payload.get("pending_hive_create") or {})
        if str(stored.get("title") or "").strip():
            return True

        recovered = self._recover_hive_create_pending_from_history(
            history=list((source_context or {}).get("conversation_history") or []),
            fallback_task_id="",
        )
        return recovered is not None

    def _is_pending_hive_create_confirmation_input(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        hive_state: dict[str, Any] | None = None,
    ) -> bool:
        clean = " ".join(str(user_input or "").split()).strip()
        if not clean:
            return False
        is_confirmation = bool(
            self._HIVE_CONFIRM_POSITIVE_STRICT.match(clean)
            or self._HIVE_CONFIRM_POSITIVE_LOOSE.match(clean)
            or self._HIVE_CONFIRM_NEGATIVE.match(clean)
        )
        if not is_confirmation:
            return False
        state = hive_state or session_hive_state(session_id)
        return self._has_pending_hive_create_confirmation(
            session_id=session_id,
            hive_state=state,
            source_context=source_context,
        )

    def _execute_confirmed_hive_create(
        self,
        pending: dict[str, Any],
        *,
        task: Any,
        session_id: str,
        source_context: dict[str, object] | None,
        user_input: str,
    ) -> dict[str, Any]:
        title = pending["title"]
        summary = pending["summary"]
        topic_tags = pending["topic_tags"]
        linked_task_id = pending.get("task_id") or task.task_id
        auto_start_research = bool(pending.get("auto_start_research")) or self._wants_hive_create_auto_start(user_input)

        try:
            result = self.public_hive_bridge.create_public_topic(
                title=title,
                summary=summary,
                topic_tags=topic_tags,
                linked_task_id=linked_task_id,
                idempotency_key=f"{linked_task_id}:hive_create",
            )
        except Exception as exc:
            error_text = str(exc or "").strip()
            status = "invalid_auth" if "unauthorized" in error_text.lower() else "topic_failed"
            return self._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response=self._hive_topic_create_failure_text(status),
                confidence=0.46,
                source_context=source_context,
                reason=f"hive_topic_create_{status}",
                success=False,
                details={"status": status, "error": error_text},
                mode_override="tool_failed",
                task_outcome="failed",
                workflow_summary=self._action_workflow_summary(
                    operator_kind="hive.create_topic",
                    dispatch_status=status,
                    details={"action_id": ""},
                ),
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

        with contextlib.suppress(Exception):
            self.hive_activity_tracker.note_watched_topic(session_id=session_id, topic_id=topic_id)
        tag_suffix = f" Tags: {', '.join(topic_tags[:6])}." if topic_tags else ""
        response = f"Created Hive task `{title}` (#{topic_id[:8]}).{tag_suffix}"
        if auto_start_research:
            signal = {"topic_id": topic_id, "title": title}
            self._sync_public_presence(status="busy", source_context=source_context)
            research_result = research_topic_from_signal(
                signal,
                public_hive_bridge=self.public_hive_bridge,
                curiosity=self.curiosity,
                hive_activity_tracker=self.hive_activity_tracker,
                session_id=session_id,
                auto_claim=True,
            )
            if research_result.ok:
                set_hive_interaction_state(
                    session_id,
                    mode="hive_task_active",
                    payload={
                        "active_topic_id": topic_id,
                        "active_title": title,
                        "claim_id": str(research_result.claim_id or "").strip(),
                    },
                )
                response = f"{response} Started Hive research on `{title}`."
                if research_result.claim_id:
                    response = f"{response} Claim `{str(research_result.claim_id)[:8]}` is active."
            else:
                failure_text = str(research_result.response_text or "").strip()
                if failure_text:
                    response = f"{response} The task is live, but starting research failed: {failure_text}"
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

    def _check_hive_duplicate(self, title: str, summary: str) -> dict[str, Any] | None:
        """Check if a similar hive topic exists within the last 3 days."""
        try:
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
            topics = self.public_hive_bridge.list_public_topics(limit=50)
            title_tokens = set(title.lower().split())
            summary_tokens = set(summary.lower().split()[:30])
            all_tokens = title_tokens | summary_tokens
            stop_words = {"the", "a", "an", "is", "to", "for", "on", "in", "of", "and", "or", "how", "what", "why", "create", "task", "new", "hive"}
            meaningful = all_tokens - stop_words
            if not meaningful:
                return None
            for topic in topics:
                topic_date = str(topic.get("updated_at") or topic.get("created_at") or "")
                if topic_date and topic_date < cutoff:
                    continue
                t_title = str(topic.get("title") or "").lower()
                t_summary = str(topic.get("summary") or "").lower()
                t_tokens = set(t_title.split()) | set(t_summary.split()[:30])
                overlap = meaningful & t_tokens
                if len(overlap) >= max(2, len(meaningful) * 0.5):
                    return topic
        except Exception:
            pass
        return None

    @staticmethod
    def _clean_hive_title(raw: str) -> str:
        """Basic cleanup: strip command prefixes, fix common doubled chars, capitalize."""
        title = re.sub(
            r"^(?:create\s+(?:a\s+)?(?:hive\s+)?task\s*[-:—]*\s*)", "", raw, flags=re.IGNORECASE,
        ).strip()
        title = re.sub(r"^[-:—]+\s*", "", title).strip()
        if title and title[0].islower():
            title = title[0].upper() + title[1:]
        return title or raw

    def _extract_hive_topic_create_draft(self, text: str) -> dict[str, Any] | None:
        clean = " ".join(str(text or "").split()).strip()
        lowered = clean.lower()
        if not self._looks_like_hive_topic_create_request(lowered):
            return None

        sections = {
            "title": re.search(r"\b(?:name it|title|call it|called)\b\s*[:=-]?\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
            "task": re.search(r"\btask\b\s*[:=-]\s*(.+?)(?=(?:\b(?:goal|summary)\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
            "goal": re.search(r"\bgoal\b\s*[:=-]\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
            "summary": re.search(r"\bsummary\b\s*[:=-]\s*(.+?)(?=(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
            "tags": re.search(r"\b(?:topic tags?|tags?)\b\s*[:=-]\s*(.+)$", clean, re.IGNORECASE),
        }
        title = ""
        if sections["title"] is not None:
            title = str(sections["title"].group(1) or "")
        elif sections["task"] is not None:
            title = str(sections["task"].group(1) or "")
        elif ":" in clean:
            title = clean.rsplit(":", 1)[-1]
        else:
            title = re.sub(r"^.*?\bhive\b[?!.,:;-]*\s*", "", clean, flags=re.IGNORECASE)
        title = re.sub(r"^(?:name it|title|call it|called)\b\s*[:=-]?\s*", "", title, flags=re.IGNORECASE)
        title = re.sub(
            r"^(?:(?:ok\s+)?(?:lets?|let'?s|can you|please|pls|now)\s+)*"
            r"(?:create|make|start|open|add)\s+"
            r"(?:(?:a|the|new|hive|brain hive|this)\s+)*"
            r"(?:task|topic|thread)\s*"
            r"(?:(?:on|in|for|to|at)\s+(?:(?:the\s+)?(?:hive|hive mind|brain hive))\s*)?",
            "", title, flags=re.IGNORECASE,
        ).strip().lstrip("-–—:;/.,!? ")
        if not title:
            for prefix in ("create task", "create new task", "create hive task", "new task", "add task"):
                if clean.lower().startswith(prefix):
                    title = clean[len(prefix):].strip().lstrip("-:–/")
                    break
        if re.match(r"^.{0,30}---+", title):
            title = re.sub(r"^.{0,30}---+\s*", "", title).strip()
        if " - " in title and len(title.split(" - ", 1)[1].strip()) > 15:
            title = title.split(" - ", 1)[1].strip()
        title = self._strip_wrapping_quotes(" ".join(title.split()).strip().strip("."))

        summary = ""
        if sections["summary"] is not None:
            summary = self._strip_wrapping_quotes(" ".join(str(sections["summary"].group(1) or "").split()).strip().strip("."))
        elif sections["goal"] is not None:
            summary = self._strip_wrapping_quotes(" ".join(str(sections["goal"].group(1) or "").split()).strip().strip("."))
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
            "auto_start_research": self._wants_hive_create_auto_start(clean),
        }

    def _remember_hive_create_pending(self, session_id: str, pending: dict[str, Any]) -> None:
        payload = {
            "title": str(pending.get("title") or "").strip(),
            "summary": str(pending.get("summary") or "").strip(),
            "topic_tags": [
                str(item).strip()
                for item in list(pending.get("topic_tags") or [])
                if str(item).strip()
            ][:8],
            "task_id": str(pending.get("task_id") or "").strip(),
            "auto_start_research": bool(pending.get("auto_start_research")),
        }
        self._hive_create_pending[session_id] = dict(payload)
        set_hive_interaction_state(
            session_id,
            mode="hive_topic_create_pending",
            payload={"pending_hive_create": payload},
        )

    def _clear_hive_create_pending(self, session_id: str) -> None:
        self._hive_create_pending.pop(session_id, None)
        hive_state = session_hive_state(session_id)
        if str(hive_state.get("interaction_mode") or "").strip().lower() == "hive_topic_create_pending":
            clear_hive_interaction_state(session_id)

    def _load_pending_hive_create(
        self,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        fallback_task_id: str,
        allow_history_recovery: bool,
    ) -> dict[str, Any] | None:
        pending = self._hive_create_pending.get(session_id)
        if pending:
            return dict(pending)

        hive_state = session_hive_state(session_id)
        payload = dict(hive_state.get("interaction_payload") or {})
        stored = dict(payload.get("pending_hive_create") or {})
        if stored and str(stored.get("title") or "").strip():
            recovered = {
                "title": str(stored.get("title") or "").strip(),
                "summary": str(stored.get("summary") or "").strip() or str(stored.get("title") or "").strip(),
                "topic_tags": [
                    str(item).strip()
                    for item in list(stored.get("topic_tags") or [])
                    if str(item).strip()
                ][:8],
                "task_id": str(stored.get("task_id") or "").strip() or fallback_task_id,
                "auto_start_research": bool(stored.get("auto_start_research")),
            }
            self._hive_create_pending[session_id] = dict(recovered)
            return recovered

        if not allow_history_recovery:
            return None
        recovered = self._recover_hive_create_pending_from_history(
            history=list((source_context or {}).get("conversation_history") or []),
            fallback_task_id=fallback_task_id,
        )
        if recovered is not None:
            self._remember_hive_create_pending(session_id, recovered)
        return recovered

    def _recover_hive_create_pending_from_history(
        self,
        *,
        history: list[dict[str, Any]],
        fallback_task_id: str,
    ) -> dict[str, Any] | None:
        recent_messages = [dict(item) for item in list(history or [])[-8:] if isinstance(item, dict)]
        assistant_preview: str = ""
        latest_user_draft: dict[str, Any] | None = None
        for message in reversed(recent_messages):
            role = str(message.get("role") or "").strip().lower()
            content = str(message.get("content") or "")
            if not content:
                continue
            if not assistant_preview and role == "assistant" and "ready to post this to the public hive" in content.lower():
                assistant_preview = content
            if latest_user_draft is None and role == "user":
                draft = self._extract_hive_topic_create_draft(content)
                if draft is not None and str(draft.get("title") or "").strip():
                    latest_user_draft = draft
            if assistant_preview and latest_user_draft is not None:
                break

        preview_title = ""
        preview_tags: list[str] = []
        if assistant_preview:
            title_match = re.search(
                r"Ready to post this to the public Hive:\s*\*\*(.+?)\*\*",
                assistant_preview,
                re.IGNORECASE | re.DOTALL,
            )
            if title_match is not None:
                preview_title = self._clean_hive_title(self._strip_wrapping_quotes(" ".join(str(title_match.group(1) or "").split())))
            tag_match = re.search(r"\bTags:\s*(.+?)(?:\n|$)", assistant_preview, re.IGNORECASE)
            if tag_match is not None:
                preview_tags = [
                    normalized
                    for normalized in (
                        self._normalize_hive_topic_tag(item)
                        for item in re.split(r"[,;|/]+", str(tag_match.group(1) or ""))
                    )
                    if normalized
                ][:8]

        if not preview_title and latest_user_draft is None:
            return None

        title = preview_title or str((latest_user_draft or {}).get("title") or "").strip()
        if not title:
            return None
        return {
            "title": title,
            "summary": str((latest_user_draft or {}).get("summary") or "").strip() or title,
            "topic_tags": preview_tags or [
                str(item).strip()
                for item in list((latest_user_draft or {}).get("topic_tags") or [])
                if str(item).strip()
            ][:8],
            "task_id": fallback_task_id,
            "auto_start_research": bool((latest_user_draft or {}).get("auto_start_research")),
        }

    @staticmethod
    def _wants_hive_create_auto_start(text: str) -> bool:
        compact = " ".join(str(text or "").split()).strip().lower()
        if not compact:
            return False
        return any(
            phrase in compact
            for phrase in (
                "start working on it",
                "start working on this",
                "start on it",
                "start on this",
                "start researching",
                "start research",
                "work on it",
                "work on this",
                "research it",
                "research this",
                "go ahead and start",
                "create it and start",
                "post it and start",
                "start there",
            )
        )

    def _prepare_public_hive_topic_copy(
        self,
        *,
        raw_input: str,
        title: str,
        summary: str,
    ) -> dict[str, Any]:
        clean_title = " ".join(str(title or "").split()).strip()
        clean_summary = " ".join(str(summary or "").split()).strip() or clean_title
        if self._looks_like_raw_chat_transcript(raw_input) and not self._has_structured_hive_public_brief(raw_input):
            return {
                "ok": False,
                "reason": "hive_topic_create_transcript_blocked",
                "privacy_risks": ["raw_chat_transcript"],
                "response": (
                    "That looks like a raw chat log/transcript. I won't dump private chat into the public Hive. "
                    "Give me a public-safe brief in plain language, or mark the shareable parts with `Task:` and optional `Goal:`. "
                    "I can still keep the raw chat local."
                ),
            }

        original_risks = text_privacy_risks(f"{clean_title}\n{clean_summary}")
        sanitized_title = self._sanitize_public_hive_text(clean_title)
        sanitized_summary = self._sanitize_public_hive_text(clean_summary) or sanitized_title
        remaining_risks = text_privacy_risks(f"{sanitized_title}\n{sanitized_summary}")
        hard_risks = [
            risk
            for risk in list(original_risks or [])
            if risk.startswith("restricted_term:") or risk in _HIVE_CREATE_HARD_PRIVACY_RISKS
        ]
        unresolved_risks = [
            risk
            for risk in list(remaining_risks or [])
            if risk.startswith("restricted_term:") or risk in _HIVE_CREATE_HARD_PRIVACY_RISKS or risk in {"email", "filesystem_path", "secret_assignment", "openai_key", "github_token", "aws_access_key", "slack_token"}
        ]
        if hard_risks or unresolved_risks:
            risk_labels = ", ".join((hard_risks or unresolved_risks)[:4])
            return {
                "ok": False,
                "reason": "hive_topic_create_privacy_blocked",
                "privacy_risks": hard_risks or unresolved_risks,
                "response": (
                    "I won't create that Hive task because the public brief still looks private "
                    f"({risk_labels}). I can help rewrite it into a public-safe research brief."
                ),
            }

        preview_note = ""
        if sanitized_title != clean_title or sanitized_summary != clean_summary:
            redacted_labels = [
                risk
                for risk in list(original_risks or [])
                if risk not in _HIVE_CREATE_HARD_PRIVACY_RISKS and not risk.startswith("restricted_term:")
            ]
            if not redacted_labels:
                redacted_labels = ["private_fields"]
            preview_note = (
                "\n\nSafety: I redacted private-looking fields before preview "
                f"({', '.join(redacted_labels[:4])})."
            )

        return {
            "ok": True,
            "title": sanitized_title[:180],
            "summary": sanitized_summary[:4000],
            "preview_note": preview_note,
            "privacy_risks": original_risks,
        }

    @staticmethod
    def _sanitize_public_hive_text(text: str) -> str:
        sanitized = redact_text(str(text or ""))
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        return sanitized

    @staticmethod
    def _has_structured_hive_public_brief(text: str) -> bool:
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return False
        return bool(
            re.search(r"\b(?:task|goal|summary|title|name it|call it|called)\b\s*[:=-]", clean, re.IGNORECASE)
        )

    @staticmethod
    def _looks_like_raw_chat_transcript(text: str) -> bool:
        raw = str(text or "")
        if not raw.strip():
            return False
        hits = 0
        patterns = (
            r"(?m)^\s*(?:NULLA|You|User|Assistant|U)\s*$",
            r"\b\d{1,2}:\d{2}\b",
            r"(?m)^\s*/new\s*$",
            r"∅",
            r"(?m)^\s*(?:U|A)\s*$",
        )
        for pattern in patterns:
            if re.search(pattern, raw, re.IGNORECASE):
                hits += 1
        return hits >= 2

    def _looks_like_hive_topic_create_request(self, lowered: str) -> bool:
        text = str(lowered or "").strip().lower()
        if not text:
            return False
        has_create = any(marker in text for marker in ("create", "make", "start", "new task", "new topic", "open a", "open new"))
        has_target = any(marker in text for marker in ("task", "topic", "thread"))
        if not (has_create and has_target):
            return False
        if "hive" not in text and "topic" not in text and "create" not in text:
            return False
        return not any(marker in text for marker in ("claim task", "pull hive tasks", "open hive tasks", "open tasks", "show me", "what do we have", "any tasks", "list tasks", "ignore hive", "research complete", "status"))

    def _infer_hive_topic_tags(self, title: str) -> list[str]:
        stopwords = {
            "a", "about", "all", "also", "an", "and", "any", "are", "as", "at",
            "be", "been", "being", "best", "better", "build", "building", "but",
            "by", "can", "could", "create", "do", "does", "doing", "each",
            "fast", "fastest", "find", "for", "from", "future", "get", "good",
            "got", "had", "has", "have", "her", "here", "him", "his", "how",
            "human", "if", "improving", "in", "into", "is", "it", "its",
            "just", "know", "let", "lets", "like", "look", "make", "more",
            "most", "much", "my", "need", "new", "not", "now", "of", "on",
            "one", "only", "or", "other", "our", "out", "over", "own",
            "preserving", "pure", "put", "really", "reuse", "self", "she",
            "should", "so", "some", "such", "task", "than", "that", "the",
            "their", "them", "then", "there", "these", "they", "thing",
            "this", "those", "to", "too", "try", "up", "us", "use", "very",
            "want", "was", "way", "we", "well", "were", "what", "when",
            "where", "which", "while", "who", "why", "will", "with", "would",
            "you", "your",
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
            return "Hive topic creation is configured incompletely on this runtime, so I can't post the task yet. Hive truth: future/unsupported."
        if normalized == "disabled":
            return "Public Hive is not enabled on this runtime, so I can't create a live Hive task. Hive truth: future/unsupported."
        if normalized == "missing_auth":
            return "Hive task creation is disabled here because public Hive auth is not configured for writes. Hive truth: future/unsupported."
        if normalized == "invalid_auth":
            return "Hive task creation is configured, but the live Hive rejected this runtime's write auth. I need to refresh public Hive auth before posting."
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
        response = " ".join(part for part in summary if part)
        if self._is_chat_truth_surface(source_context):
            return self._chat_surface_hive_wording_result(
                session_id=session_id,
                user_input=clean,
                source_context=source_context,
                response_class=ResponseClass.TASK_STATUS,
                reason="hive_status_model_wording",
                observations=self._chat_surface_hive_status_observations(
                    topic_id=resolved_topic_id,
                    title=title,
                    status=status,
                    execution_state=execution_state,
                    active_claim_count=active_claim_count,
                    artifact_count=artifact_count,
                    post_count=post_count,
                    latest_post_kind=latest_post_kind,
                    latest_post_body=latest_post_body,
                    truth_payload=packet,
                ),
                fallback_response=(
                    f"I pulled current Hive status for `{title}` in this run, but I couldn't produce a clean final summary."
                ),
            )
        return self._fast_path_result(
            session_id=session_id,
            user_input=clean,
            response=response,
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
        shown_titles: list[str],
        history_has_task_list: bool,
    ) -> bool:
        text = str(lowered or "").strip().lower()
        normalized_text = self._normalize_hive_topic_text(text)
        if topic_hint:
            bare_hint = f"#{topic_hint}"
            compact_text = re.sub(r"\s+", "", text.rstrip(".!?"))
            if compact_text in {topic_hint, bare_hint}:
                return True
            if any(
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
            ):
                return True
            return bool(bare_hint in compact_text and any(phrase in text for phrase in ("full research", "research on this", "research this", "do this in full", "do all step by step", "lets do this", "let's do this", "do this", "start this", "start that", "work on this", "work on that", "deliver to hive", "deliver it to hive", "post it to hive", "submit it to hive", "pls", "please", "full")))
        if (has_pending_topics or history_has_task_list) and shown_titles:
            normalized_titles = [
                self._normalize_hive_topic_text(str(title or ""))
                for title in list(shown_titles or [])
                if str(title or "").strip()
            ]
            if normalized_text and normalized_text in normalized_titles:
                return True
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
                "do all step by step",
                "deliver to hive",
                "deliver it to hive",
                "post it to hive",
                "submit it to hive",
                "proceed",
                "carry on",
                "continue",
                "do all",
                "start working",
                "all good",
                "proceed with next steps",
                "proceed with that",
                "just do it",
                "deliver it",
                "submit it",
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
                "do all step by step",
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
        return bool("research" in text and "pick one" in text)

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
