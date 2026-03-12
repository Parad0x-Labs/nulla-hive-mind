from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from adapters.base_adapter import ModelRequest
from core import audit_logger
from core.candidate_knowledge_lane import build_task_hash, record_candidate_output
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest
from core.model_trust import output_trust_score
from core.output_validator import validate_provider_output
from network.signer import get_local_peer_id


@dataclass
class TeacherCandidate:
    task_kind: str
    provider_name: str
    model_name: str
    output_text: str
    confidence: float
    candidate_only: bool = True
    source_model_tag: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    candidate_id: str | None = None


class ModelTeacherPipeline:
    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or ModelRegistry()

    def run(
        self,
        *,
        task_kind: str,
        prompt: str,
        system_prompt: str | None = None,
        context: dict[str, Any] | None = None,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
        trace_id: str | None = None,
        output_mode: str = "plain_text",
    ) -> TeacherCandidate | None:
        manifest = self.registry.select_manifest(
            ModelSelectionRequest(
                task_kind=task_kind,
                output_mode=output_mode,
                preferred_provider=preferred_provider,
                preferred_model=preferred_model,
                allow_paid_fallback=True,
            )
        )
        if not manifest:
            return None
        adapter = self.registry.build_adapter(manifest)
        response = adapter.invoke(
            ModelRequest(
                task_kind=task_kind,
                prompt=prompt,
                system_prompt=system_prompt,
                context=dict(context or {}),
                output_mode=output_mode,
                trace_id=trace_id,
            )
        )
        validation = validate_provider_output(
            provider_id=manifest.provider_id,
            output_mode=output_mode,
            raw_text=response.output_text,
            trace_id=trace_id,
        )
        trust = output_trust_score(
            manifest=manifest,
            raw_confidence=float(response.confidence or 0.5),
            contract_ok=validation.ok,
            trust_penalty=validation.trust_penalty,
            freshness_score=1.0,
        )
        candidate_id = record_candidate_output(
            task_hash=build_task_hash(normalized_input=prompt, task_class=task_kind, output_mode=output_mode),
            task_id=None,
            trace_id=trace_id,
            task_class=task_kind,
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
            metadata={"teacher_pipeline": True},
            provenance={
                **adapter.get_license_metadata(),
                "provider_name": manifest.provider_name,
                "model_name": manifest.model_name,
                "local_peer_id": get_local_peer_id(),
                "task_kind": task_kind,
            },
        )
        candidate = TeacherCandidate(
            task_kind=task_kind,
            provider_name=manifest.provider_name,
            model_name=manifest.model_name,
            output_text=validation.normalized_text,
            confidence=max(0.0, min(1.0, float(response.confidence) - validation.trust_penalty)),
            source_model_tag=f"{manifest.provider_name}:{manifest.model_name}",
            provenance={
                "provider_name": manifest.provider_name,
                "model_name": manifest.model_name,
                "source_type": manifest.source_type,
                "license_name": manifest.license_name,
                "license_reference": manifest.resolved_license_reference,
                "weight_location": manifest.weight_location,
                "redistribution_allowed": manifest.redistribution_allowed,
                "local_peer_id": get_local_peer_id(),
                "task_kind": task_kind,
                "runtime_dependency": manifest.runtime_dependency,
            },
            candidate_id=candidate_id,
        )
        audit_logger.log(
            "model_teacher_candidate_generated",
            target_id=candidate.source_model_tag,
            target_type="model_provider",
            trace_id=trace_id,
            details={
                "task_kind": candidate.task_kind,
                "candidate_only": True,
                "confidence": candidate.confidence,
                "provider_name": candidate.provider_name,
                "model_name": candidate.model_name,
                "candidate_id": candidate_id,
            },
        )
        return candidate

    def summarize(self, text: str, *, trace_id: str | None = None) -> TeacherCandidate | None:
        return self.run(task_kind="summarization", prompt=text, trace_id=trace_id)

    def classify(self, text: str, *, trace_id: str | None = None) -> TeacherCandidate | None:
        return self.run(task_kind="classification", prompt=text, trace_id=trace_id, output_mode="json_object")

    def generate_candidate_shard(self, text: str, *, trace_id: str | None = None) -> TeacherCandidate | None:
        return self.run(task_kind="candidate_shard_generation", prompt=text, trace_id=trace_id, output_mode="json_object")

    def normalization_assist(self, text: str, *, trace_id: str | None = None) -> TeacherCandidate | None:
        return self.run(task_kind="normalization_assist", prompt=text, trace_id=trace_id, output_mode="summary_block")

    @staticmethod
    def as_candidate_record(candidate: TeacherCandidate) -> dict[str, Any]:
        return asdict(candidate)
