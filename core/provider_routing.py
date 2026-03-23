from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core import policy_engine
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest
from storage.model_provider_manifest import ModelProviderManifest

ProviderRole = Literal["auto", "drone", "queen"]


@dataclass(frozen=True)
class ProviderRoutingPlan:
    role: ProviderRole
    task_kind: str
    output_mode: str
    allow_paid_fallback: bool
    swarm_size: int
    preferred_provider: str | None
    preferred_model: str | None
    selected: ModelProviderManifest | None
    candidates: tuple[ModelProviderManifest, ...]

    @property
    def candidate_provider_ids(self) -> tuple[str, ...]:
        return tuple(manifest.provider_id for manifest in self.candidates)


def rank_provider_candidates(
    registry: ModelRegistry,
    *,
    task_kind: str,
    output_mode: str,
    role: ProviderRole = "auto",
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    allow_paid_fallback: bool | None = None,
    swarm_size: int = 1,
    min_trust: float = 0.0,
) -> list[ModelProviderManifest]:
    normalized_role = _normalize_role(role)
    resolved_allow_paid = _resolve_allow_paid_fallback(normalized_role, allow_paid_fallback)
    resolved_swarm_size = _resolve_swarm_size(normalized_role, swarm_size)
    base_ranked = registry.rank_manifests(
        ModelSelectionRequest(
            task_kind=task_kind,
            output_mode=output_mode,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            preferred_source_types=["http", "local_path", "subprocess"],
            allow_paid_fallback=resolved_allow_paid,
            min_trust=min_trust,
        )
    )
    if normalized_role == "auto":
        return base_ranked[:resolved_swarm_size]

    total = max(len(base_ranked), 1)
    rescored: list[tuple[float, ModelProviderManifest]] = []
    for index, manifest in enumerate(base_ranked):
        base_score = float(total - index)
        role_bonus = _role_bonus(manifest, normalized_role)
        rescored.append((base_score + role_bonus, manifest))
    rescored.sort(key=lambda item: (item[0], item[1].provider_name, item[1].model_name), reverse=True)
    return [manifest for _, manifest in rescored[:resolved_swarm_size]]


def resolve_provider_routing_plan(
    registry: ModelRegistry,
    *,
    task_kind: str,
    output_mode: str,
    role: ProviderRole = "auto",
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    allow_paid_fallback: bool | None = None,
    swarm_size: int = 1,
    min_trust: float = 0.0,
) -> ProviderRoutingPlan:
    normalized_role = _normalize_role(role)
    resolved_allow_paid = _resolve_allow_paid_fallback(normalized_role, allow_paid_fallback)
    resolved_swarm_size = _resolve_swarm_size(normalized_role, swarm_size)
    candidates = tuple(
        rank_provider_candidates(
            registry,
            task_kind=task_kind,
            output_mode=output_mode,
            role=normalized_role,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            allow_paid_fallback=resolved_allow_paid,
            swarm_size=resolved_swarm_size,
            min_trust=min_trust,
        )
    )
    return ProviderRoutingPlan(
        role=normalized_role,
        task_kind=task_kind,
        output_mode=output_mode,
        allow_paid_fallback=resolved_allow_paid,
        swarm_size=resolved_swarm_size,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        selected=candidates[0] if candidates else None,
        candidates=candidates,
    )


def _normalize_role(role: str) -> ProviderRole:
    clean = str(role or "auto").strip().lower()
    if clean in {"drone", "queen"}:
        return clean
    return "auto"


def _resolve_allow_paid_fallback(role: ProviderRole, explicit: bool | None) -> bool:
    if explicit is not None:
        return bool(explicit)
    if role == "drone":
        return False
    if role == "queen":
        return bool(policy_engine.get("model_orchestration.queen_allow_paid_fallback", True))
    return True


def _resolve_swarm_size(role: ProviderRole, requested: int) -> int:
    requested_value = max(1, int(requested or 1))
    if role == "drone":
        default_width = int(policy_engine.get("model_orchestration.drone_swarm_width", 2) or 2)
    elif role == "queen":
        default_width = int(policy_engine.get("model_orchestration.queen_swarm_width", 1) or 1)
    else:
        default_width = requested_value
    return max(1, min(4, requested_value if requested else default_width))


def _role_bonus(manifest: ModelProviderManifest, role: ProviderRole) -> float:
    if role == "auto":
        return 0.0
    provider_hint = str(policy_engine.get("model_orchestration.queen_provider_hint", "kimi") or "kimi").strip().lower()
    drone_hint = str(policy_engine.get("model_orchestration.drone_provider_hint", "qwen") or "qwen").strip().lower()
    orchestration_role = str((manifest.metadata or {}).get("orchestration_role") or "").strip().lower()
    deployment_class = str((manifest.metadata or {}).get("deployment_class") or "").strip().lower()
    text_blob = " ".join(
        [
            manifest.provider_name,
            manifest.model_name,
            str(manifest.notes or ""),
            str((manifest.metadata or {}).get("runtime_family") or ""),
        ]
    ).lower()
    capabilities = {str(item).strip().lower() for item in list(manifest.capabilities or [])}
    local_http = _is_local_http(manifest)
    is_local = manifest.source_type in {"local_path", "subprocess"} or local_http or deployment_class == "local"
    is_remote = not is_local or deployment_class == "cloud"

    if role == "drone":
        score = 0.0
        if orchestration_role == "drone":
            score += 1.5
        if is_local:
            score += 0.9
        if drone_hint and drone_hint in text_blob:
            score += 0.55
        if "structured_json" in capabilities:
            score += 0.12
        if orchestration_role == "queen":
            score -= 1.25
        if provider_hint and provider_hint in text_blob:
            score -= 0.45
        if is_remote:
            score -= 0.65
        return score

    score = 0.0
    if orchestration_role == "queen":
        score += 1.7
    if provider_hint and provider_hint in text_blob:
        score += 1.35
    if "long_context" in capabilities:
        score += 0.45
    if "code_complex" in capabilities:
        score += 0.18
    if is_remote:
        score += 0.32
    if is_local and drone_hint and drone_hint in text_blob:
        score += 0.16
    if orchestration_role == "drone":
        score -= 0.55
    return score


def _is_local_http(manifest: ModelProviderManifest) -> bool:
    base_url = str((manifest.runtime_config or {}).get("base_url") or "").strip().lower()
    return base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost")


__all__ = [
    "ProviderRole",
    "ProviderRoutingPlan",
    "rank_provider_candidates",
    "resolve_provider_routing_plan",
]
