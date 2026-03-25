from __future__ import annotations

import os
import platform
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.hardware_tier import MachineProbe, QwenTier, probe_machine, select_qwen_tier
from core.provider_routing import ProviderCapabilityTruth

_PROFILE_IDS = {
    "auto-recommended",
    "local-only",
    "local-max",
    "hybrid-kimi",
    "hybrid-fallback",
    "full-orchestrated",
}

_MODEL_SIZE_GB = {
    "qwen2.5:0.5b": 1.0,
    "qwen2.5:3b": 3.5,
    "qwen2.5:7b": 8.0,
    "qwen2.5:14b": 16.0,
    "qwen2.5:32b": 36.0,
    "qwen2.5:72b": 80.0,
}


@dataclass(frozen=True)
class InstallProfileProvider:
    provider_id: str
    role: str
    locality: str
    required: bool
    api_key_envs: tuple[str, ...] = ()
    configured: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "role": self.role,
            "locality": self.locality,
            "required": self.required,
            "api_key_envs": list(self.api_key_envs),
            "configured": self.configured,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class InstallProfileVolumeCheck:
    volume_id: str
    labels: tuple[str, ...]
    path: str
    required_gb: float
    free_gb: float
    ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "volume_id": self.volume_id,
            "labels": list(self.labels),
            "path": self.path,
            "required_gb": self.required_gb,
            "free_gb": self.free_gb,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class InstallProfileTruth:
    profile_id: str
    label: str
    summary: str
    selection_source: str
    selected_model: str
    provider_mix: tuple[InstallProfileProvider, ...]
    estimated_download_gb: float
    estimated_disk_footprint_gb: float
    minimum_free_space_gb: float
    ram_expectation_gb: float
    vram_expectation_gb: float
    ready: bool
    single_volume_ready: bool
    reasons: tuple[str, ...]
    volume_checks: tuple[InstallProfileVolumeCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "nulla.install_profile.v1",
            "profile_id": self.profile_id,
            "label": self.label,
            "summary": self.summary,
            "selection_source": self.selection_source,
            "selected_model": self.selected_model,
            "provider_mix": [item.to_dict() for item in self.provider_mix],
            "estimated_download_gb": self.estimated_download_gb,
            "estimated_disk_footprint_gb": self.estimated_disk_footprint_gb,
            "minimum_free_space_gb": self.minimum_free_space_gb,
            "ram_expectation_gb": self.ram_expectation_gb,
            "vram_expectation_gb": self.vram_expectation_gb,
            "ready": self.ready,
            "single_volume_ready": self.single_volume_ready,
            "reasons": list(self.reasons),
            "volume_checks": [item.to_dict() for item in self.volume_checks],
        }

    def display_summary(self) -> str:
        provider_roles = ", ".join(f"{item.role}:{item.provider_id}" for item in self.provider_mix)
        return (
            f"{self.profile_id} -> {self.selected_model} "
            f"({provider_roles}; download~{self.estimated_download_gb:.1f} GB; "
            f"disk~{self.estimated_disk_footprint_gb:.1f} GB)"
        )


