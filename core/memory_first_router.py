from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any

from adapters.base_adapter import ModelAdapter, ModelRequest, ModelResponse
from core import audit_logger, policy_engine
from core.cache_freshness_policy import default_ttl_seconds, freshness_score, should_revalidate
from core.candidate_knowledge_lane import build_task_hash, get_exact_candidate, record_candidate_output
from core.compute_mode import get_active_compute_budget
from core.model_health import circuit_is_open, record_provider_failure, record_provider_success
from core.model_registry import ModelRegistry
from core.model_selection_policy import provider_cost_class
from core.model_trust import output_trust_score
from core.output_validator import validate_provider_output
from core.prompt_normalizer import normalize_prompt
from core.provider_routing import ProviderRole, rank_provider_candidates
from core.runtime_task_events import emit_runtime_event
from core.task_router import model_execution_profile

_STRUCTURED_OUTPUT_MODES = {"json_object", "action_plan", "tool_intent", "summary_block"}
_CHAT_TRUTH_SURFACES = {"channel", "openclaw", "api"}


@dataclass
class ModelExecutionDecision:
    source: str
    task_hash: str
    provider_id: str | None = None
    provider_name: str | None = None
    model_name: str | None = None
    output_text: str | None = None
    structured_output: Any = None
    confidence: float = 0.0
    trust_score: float = 0.0
    used_model: bool = False
    cache_hit: bool = False
    candidate_id: str | None = None
    failover_used: bool = False
    validation_state: str = "not_run"
    details: dict[str, Any] = field(default_factory=dict)

    def as_plan_candidate(self) -> dict[str, Any] | None:
        if not self.output_text:
            return None
        summary = self.output_text.strip().splitlines()[0][:220] if self.output_text.strip() else "Model-generated candidate"
        steps = []
        if isinstance(self.structured_output, dict):
            raw_steps = self.structured_output.get("steps") or []
            if isinstance(raw_steps, list):
                steps = [str(step) for step in raw_steps[:8]]
        return {
            "summary": summary,
            "resolution_pattern": steps,
            "score": self.trust_score or self.confidence,
            "source_type": "model_candidate",
            "source_node_id": self.provider_id,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "candidate_id": self.candidate_id,
            "structured_output": self.structured_output,
            "validation_state": self.validation_state,
        }


