from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from adapters.base_adapter import ModelRequest
from core import audit_logger, policy_engine
from core.cache_freshness_policy import default_ttl_seconds, freshness_score, should_revalidate
from core.candidate_knowledge_lane import build_task_hash, get_exact_candidate, record_candidate_output
from core.model_failover import select_with_failover
from core.model_health import record_provider_failure, record_provider_success
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest
from core.model_trust import output_trust_score
from core.output_validator import validate_provider_output
from core.prompt_normalizer import normalize_prompt
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
            surface=surface,
            source_context=source_context,
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
        surface: str,
        source_context: dict[str, Any] | None,
    ) -> ModelExecutionDecision:
        selection_request = ModelSelectionRequest(
            task_kind=task_kind,
            output_mode=output_mode,
            preferred_source_types=["http", "local_path", "subprocess"],
            allow_paid_fallback=bool(allow_paid_fallback) and not policy_engine.local_only_mode(),
            min_trust=0.45,
        )
        attempted: list[str] = []
        failover_used = False

        while True:
            decision = select_with_failover(self.registry, selection_request, attempted_provider_ids=attempted)
            manifest = decision.selected
            if not manifest:
                return ModelExecutionDecision(
                    source="no_provider_available",
                    task_hash=task_hash,
                    used_model=False,
                    failover_used=failover_used,
                    details={"attempted": decision.attempted_provider_ids, "reason": decision.reason},
                )

            adapter = self.registry.build_adapter(manifest)
            health = adapter.health_check()
            if not bool(health.get("ok")):
                attempted.append(manifest.provider_id)
                failover_used = True
                record_provider_failure(manifest.provider_id, error=str(health.get("error") or "health_check_failed"))
                audit_logger.log(
                    "model_provider_unhealthy",
                    target_id=manifest.provider_id,
                    target_type="model_provider",
                    trace_id=getattr(task, "task_id", None),
                    details={"health": health},
                )
                continue

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
            request = ModelRequest(
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
                metadata=internal_request.metadata,
                attachments=internal_request.attachments,
            )
            try:
                response = (
                    adapter.run_structured_task(request)
                    if output_mode in _STRUCTURED_OUTPUT_MODES
                    else adapter.run_text_task(request)
                )
                record_provider_success(manifest.provider_id)
            except Exception as exc:
                attempted.append(manifest.provider_id)
                failover_used = True
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
                continue

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
                },
            )
            return ModelExecutionDecision(
                source="provider_execution",
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
                details={"warnings": validation.warnings, "contract_error": validation.error},
            )


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