def build_install_profile_truth(
    *,
    requested_profile: str | None = None,
    probe: MachineProbe | None = None,
    tier: QwenTier | None = None,
    selected_model: str | None = None,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...] = (),
    runtime_home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> InstallProfileTruth:
    env_map = os.environ if env is None else env
    active_probe = probe or probe_machine()
    active_tier = tier or select_qwen_tier(active_probe)
    requested = str(requested_profile or env_map.get("NULLA_INSTALL_PROFILE") or "").strip().lower()
    profile_id, selection_source, selection_reasons = _resolve_profile_id(
        requested=requested,
        probe=active_probe,
        tier=active_tier,
        env=env_map,
    )
    model_tag = str(selected_model or active_tier.ollama_tag or "").strip() or "qwen2.5:7b"
    estimates = _profile_estimates(profile_id=profile_id, model_tag=model_tag, tier=active_tier)
    provider_mix, provider_reasons = _provider_mix(
        profile_id=profile_id,
        model_tag=model_tag,
        provider_capability_truth=provider_capability_truth,
        env=env_map,
    )
    volume_checks = _volume_checks(
        runtime_home=runtime_home,
        env=env_map,
        runtime_required_gb=estimates["runtime_required_gb"],
        ollama_required_gb=estimates["model_required_gb"],
    )
    single_volume_ready = all(item.ok for item in volume_checks)
    reasons = list(selection_reasons)
    reasons.extend(provider_reasons)
    if not single_volume_ready:
        reasons.append(
            "No single target volume currently has enough free space for the selected runtime + model footprint."
        )
    ready = single_volume_ready and all(item.configured for item in provider_mix if item.required)
    if not ready and all(item.configured for item in provider_mix if item.required) and single_volume_ready:
        reasons.append("Profile is selected but not fully ready.")
    return InstallProfileTruth(
        profile_id=profile_id,
        label=_profile_label(profile_id),
        summary=_profile_summary(profile_id),
        selection_source=selection_source,
        selected_model=model_tag,
        provider_mix=provider_mix,
        estimated_download_gb=estimates["estimated_download_gb"],
        estimated_disk_footprint_gb=estimates["estimated_disk_footprint_gb"],
        minimum_free_space_gb=estimates["minimum_free_space_gb"],
        ram_expectation_gb=estimates["ram_expectation_gb"],
        vram_expectation_gb=estimates["vram_expectation_gb"],
        ready=ready,
        single_volume_ready=single_volume_ready,
        reasons=tuple(dict.fromkeys(reason.strip() for reason in reasons if reason.strip())),
        volume_checks=volume_checks,
    )