class MemoryFirstRouter:
    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()

    def resolve(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        force_model: bool = False,
        surface: str = "cli",
        source_context: dict[str, Any] | None = None,
    ) -> ModelExecutionDecision:
        force_model = _force_model_on_chat_surface(
            force_model=force_model,
            surface=surface,
            source_context=source_context,
        )
        profile = model_execution_profile(str(classification.get("task_class", "unknown")))
        task_kind = str(profile["task_kind"])
        output_mode = str(profile["output_mode"])
        normalized_input = getattr(interpretation, "reconstructed_text", "") or getattr(task, "task_summary", "")
        task_hash = build_task_hash(
            normalized_input=normalized_input,
            task_class=str(classification.get("task_class", "unknown")),
            output_mode=output_mode,
        )

        if not force_model:
            cached = get_exact_candidate(task_hash, output_mode=output_mode)
            if cached and not should_revalidate(cached) and float(cached.get("trust_score") or 0.0) >= 0.56:
                return ModelExecutionDecision(
                    source="exact_cache_hit",
                    task_hash=task_hash,
                    provider_id=f"{cached['provider_name']}:{cached['model_name']}",
                    provider_name=cached["provider_name"],
                    model_name=cached["model_name"],
                    output_text=str(cached.get("normalized_output") or ""),
                    structured_output=cached.get("structured_output"),
                    confidence=float(cached.get("confidence") or 0.0),
                    trust_score=float(cached.get("trust_score") or 0.0),
                    cache_hit=True,
                    used_model=False,
                    candidate_id=cached["candidate_id"],
                    validation_state=str(cached.get("validation_state") or "cached"),
                    details={"reason": "fresh_exact_candidate_cache"},
                )

            if _memory_is_good_enough(context_result, classification):
                return ModelExecutionDecision(
                    source="memory_hit",
                    task_hash=task_hash,
                    used_model=False,
                    details={"reason": "relevant_local_memory_sufficient"},
                )

        return self._execute_provider_task(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            task_hash=task_hash,
            task_kind=task_kind,
            output_mode=output_mode,
            allow_paid_fallback=bool(profile.get("allow_paid_fallback", False)),
            provider_role=_provider_role_for_request(profile.get("provider_role")),
            surface=surface,
            source_context=source_context,
        )

    def resolve_tool_intent(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        surface: str = "cli",
        source_context: dict[str, Any] | None = None,
    ) -> ModelExecutionDecision:
        normalized_input = getattr(interpretation, "reconstructed_text", "") or getattr(task, "task_summary", "")
        task_hash = build_task_hash(
            normalized_input=normalized_input,
            task_class=str(classification.get("task_class", "unknown")),
            output_mode="tool_intent",
        )
        return self._execute_provider_task(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            task_hash=task_hash,
            task_kind="tool_intent",
            output_mode="tool_intent",
            allow_paid_fallback=False,
            provider_role="drone",
            surface=surface,
            source_context=source_context,
        )

    def _build_request(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        output_mode: str,
        task_kind: str,
        surface: str,
        source_context: dict[str, Any] | None,
    ) -> ModelRequest:
        internal_request = normalize_prompt(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            output_mode=output_mode,
            task_kind=task_kind,
            trace_id=str(getattr(task, "task_id", "")),
            surface=surface,
            source_context=source_context,
        )
        return ModelRequest(
            task_kind=task_kind,
            prompt=internal_request.user_prompt(),
            system_prompt=internal_request.system_prompt(),
            context=internal_request.context_summary,
            temperature=internal_request.temperature,
            max_output_tokens=internal_request.max_output_tokens,
            messages=internal_request.as_openai_messages(),
            output_mode=output_mode,
            trace_id=internal_request.trace_id,
            contract={"mode": output_mode},
            metadata={
                **dict(internal_request.metadata or {}),
                **({"task_envelope": dict((source_context or {}).get("task_envelope") or {})} if (source_context or {}).get("task_envelope") else {}),
                **({"task_role": str((source_context or {}).get("task_role") or "")} if (source_context or {}).get("task_role") else {}),
            },
            attachments=internal_request.attachments,
        )

    def _invoke_manifest(
        self,
        *,
        manifest: Any,
        request: ModelRequest,
        output_mode: str,
        task: Any,
        source_context: dict[str, Any] | None,
    ) -> tuple[ModelAdapter | None, ModelResponse | None, str | None]:
        if circuit_is_open(manifest.provider_id):
            return None, None, "circuit_open"

        adapter = self.registry.build_adapter(manifest)
        health = adapter.health_check()
        if not bool(health.get("ok")):
            record_provider_failure(manifest.provider_id, error=str(health.get("error") or "health_check_failed"))
            audit_logger.log(
                "model_provider_unhealthy",
                target_id=manifest.provider_id,
                target_type="model_provider",
                trace_id=getattr(task, "task_id", None),
                details={"health": health},
            )
            return adapter, None, str(health.get("error") or "health_check_failed")

        try:
            if output_mode in _STRUCTURED_OUTPUT_MODES:
                response = adapter.run_structured_task(request)
            elif _streaming_requested(source_context, output_mode=output_mode) and adapter.supports_streaming():
                response = self._stream_response(
                    adapter=adapter,
                    manifest=manifest,
                    request=request,
                    source_context=source_context,
                )
            else:
                response = adapter.run_text_task(request)
            record_provider_success(manifest.provider_id)
            return adapter, response, None
        except Exception as exc:
            record_provider_failure(
                manifest.provider_id,
                error=str(exc),
                timeout="timeout" in str(exc).lower(),
            )
            audit_logger.log(
                "model_provider_execution_failed",
                target_id=manifest.provider_id,
                target_type="model_provider",
                trace_id=getattr(task, "task_id", None),
                details={"error": str(exc)},
            )
            return adapter, None, str(exc)

    def _stream_response(
        self,
        *,
        adapter: ModelAdapter,
        manifest: Any,
        request: ModelRequest,
        source_context: dict[str, Any] | None,
    ) -> ModelResponse:
        emitted_chunks: list[str] = []
        raw_events: list[Any] = []
        stream_context = _ephemeral_stream_context(source_context)
        for chunk in adapter.stream_text_task(request):
            if chunk.delta_text:
                emitted_chunks.append(chunk.delta_text)
                emit_runtime_event(
                    stream_context,
                    event_type="model_output_chunk",
                    message=chunk.delta_text,
                    details={
                        "provider_id": manifest.provider_id,
                        "model_name": manifest.model_name,
                    },
                )
            if chunk.raw_event is not None:
                raw_events.append(chunk.raw_event)
        return ModelResponse(
            output_text="".join(emitted_chunks),
            confidence=float(manifest.metadata.get("confidence_baseline") or 0.65),
            raw_response=raw_events,
            provider_id=manifest.provider_id,
            model_name=manifest.model_name,
            output_mode=request.output_mode,
        )

    def _maybe_race_manifests(
        self,
        *,
        ranked_manifests: list[Any],
        request: ModelRequest,
        output_mode: str,
        allow_paid_fallback: bool,
        task: Any,
        source_context: dict[str, Any] | None,
    ) -> tuple[Any | None, ModelAdapter | None, ModelResponse | None, list[str], bool]:
        if _streaming_requested(source_context, output_mode=output_mode):
            return None, None, None, [], False
        if not allow_paid_fallback:
            return None, None, None, [], False
        budget = get_active_compute_budget()
        if int(budget.worker_pool_cap) < 2:
            return None, None, None, [], False
        race_pair = _local_remote_race_pair(ranked_manifests)
        if not race_pair:
            return None, None, None, [], False

        local_manifest, remote_manifest = race_pair
        attempted: list[str] = []
        result_queue: queue.Queue[tuple[Any, ModelAdapter | None, ModelResponse | None, str | None]] = queue.Queue()

        def _worker(manifest: Any) -> None:
            adapter, response, error = self._invoke_manifest(
                manifest=manifest,
                request=request,
                output_mode=output_mode,
                task=task,
                source_context=source_context,
            )
            result_queue.put((manifest, adapter, response, error))

        for manifest in (local_manifest, remote_manifest):
            thread = threading.Thread(
                target=_worker,
                args=(manifest,),
                name=f"nulla-provider-race-{manifest.provider_name}",
                daemon=True,
            )
            thread.start()

        remaining = 2
        while remaining > 0:
            manifest, adapter, response, error = result_queue.get()
            remaining -= 1
            if error or response is None:
                attempted.append(manifest.provider_id)
                continue
            return manifest, adapter, response, attempted, True
        for manifest in (local_manifest, remote_manifest):
            if manifest.provider_id not in attempted:
                attempted.append(manifest.provider_id)
        return None, None, None, attempted, True

    def _decision_from_response(
        self,
        *,
        manifest: Any,
        adapter: ModelAdapter,
        response: ModelResponse,
        task_hash: str,
        task: Any,
        classification: dict[str, Any],
        context_result: Any,
        task_kind: str,
        output_mode: str,
        provider_role: ProviderRole,
        ranked_manifests: list[Any],
        attempted: list[str],
        failover_used: bool,
        source: str,
    ) -> ModelExecutionDecision:
        validation = validate_provider_output(
            provider_id=manifest.provider_id,
            output_mode=output_mode,
            raw_text=response.output_text,
            trace_id=str(getattr(task, "task_id", "")),
        )
        freshness = freshness_score(None, None)
        trust = output_trust_score(
            manifest=manifest,
            raw_confidence=float(response.confidence or 0.5),
            contract_ok=validation.ok,
            trust_penalty=validation.trust_penalty,
            freshness_score=freshness,
            reviewed=False,
            agreement_score=min(1.0, float(context_result.retrieval_confidence_score or 0.0)),
        )
        candidate_id = record_candidate_output(
            task_hash=task_hash,
            task_id=str(getattr(task, "task_id", "")),
            trace_id=str(getattr(task, "task_id", "")),
            task_class=str(classification.get("task_class", "unknown")),
            task_kind=task_kind,
            output_mode=output_mode,
            provider_name=manifest.provider_name,
            model_name=manifest.model_name,
            raw_output=response.output_text,
            normalized_output=validation.normalized_text,
            structured_output=validation.structured_output,
            confidence=float(response.confidence or 0.5),
            trust_score=trust,
            validation_state="valid" if validation.ok else "contract_failed",
            metadata={
                "cost_class": adapter.estimate_cost_class(),
                "warnings": validation.warnings,
                "context_retrieval_confidence": context_result.report.retrieval_confidence,
            },
            provenance={
                **adapter.get_license_metadata(),
                "provider_id": manifest.provider_id,
                "output_mode": output_mode,
            },
            ttl_seconds=default_ttl_seconds(task_kind=task_kind, output_mode=output_mode),
        )
        audit_logger.log(
            "model_candidate_recorded",
            target_id=candidate_id,
            target_type="candidate_knowledge",
            trace_id=str(getattr(task, "task_id", "")),
            details={
                "provider_id": manifest.provider_id,
                "task_kind": task_kind,
                "output_mode": output_mode,
                "validation_ok": validation.ok,
                "trust_score": trust,
                "execution_source": source,
            },
        )
        return ModelExecutionDecision(
            source=source,
            task_hash=task_hash,
            provider_id=manifest.provider_id,
            provider_name=manifest.provider_name,
            model_name=manifest.model_name,
            output_text=validation.normalized_text or response.output_text,
            structured_output=validation.structured_output,
            confidence=float(response.confidence or 0.5),
            trust_score=trust,
            used_model=True,
            candidate_id=candidate_id,
            failover_used=failover_used,
            validation_state="valid" if validation.ok else "contract_failed",
            details={
                "warnings": validation.warnings,
                "contract_error": validation.error,
                "provider_role": provider_role,
                "ranked_candidates": [entry.provider_id for entry in ranked_manifests],
                "attempted": attempted,
            },
        )

    def _execute_provider_task(
        self,
        *,
        task: Any,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        persona: Any,
        task_hash: str,
        task_kind: str,
        output_mode: str,
        allow_paid_fallback: bool,
        provider_role: ProviderRole,
        surface: str,
        source_context: dict[str, Any] | None,
    ) -> ModelExecutionDecision:
        preferred_provider, preferred_model = self._requested_model_preferences(source_context)
        requested_manifest = self._requested_model_manifest(source_context)
        requested_paid_cloud = requested_manifest is not None and provider_cost_class(requested_manifest) == "paid_cloud"
        resolved_allow_paid = (bool(allow_paid_fallback) or requested_paid_cloud) and not policy_engine.local_only_mode()
        ranked_manifests = rank_provider_candidates(
            self.registry,
            task_kind=task_kind,
            output_mode=output_mode,
            role=provider_role,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            allow_paid_fallback=resolved_allow_paid,
            swarm_size=4,
            min_trust=0.45,
        )
        attempted: list[str] = []
        failover_used = False

        if not ranked_manifests:
            return ModelExecutionDecision(
                source="no_provider_available",
                task_hash=task_hash,
                used_model=False,
                failover_used=failover_used,
                details={
                    "attempted": attempted,
                    "reason": "no_ranked_provider",
                    "provider_role": provider_role,
                    "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                    "ranked_candidates": [],
                },
            )

        request = self._build_request(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            output_mode=output_mode,
            task_kind=task_kind,
            surface=surface,
            source_context=source_context,
        )

        raced_manifest, raced_adapter, raced_response, raced_attempted, race_used = self._maybe_race_manifests(
            ranked_manifests=ranked_manifests,
            request=request,
            output_mode=output_mode,
            allow_paid_fallback=resolved_allow_paid,
            task=task,
            source_context=source_context,
        )
        if race_used:
            attempted.extend(raced_attempted)
            failover_used = True
            if raced_manifest is not None and raced_adapter is not None and raced_response is not None:
                return self._decision_from_response(
                    manifest=raced_manifest,
                    adapter=raced_adapter,
                    response=raced_response,
                    task_hash=task_hash,
                    task=task,
                    classification=classification,
                    context_result=context_result,
                    task_kind=task_kind,
                    output_mode=output_mode,
                    provider_role=provider_role,
                    ranked_manifests=ranked_manifests,
                    attempted=attempted,
                    failover_used=failover_used,
                    source="provider_race_winner",
                )

        skipped_provider_ids = {manifest.provider_id for manifest in ranked_manifests if manifest.provider_id in attempted}
        for manifest in ranked_manifests:
            if manifest.provider_id in skipped_provider_ids:
                continue
            adapter, response, error = self._invoke_manifest(
                manifest=manifest,
                request=request,
                output_mode=output_mode,
                task=task,
                source_context=source_context,
            )
            if error or adapter is None or response is None:
                attempted.append(manifest.provider_id)
                failover_used = True
                continue
            return self._decision_from_response(
                manifest=manifest,
                adapter=adapter,
                response=response,
                task_hash=task_hash,
                task=task,
                classification=classification,
                context_result=context_result,
                task_kind=task_kind,
                output_mode=output_mode,
                provider_role=provider_role,
                ranked_manifests=ranked_manifests,
                attempted=attempted,
                failover_used=failover_used,
                source="provider_execution",
            )

        return ModelExecutionDecision(
            source="no_provider_available",
            task_hash=task_hash,
            used_model=False,
            failover_used=failover_used,
            details={
                "attempted": attempted,
                "reason": "all_ranked_providers_failed",
                "provider_role": provider_role,
                "requested_model": str((source_context or {}).get("requested_model") or "").strip(),
                "ranked_candidates": [entry.provider_id for entry in ranked_manifests],
            },
        )

    def _requested_model_preferences(self, source_context: dict[str, Any] | None) -> tuple[str | None, str | None]:
        requested_model = str((source_context or {}).get("requested_model") or "").strip()
        if not requested_model:
            return None, None
        lowered = requested_model.lower()
        if lowered in {"nulla", "nulla:latest"}:
            return None, None

        manifests = self.registry.list_manifests(enabled_only=True)
        for manifest in manifests:
            if requested_model == manifest.provider_id:
                return manifest.provider_name, manifest.model_name
        for manifest in manifests:
            if requested_model == manifest.model_name:
                return None, manifest.model_name

        provider_hint, separator, model_hint = requested_model.partition(":")
        if separator and provider_hint and model_hint:
            if any(provider_hint == manifest.provider_name for manifest in manifests):
                return provider_hint, model_hint
        return None, requested_model

    def _requested_model_manifest(self, source_context: dict[str, Any] | None) -> Any | None:
        requested_model = str((source_context or {}).get("requested_model") or "").strip()
        if not requested_model:
            return None
        lowered = requested_model.lower()
        if lowered in {"nulla", "nulla:latest"}:
            return None

        manifests = self.registry.list_manifests(enabled_only=True)
        for manifest in manifests:
            if requested_model == manifest.provider_id:
                return manifest

        model_matches = [manifest for manifest in manifests if requested_model == manifest.model_name]
        if len(model_matches) == 1:
            return model_matches[0]

        provider_hint, separator, model_hint = requested_model.partition(":")
        if separator and provider_hint and model_hint:
            for manifest in manifests:
                if provider_hint == manifest.provider_name and model_hint == manifest.model_name:
                    return manifest
        return None


