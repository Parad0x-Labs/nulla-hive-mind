from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.hardware_tier import MachineProbe, QwenTier, probe_machine, select_qwen_tier
from core.local_specialist_lane import (
    DEFAULT_SECONDARY_LOCAL_BACKEND,
    DEFAULT_SECONDARY_LOCAL_BASE_URL,
    DEFAULT_SECONDARY_LOCAL_MODEL,
    DEFAULT_SECONDARY_LOCAL_PROFILE,
    secondary_local_model,
)
from core.provider_env import merge_provider_env
from core.runtime_install_profiles import format_install_profile_id


@dataclass(frozen=True)
class InstallRecommendationTruth:
    recommended_default_profile: str
    recommended_optional_profile: str
    primary_local_model: str
    secondary_local_model: str
    secondary_local_supported: bool
    selection_reasons: tuple[str, ...]
    local_multi_llm_fit: str
    secondary_local_backend: str = DEFAULT_SECONDARY_LOCAL_BACKEND
    secondary_local_base_url: str = DEFAULT_SECONDARY_LOCAL_BASE_URL

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "nulla.install_recommendation.v1",
            "recommended_default_profile": self.recommended_default_profile,
            "recommended_default_profile_display_id": format_install_profile_id(
                self.recommended_default_profile,
                allow_auto=False,
            ),
            "recommended_optional_profile": self.recommended_optional_profile,
            "recommended_optional_profile_display_id": format_install_profile_id(
                self.recommended_optional_profile,
                allow_auto=False,
            )
            if self.recommended_optional_profile
            else "",
            "primary_local_model": self.primary_local_model,
            "secondary_local_model": self.secondary_local_model,
            "secondary_local_supported": self.secondary_local_supported,
            "selection_reasons": list(self.selection_reasons),
            "local_multi_llm_fit": self.local_multi_llm_fit,
            "secondary_local_backend": self.secondary_local_backend,
            "secondary_local_base_url": self.secondary_local_base_url,
        }


def local_multi_llm_fit(probe: MachineProbe | Mapping[str, Any] | None = None) -> str:
    if probe is None:
        active_probe = probe_machine()
        ram_gb = float(active_probe.ram_gb or 0.0)
        accelerator = str(active_probe.accelerator or "").strip().lower()
        vram_gb = float(active_probe.vram_gb or 0.0) if active_probe.vram_gb is not None else 0.0
    elif isinstance(probe, Mapping):
        ram_gb = float(probe.get("ram_gb") or 0.0)
        accelerator = str(probe.get("accelerator") or "").strip().lower()
        vram_gb = float(probe.get("vram_gb") or 0.0) if probe.get("vram_gb") is not None else 0.0
    else:
        ram_gb = float(probe.ram_gb or 0.0)
        accelerator = str(probe.accelerator or "").strip().lower()
        vram_gb = float(probe.vram_gb or 0.0) if probe.vram_gb is not None else 0.0
    if accelerator == "mps":
        if ram_gb >= 48.0:
            return "comfortable"
        if ram_gb >= 24.0:
            return "pressure_sensitive"
        return "single_model_only"
    if vram_gb >= 20.0 or ram_gb >= 48.0:
        return "comfortable"
    if vram_gb >= 10.0 or ram_gb >= 24.0:
        return "pressure_sensitive"
    return "single_model_only"


def build_install_recommendation_truth(
    *,
    probe: MachineProbe | None = None,
    tier: QwenTier | None = None,
    selected_model: str | None = None,
    runtime_home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> InstallRecommendationTruth:
    env_map = merge_provider_env(runtime_home, env=os.environ if env is None else env)
    active_probe = probe or probe_machine()
    active_tier = tier or select_qwen_tier(active_probe)
    primary_local_model = str(selected_model or active_tier.ollama_tag or "").strip() or "qwen2.5:7b"
    specialist_model = secondary_local_model(env_map)
    fit = local_multi_llm_fit(active_probe)
    free_gb = _free_gb(runtime_home)
    secondary_required_gb = _secondary_local_required_disk_gb(specialist_model)
    disk_ok = free_gb >= secondary_required_gb
    secondary_supported = fit != "single_model_only" and disk_ok
    reasons = [
        f"Primary local companion model follows hardware tier `{active_tier.tier_name}` and resolves to `{primary_local_model}`.",
        "Default install stays on the single local Ollama companion lane so first-run latency and companion behavior stay predictable.",
    ]
    if secondary_supported:
        if fit == "comfortable":
            reasons.append(
                f"This machine has enough RAM/VRAM headroom and disk for the optional `{DEFAULT_SECONDARY_LOCAL_BACKEND}` verifier lane."
            )
        else:
            reasons.append(
                f"This machine can carry the optional `{DEFAULT_SECONDARY_LOCAL_BACKEND}` verifier lane, but it is pressure-sensitive under concurrency."
            )
        reasons.append(
            f"The optional stronger local lane targets `{specialist_model}` for coding and verifier-heavy work."
        )
    else:
        if fit == "single_model_only":
            reasons.append(
                "This machine should stay on one local model at a time; the optional stronger local lane is not recommended."
            )
        else:
            reasons.append(
                f"The optional stronger local lane is held back because the active target volume only has {free_gb:.1f} GiB free, below the {secondary_required_gb:.1f} GiB floor."
            )
    return InstallRecommendationTruth(
        recommended_default_profile="local-only",
        recommended_optional_profile=DEFAULT_SECONDARY_LOCAL_PROFILE if secondary_supported else "",
        primary_local_model=primary_local_model,
        secondary_local_model=specialist_model,
        secondary_local_supported=secondary_supported,
        selection_reasons=tuple(reasons),
        local_multi_llm_fit=fit,
    )

def _secondary_local_required_disk_gb(model_name: str) -> float:
    clean = str(model_name or "").strip().lower()
    if "32b" in clean:
        return 38.0
    if "14b" in clean:
        return 18.0
    if "7b" in clean:
        return 10.0
    return 16.0


def _free_gb(runtime_home: str | Path | None) -> float:
    candidate = Path(runtime_home).expanduser().resolve() if runtime_home else (Path.home() / ".nulla_runtime").resolve()
    existing = _nearest_existing_path(candidate)
    usage = shutil.disk_usage(existing)
    return float(usage.free) / (1024.0 ** 3)


def _nearest_existing_path(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current.resolve()


__all__ = [
    "DEFAULT_SECONDARY_LOCAL_BACKEND",
    "DEFAULT_SECONDARY_LOCAL_BASE_URL",
    "DEFAULT_SECONDARY_LOCAL_MODEL",
    "DEFAULT_SECONDARY_LOCAL_PROFILE",
    "InstallRecommendationTruth",
    "build_install_recommendation_truth",
    "local_multi_llm_fit",
]