def default_ollama_models_path(env: Mapping[str, str] | None = None) -> Path:
    env_map = os.environ if env is None else env
    override = str(env_map.get("OLLAMA_MODELS") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    home = Path.home()
    system = platform.system().lower()
    if system == "windows":
        return (home / ".ollama" / "models").resolve()
    return (home / ".ollama" / "models").resolve()


def _resolve_profile_id(
    *,
    requested: str,
    probe: MachineProbe,
    tier: QwenTier,
    env: Mapping[str, str],
) -> tuple[str, str, list[str]]:
    reasons: list[str] = []
    if requested:
        if requested in _PROFILE_IDS:
            reasons.append(f"Install profile came from NULLA_INSTALL_PROFILE={requested}.")
            return requested, "env_override", reasons
        reasons.append(f"Unknown install profile `{requested}`. Falling back to auto-recommended.")

    if _has_any_env(env, "KIMI_API_KEY") and tier.tier_name in {"nano", "lite", "base"}:
        reasons.append("Auto-selected hybrid-kimi because local tier is limited and Kimi is configured.")
        return "hybrid-kimi", "auto", reasons
    if tier.tier_name in {"mid", "heavy", "titan"}:
        reasons.append("Auto-selected local-max because this machine can hold a stronger fully local lane.")
        return "local-max", "auto", reasons
    reasons.append("Auto-selected local-only to keep the default runtime local-first and dependency-light.")
    return "local-only", "auto", reasons


def _profile_estimates(*, profile_id: str, model_tag: str, tier: QwenTier) -> dict[str, float]:
    primary_model_gb = _estimate_model_storage_gb(model_tag)
    extra_local_model_gb = 0.0
    runtime_required_gb = 2.5
    remote_overhead_gb = 0.0

    if profile_id in {"local-max", "full-orchestrated"}:
        extra_local_model_gb += 3.5
        runtime_required_gb += 1.0
    if profile_id in {"hybrid-kimi", "hybrid-fallback", "full-orchestrated"}:
        remote_overhead_gb += 0.5

    estimated_download_gb = round(primary_model_gb + extra_local_model_gb + remote_overhead_gb, 1)
    estimated_disk_footprint_gb = round(estimated_download_gb + runtime_required_gb + 1.5, 1)
    model_required_gb = round(primary_model_gb + extra_local_model_gb + 1.5, 1)
    minimum_free_space_gb = round(runtime_required_gb + model_required_gb, 1)
    return {
        "estimated_download_gb": estimated_download_gb,
        "estimated_disk_footprint_gb": estimated_disk_footprint_gb,
        "minimum_free_space_gb": minimum_free_space_gb,
        "runtime_required_gb": round(runtime_required_gb, 1),
        "model_required_gb": model_required_gb,
        "ram_expectation_gb": float(max(tier.min_ram_gb, 6.0)),
        "vram_expectation_gb": float(max(tier.min_vram_gb, 0.0)),
    }


def _provider_mix(
    *,
    profile_id: str,
    model_tag: str,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    env: Mapping[str, str],
) -> tuple[tuple[InstallProfileProvider, ...], list[str]]:
    local_provider_id = _find_local_provider_id(provider_capability_truth, model_tag=model_tag)
    secondary_local_provider_id = _find_secondary_local_provider_id(
        provider_capability_truth,
        primary_provider_id=local_provider_id,
    )
    kimi_provider_id = _find_remote_provider_id(provider_capability_truth, hint="kimi")
    fallback_provider_id = _find_remote_provider_id(provider_capability_truth, hint=None, exclude={kimi_provider_id})
    providers: list[InstallProfileProvider] = []
    reasons: list[str] = []

    providers.append(
        InstallProfileProvider(
            provider_id=local_provider_id,
            role="coder",
            locality="local",
            required=True,
            configured=True,
            notes="Primary local Ollama lane.",
        )
    )
    if profile_id in {"local-max", "full-orchestrated"}:
        providers.append(
            InstallProfileProvider(
                provider_id=secondary_local_provider_id,
                role="verifier",
                locality="local",
                required=True,
                configured=True,
                notes=(
                    "Secondary local verification lane."
                    if secondary_local_provider_id != local_provider_id
                    else "Secondary local verification lane on the primary local backend."
                ),
            )
        )
    if profile_id == "hybrid-kimi":
        configured = _has_any_env(env, "KIMI_API_KEY")
        providers.append(
            InstallProfileProvider(
                provider_id=kimi_provider_id,
                role="queen",
                locality="remote",
                required=True,
                api_key_envs=("KIMI_API_KEY",),
                configured=configured,
                notes="Remote reasoning/synthesis lane.",
            )
        )
        if not configured:
            reasons.append("hybrid-kimi needs KIMI_API_KEY before the remote queen lane is usable.")
    elif profile_id == "hybrid-fallback":
        configured = _has_any_env(env, "OPENAI_API_KEY", "KIMI_API_KEY")
        providers.append(
            InstallProfileProvider(
                provider_id=fallback_provider_id,
                role="queen",
                locality="remote",
                required=True,
                api_key_envs=("OPENAI_API_KEY", "KIMI_API_KEY"),
                configured=configured,
                notes="Remote fallback lane for when local quality or availability is insufficient.",
            )
        )
        if not configured:
            reasons.append("hybrid-fallback needs OPENAI_API_KEY or KIMI_API_KEY.")
    elif profile_id == "full-orchestrated":
        kimi_configured = _has_any_env(env, "KIMI_API_KEY")
        fallback_configured = _has_any_env(env, "OPENAI_API_KEY", "KIMI_API_KEY")
        providers.extend(
            [
                InstallProfileProvider(
                    provider_id=kimi_provider_id,
                    role="queen",
                    locality="remote",
                    required=True,
                    api_key_envs=("KIMI_API_KEY",),
                    configured=kimi_configured,
                    notes="Primary remote synthesis lane.",
                ),
                InstallProfileProvider(
                    provider_id=fallback_provider_id,
                    role="researcher",
                    locality="remote",
                    required=True,
                    api_key_envs=("OPENAI_API_KEY", "KIMI_API_KEY"),
                    configured=fallback_configured,
                    notes="Remote fallback/research lane.",
                ),
            ]
        )
        if not kimi_configured:
            reasons.append("full-orchestrated needs KIMI_API_KEY for the queen lane.")
        if not fallback_configured:
            reasons.append("full-orchestrated needs OPENAI_API_KEY or KIMI_API_KEY for the remote fallback lane.")

    return tuple(providers), reasons


def _find_local_provider_id(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    *,
    model_tag: str,
) -> str:
    preferred_provider_id = ""
    fallback_provider_id = ""
    for item in provider_capability_truth:
        if item.locality != "local":
            continue
        if not fallback_provider_id:
            fallback_provider_id = item.provider_id
        if item.provider_id.lower().startswith("ollama-local:"):
            preferred_provider_id = item.provider_id
            break
    return preferred_provider_id or fallback_provider_id or f"ollama-local:{model_tag}"


def _find_secondary_local_provider_id(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    *,
    primary_provider_id: str,
) -> str:
    preferred_provider_id = ""
    fallback_provider_id = ""
    for item in provider_capability_truth:
        if item.locality != "local" or item.provider_id == primary_provider_id:
            continue
        if not fallback_provider_id:
            fallback_provider_id = item.provider_id
        lowered = item.provider_id.lower()
        if item.role_fit == "verifier" or lowered.startswith("vllm-local:") or lowered.startswith("llamacpp-local:"):
            preferred_provider_id = item.provider_id
            break
    return preferred_provider_id or fallback_provider_id or primary_provider_id


def _find_remote_provider_id(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    *,
    hint: str | None,
    exclude: set[str | None] | None = None,
) -> str:
    excluded = {item for item in list(exclude or set()) if item}
    hinted = [
        item.provider_id
        for item in provider_capability_truth
        if item.locality == "remote" and item.provider_id not in excluded
    ]
    if hint:
        for provider_id in hinted:
            if hint in provider_id.lower():
                return provider_id
    if hinted:
        return hinted[0]
    if hint == "kimi":
        return "kimi-remote"
    return "openai-compatible-remote"


def _volume_checks(
    *,
    runtime_home: str | Path | None,
    env: Mapping[str, str],
    runtime_required_gb: float,
    ollama_required_gb: float,
) -> tuple[InstallProfileVolumeCheck, ...]:
    runtime_target = Path(runtime_home).expanduser().resolve() if runtime_home else (Path.home() / ".nulla_runtime").resolve()
    ollama_target = default_ollama_models_path(env)
    allocations = [
        ("runtime_home", runtime_target, runtime_required_gb),
        ("ollama_models", ollama_target, ollama_required_gb),
    ]
    grouped: dict[str, dict[str, Any]] = {}
    for label, path, required_gb in allocations:
        existing_path = _nearest_existing_path(path)
        volume_id = _volume_id(existing_path)
        entry = grouped.setdefault(
            volume_id,
            {
                "labels": [],
                "path": str(existing_path),
                "required_gb": 0.0,
                "free_gb": _disk_free_gb(existing_path),
            },
        )
        entry["labels"].append(label)
        entry["required_gb"] += float(required_gb)
    checks = [
        InstallProfileVolumeCheck(
            volume_id=volume_id,
            labels=tuple(sorted(entry["labels"])),
            path=str(entry["path"]),
            required_gb=round(float(entry["required_gb"]), 1),
            free_gb=round(float(entry["free_gb"]), 1),
            ok=float(entry["free_gb"]) >= float(entry["required_gb"]),
        )
        for volume_id, entry in sorted(grouped.items())
    ]
    return tuple(checks)


def _disk_free_gb(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return float(usage.free) / (1024.0 ** 3)


def _nearest_existing_path(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current.resolve()


def _volume_id(path: Path) -> str:
    anchor = path.anchor.lower() or "/"
    try:
        stat = path.stat()
        return f"{anchor}:{stat.st_dev}"
    except OSError:
        return anchor


def _profile_label(profile_id: str) -> str:
    return {
        "auto-recommended": "Auto recommended",
        "local-only": "Local only",
        "local-max": "Local max",
        "hybrid-kimi": "Hybrid Kimi",
        "hybrid-fallback": "Hybrid fallback",
        "full-orchestrated": "Full orchestrated",
    }[profile_id]


def _profile_summary(profile_id: str) -> str:
    return {
        "auto-recommended": "Choose the strongest honest profile from current hardware and configured providers.",
        "local-only": "Single local Ollama lane with no remote provider dependency.",
        "local-max": "Heavier fully local lane with extra local verification capacity.",
        "hybrid-kimi": "Local coding lane plus a remote Kimi synthesis lane.",
        "hybrid-fallback": "Local coding lane plus a generic remote fallback lane.",
        "full-orchestrated": "Local coding/verifier lanes plus remote synthesis and fallback lanes.",
    }[profile_id]


def _estimate_model_storage_gb(model_tag: str) -> float:
    clean = str(model_tag or "").strip().lower()
    if clean in _MODEL_SIZE_GB:
        return _MODEL_SIZE_GB[clean]
    match = re.search(r"(\d+(?:\.\d+)?)b", clean)
    if match:
        return round(max(1.0, float(match.group(1)) * 1.12), 1)
    return 8.0


def _has_any_env(env: Mapping[str, str], *keys: str) -> bool:
    return any(str(env.get(key) or "").strip() for key in keys)


__all__ = [
    "InstallProfileProvider",
    "InstallProfileTruth",
    "InstallProfileVolumeCheck",
    "build_install_profile_truth",
    "default_ollama_models_path",
]