def _memory_is_good_enough(context_result: Any, classification: dict[str, Any]) -> bool:
    if getattr(context_result, "local_candidates", None):
        top = float(context_result.local_candidates[0].get("score") or 0.0)
        if top >= 0.64:
            return True
    retrieval_confidence = float(getattr(context_result, "retrieval_confidence_score", 0.0) or 0.0)
    task_class = str(classification.get("task_class", "unknown"))
    if task_class in {"shell_guidance", "file_inspection"} and retrieval_confidence >= 0.45:
        return True
    return retrieval_confidence >= 0.72


def _force_model_on_chat_surface(
    *,
    force_model: bool,
    surface: str,
    source_context: dict[str, Any] | None,
) -> bool:
    if force_model:
        return True
    normalized_surface = str(surface or "").strip().lower()
    if normalized_surface in _CHAT_TRUTH_SURFACES:
        return True
    source_surface = str((source_context or {}).get("surface", "") or "").strip().lower()
    return source_surface in _CHAT_TRUTH_SURFACES


def _provider_role_for_request(role: object) -> ProviderRole:
    candidate = str(role or "auto").strip().lower()
    if candidate in {"drone", "queen"}:
        return candidate
    return "auto"


def _streaming_requested(source_context: dict[str, Any] | None, *, output_mode: str) -> bool:
    if output_mode != "plain_text":
        return False
    return bool(str((source_context or {}).get("runtime_event_stream_id") or "").strip())


def _ephemeral_stream_context(source_context: dict[str, Any] | None) -> dict[str, Any]:
    stream_id = str((source_context or {}).get("runtime_event_stream_id") or "").strip()
    if not stream_id:
        return {}
    return {"runtime_event_stream_id": stream_id}


def _manifest_locality(manifest: Any) -> str:
    deployment_class = str(getattr(manifest, "metadata", {}).get("deployment_class") or "").strip().lower()
    if deployment_class in {"local", "remote"}:
        return deployment_class
    base_url = str(getattr(manifest, "runtime_config", {}).get("base_url") or "").strip().lower()
    if base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost"):
        return "local"
    return "remote"


def _local_remote_race_pair(ranked_manifests: list[Any]) -> tuple[Any, Any] | None:
    local_manifest = next((manifest for manifest in ranked_manifests if _manifest_locality(manifest) == "local"), None)
    remote_manifest = next((manifest for manifest in ranked_manifests if _manifest_locality(manifest) == "remote"), None)
    if local_manifest is None or remote_manifest is None:
        return None
    if local_manifest.provider_id == remote_manifest.provider_id:
        return None
    return local_manifest, remote_manifest
