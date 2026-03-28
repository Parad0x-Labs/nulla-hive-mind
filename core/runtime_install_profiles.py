from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from core.hardware_tier import MachineProbe, QwenTier, probe_machine, select_qwen_tier
from core.provider_routing import ProviderCapabilityTruth

INSTALL_PROFILE_CHOICES = (
    "auto-recommended",
    "local-only",
    "local-max",
    "hybrid-kimi",
    "hybrid-fallback",
    "full-orchestrated",
)

_PROFILE_IDS = set(INSTALL_PROFILE_CHOICES)
_PROFILE_ALIASES = {
    "auto": "auto-recommended",
    "recommended": "auto-recommended",
    "ollama-only": "local-only",
    "ollama_only": "local-only",
    "local_only": "local-only",
    "ollama-max": "local-max",
    "ollama_max": "local-max",
    "local_max": "local-max",
    "ollama+kimi": "hybrid-kimi",
    "ollama-kimi": "hybrid-kimi",
    "ollama_kimi": "hybrid-kimi",
    "hybrid_kimi": "hybrid-kimi",
    "hybrid_fallback": "hybrid-fallback",
    "full_orchestrated": "full-orchestrated",
}

_MODEL_SIZE_GB = {
    "qwen2.5:0.5b": 1.0,
    "qwen2.5:3b": 3.5,
    "qwen2.5:7b": 8.0,
    "qwen2.5:14b": 16.0,
    "qwen2.5:32b": 36.0,
    "qwen2.5:72b": 80.0,
}
_INSTALL_PROFILE_RECORD_RELATIVE_PATH = Path("config") / "install-profile.json"
_KIMI_API_KEY_ENV_KEYS = ("KIMI_API_KEY", "MOONSHOT_API_KEY", "NULLA_KIMI_API_KEY")
_KIMI_API_KEY_REASON = "KIMI_API_KEY or MOONSHOT_API_KEY"
_OLLAMA_HELPER_MODEL = "qwen2.5:7b"
_INSTALLED_OLLAMA_MODELS_ENV_KEY = "NULLA_INSTALLED_OLLAMA_MODELS"


@dataclass(frozen=True)
class InstallProfileProvider:
    provider_id: str
    role: str
    locality: str
    required: bool
    api_key_envs: tuple[str, ...] = ()
    configured: bool = True
    availability_state: str = "unregistered"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "role": self.role,
            "locality": self.locality,
            "required": self.required,
            "api_key_envs": list(self.api_key_envs),
            "configured": self.configured,
            "availability_state": self.availability_state,
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
    degraded: bool
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
            "degraded": self.degraded,
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
    requested_raw = str(requested_profile or env_map.get("NULLA_INSTALL_PROFILE") or "").strip().lower()
    requested = normalize_install_profile_id(requested_raw, allow_auto=True)
    requested_source = "env_override" if requested else ""
    if not requested:
        requested = _installed_profile_id(runtime_home)
        if requested:
            requested_source = "installed_record"
    model_tag = str(selected_model or active_tier.ollama_tag or "").strip() or "qwen2.5:7b"
    installed_ollama_models = _installed_ollama_model_tags(
        provider_capability_truth=provider_capability_truth,
        env=env_map,
    )
    auto_reasons: list[str] = []
    if requested == "auto-recommended":
        auto_reasons.append("Install profile requested auto-recommended; applying hardware/provider auto selection.")
        requested = ""
    elif requested_raw and not requested:
        auto_reasons.append(f"Unknown install profile `{requested_raw}`. Falling back to auto-recommended.")
        requested = ""

    if requested:
        if requested_source == "installed_record":
            selection_source = "installed_default"
            selection_reasons = [f"Install profile came from the installed runtime profile `{requested}`."]
        else:
            selection_source = "env_override"
            selection_reasons = [f"Install profile came from NULLA_INSTALL_PROFILE={requested}."]
        return _compose_install_profile_truth(
            profile_id=requested,
            selection_source=selection_source,
            selection_reasons=selection_reasons,
            model_tag=model_tag,
            tier=active_tier,
            provider_capability_truth=provider_capability_truth,
            runtime_home=runtime_home,
            env=env_map,
            installed_ollama_models=installed_ollama_models,
        )

    candidates = _auto_profile_candidates(probe=active_probe, tier=active_tier, env=env_map)
    evaluated: list[InstallProfileTruth] = []
    for candidate in candidates:
        evaluated.append(
            _compose_install_profile_truth(
                profile_id=candidate,
                selection_source="auto",
                selection_reasons=[*auto_reasons, _auto_selection_reason(candidate)],
                model_tag=model_tag,
                tier=active_tier,
                provider_capability_truth=provider_capability_truth,
                runtime_home=runtime_home,
                env=env_map,
                installed_ollama_models=installed_ollama_models,
            )
        )

    chosen = next((profile for profile in evaluated if profile.ready and not profile.degraded), None)
    if chosen is None:
        chosen = next((profile for profile in evaluated if profile.ready), evaluated[0])
    chosen_index = evaluated.index(chosen)
    if chosen_index == 0:
        return chosen

    fallback_reasons = [
        f"Auto-fell back from `{previous.profile_id}` because {_primary_profile_blocker(previous)}."
        for previous in evaluated[:chosen_index]
    ]
    return replace(
        chosen,
        reasons=tuple(
            dict.fromkeys(
                reason.strip()
                for reason in [*chosen.reasons, *fallback_reasons]
                if reason and reason.strip()
            )
        ),
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


def normalize_install_profile_id(profile_id: str | None, *, allow_auto: bool = True) -> str:
    normalized = str(profile_id or "").strip().lower()
    if not normalized:
        return ""
    normalized = _PROFILE_ALIASES.get(normalized, normalized)
    if normalized not in _PROFILE_IDS:
        return ""
    if not allow_auto and normalized == "auto-recommended":
        return ""
    return normalized


def installed_profile_id(runtime_home: str | Path | None) -> str:
    return _installed_profile_id(runtime_home)


def active_install_profile_id(
    *,
    runtime_home: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    allow_auto: bool = False,
) -> str:
    env_map = os.environ if env is None else env
    requested = normalize_install_profile_id(env_map.get("NULLA_INSTALL_PROFILE"), allow_auto=allow_auto)
    if requested:
        return requested
    return normalize_install_profile_id(_installed_profile_id(runtime_home), allow_auto=allow_auto)


def persist_install_profile_record(
    runtime_home: str | Path,
    profile_id: str,
    *,
    selected_model: str = "",
) -> Path:
    runtime_root = Path(runtime_home).expanduser().resolve()
    target = runtime_root / _INSTALL_PROFILE_RECORD_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "nulla.install_profile_record.v1",
        "profile_id": str(profile_id or "").strip().lower(),
        "selected_model": str(selected_model or "").strip(),
    }
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def _installed_profile_id(runtime_home: str | Path | None) -> str:
    if runtime_home is None:
        return ""
    try:
        record_path = Path(runtime_home).expanduser().resolve() / _INSTALL_PROFILE_RECORD_RELATIVE_PATH
    except Exception:
        return ""
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    profile_id = str(payload.get("profile_id") or "").strip().lower()
    if profile_id in _PROFILE_IDS:
        return profile_id
    return ""


def _compose_install_profile_truth(
    *,
    profile_id: str,
    selection_source: str,
    selection_reasons: list[str],
    model_tag: str,
    tier: QwenTier,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    runtime_home: str | Path | None,
    env: Mapping[str, str],
    installed_ollama_models: set[str],
) -> InstallProfileTruth:
    estimates = _profile_estimates(
        profile_id=profile_id,
        model_tag=model_tag,
        tier=tier,
        installed_ollama_models=installed_ollama_models,
    )
    provider_mix, provider_reasons = _provider_mix(
        profile_id=profile_id,
        model_tag=model_tag,
        provider_capability_truth=provider_capability_truth,
        env=env,
    )
    volume_checks = _volume_checks(
        runtime_home=runtime_home,
        env=env,
        runtime_required_gb=estimates["runtime_required_gb"],
        ollama_required_gb=estimates["model_required_gb"],
    )
    single_volume_ready = all(item.ok for item in volume_checks)
    reasons = list(selection_reasons)
    reasons.extend(provider_reasons)
    required_provider_mix = tuple(item for item in provider_mix if item.required)
    blocked_provider_mix = tuple(
        item for item in required_provider_mix if item.availability_state in {"blocked", "unregistered"}
    )
    degraded_provider_mix = tuple(item for item in required_provider_mix if item.availability_state == "degraded")
    if not single_volume_ready:
        reasons.append(
            "No single target volume currently has enough free space for the selected runtime + model footprint."
        )
    if blocked_provider_mix:
        for item in blocked_provider_mix:
            reasons.append(
                f"Required provider lane `{item.provider_id}` is {item.availability_state} and cannot be treated as beta-ready."
            )
    if degraded_provider_mix:
        for item in degraded_provider_mix:
            reasons.append(
                f"Required provider lane `{item.provider_id}` is degraded and may still work, but the profile is not fully healthy."
            )
    ready = (
        single_volume_ready
        and all(item.configured for item in required_provider_mix)
        and not blocked_provider_mix
    )
    degraded = bool(degraded_provider_mix)
    if not ready and all(item.configured for item in required_provider_mix) and single_volume_ready and not blocked_provider_mix:
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
        degraded=degraded,
        single_volume_ready=single_volume_ready,
        reasons=tuple(dict.fromkeys(reason.strip() for reason in reasons if reason.strip())),
        volume_checks=volume_checks,
    )


def _auto_profile_candidates(
    *,
    probe: MachineProbe,
    tier: QwenTier,
    env: Mapping[str, str],
) -> tuple[str, ...]:
    candidates: list[str] = []
    kimi_configured = _has_any_env(env, *_KIMI_API_KEY_ENV_KEYS)
    if tier.tier_name in {"mid", "heavy", "titan"}:
        candidates.append("local-max")
        if kimi_configured:
            candidates.append("hybrid-kimi")
        candidates.append("local-only")
        return tuple(dict.fromkeys(candidates))
    if kimi_configured and tier.tier_name in {"nano", "lite", "base"}:
        candidates.extend(("hybrid-kimi", "local-only"))
        return tuple(dict.fromkeys(candidates))
    candidates.append("local-only")
    return tuple(dict.fromkeys(candidates))


def _auto_selection_reason(profile_id: str) -> str:
    if profile_id == "hybrid-kimi":
        return "Auto-selected hybrid-kimi because local tier is limited and Kimi is configured."
    if profile_id == "local-max":
        return "Auto-selected local-max because this machine can hold a stronger fully local lane."
    return "Auto-selected local-only to keep the default runtime local-first and dependency-light."


def _primary_profile_blocker(profile: InstallProfileTruth) -> str:
    ignored_prefixes = (
        "Install profile requested auto-recommended",
        "Unknown install profile",
        "Auto-selected ",
        "Install profile came from ",
        "Auto-fell back from ",
    )
    for reason in profile.reasons:
        if reason.startswith(ignored_prefixes):
            continue
        return reason
    if profile.degraded:
        return "it was degraded"
    if not profile.ready:
        return "it was not ready on this machine/runtime"
    return "a safer install profile was chosen"


def _profile_estimates(
    *,
    profile_id: str,
    model_tag: str,
    tier: QwenTier,
    installed_ollama_models: set[str],
) -> dict[str, float]:
    required_local_models = _required_ollama_models(profile_id=profile_id, model_tag=model_tag)
    missing_local_models = tuple(
        model_name for model_name in required_local_models if model_name.lower() not in installed_ollama_models
    )
    missing_model_gb = sum(_estimate_model_storage_gb(model_name) for model_name in missing_local_models)
    runtime_required_gb = 2.5

    if profile_id in {"local-max", "full-orchestrated"}:
        runtime_required_gb += 1.0
    if profile_id in {"hybrid-kimi", "hybrid-fallback", "full-orchestrated"}:
        runtime_required_gb += 0.5

    model_buffer_gb = 1.5 if missing_local_models else 0.0
    estimated_download_gb = round(missing_model_gb, 1)
    model_required_gb = round(missing_model_gb + model_buffer_gb, 1)
    minimum_free_space_gb = round(runtime_required_gb + model_required_gb, 1)
    return {
        "estimated_download_gb": estimated_download_gb,
        "estimated_disk_footprint_gb": minimum_free_space_gb,
        "minimum_free_space_gb": minimum_free_space_gb,
        "runtime_required_gb": round(runtime_required_gb, 1),
        "model_required_gb": model_required_gb,
        "ram_expectation_gb": float(max(tier.min_ram_gb, 6.0)),
        "vram_expectation_gb": float(max(tier.min_vram_gb, 0.0)),
    }


def _required_ollama_models(*, profile_id: str, model_tag: str) -> tuple[str, ...]:
    required_models = [str(model_tag or "").strip() or "qwen2.5:7b"]
    if profile_id in {"local-max", "full-orchestrated"}:
        helper_model = _OLLAMA_HELPER_MODEL
        if helper_model not in required_models:
            required_models.append(helper_model)
    return tuple(required_models)


def required_ollama_models_for_profile(*, profile_id: str, model_tag: str) -> tuple[str, ...]:
    normalized_profile = normalize_install_profile_id(profile_id, allow_auto=False)
    return _required_ollama_models(profile_id=normalized_profile, model_tag=model_tag)


def _provider_mix(
    *,
    profile_id: str,
    model_tag: str,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    env: Mapping[str, str],
) -> tuple[tuple[InstallProfileProvider, ...], list[str]]:
    truth_index = _provider_truth_index(provider_capability_truth)
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
            availability_state=_provider_availability_state(local_provider_id, truth_index),
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
                availability_state=_provider_availability_state(secondary_local_provider_id, truth_index),
                notes=(
                    "Secondary local verification lane."
                    if secondary_local_provider_id != local_provider_id
                    else "Secondary local verification lane on the primary local backend."
                ),
            )
        )
    if profile_id == "hybrid-kimi":
        configured = _has_any_env(env, *_KIMI_API_KEY_ENV_KEYS)
        providers.append(
            InstallProfileProvider(
                provider_id=kimi_provider_id,
                role="queen",
                locality="remote",
                required=True,
                api_key_envs=_KIMI_API_KEY_ENV_KEYS,
                configured=configured,
                availability_state=_provider_availability_state(kimi_provider_id, truth_index),
                notes="Remote reasoning/synthesis lane.",
            )
        )
        if not configured:
            reasons.append(f"hybrid-kimi needs {_KIMI_API_KEY_REASON} before the remote queen lane is usable.")
    elif profile_id == "hybrid-fallback":
        configured = _has_any_env(env, "OPENAI_API_KEY", *_KIMI_API_KEY_ENV_KEYS)
        providers.append(
            InstallProfileProvider(
                provider_id=fallback_provider_id,
                role="queen",
                locality="remote",
                required=True,
                api_key_envs=("OPENAI_API_KEY", *_KIMI_API_KEY_ENV_KEYS),
                configured=configured,
                availability_state=_provider_availability_state(fallback_provider_id, truth_index),
                notes="Remote fallback lane for when local quality or availability is insufficient.",
            )
        )
        if not configured:
            reasons.append(f"hybrid-fallback needs OPENAI_API_KEY or {_KIMI_API_KEY_REASON}.")
    elif profile_id == "full-orchestrated":
        kimi_configured = _has_any_env(env, *_KIMI_API_KEY_ENV_KEYS)
        fallback_configured = _has_any_env(env, "OPENAI_API_KEY", *_KIMI_API_KEY_ENV_KEYS)
        providers.extend(
            [
                InstallProfileProvider(
                    provider_id=kimi_provider_id,
                    role="queen",
                    locality="remote",
                    required=True,
                    api_key_envs=_KIMI_API_KEY_ENV_KEYS,
                    configured=kimi_configured,
                    availability_state=_provider_availability_state(kimi_provider_id, truth_index),
                    notes="Primary remote synthesis lane.",
                ),
                InstallProfileProvider(
                    provider_id=fallback_provider_id,
                    role="researcher",
                    locality="remote",
                    required=True,
                    api_key_envs=("OPENAI_API_KEY", *_KIMI_API_KEY_ENV_KEYS),
                    configured=fallback_configured,
                    availability_state=_provider_availability_state(fallback_provider_id, truth_index),
                    notes="Remote fallback/research lane.",
                ),
            ]
        )
        if not kimi_configured:
            reasons.append(f"full-orchestrated needs {_KIMI_API_KEY_REASON} for the queen lane.")
        if not fallback_configured:
            reasons.append(f"full-orchestrated needs OPENAI_API_KEY or {_KIMI_API_KEY_REASON} for the remote fallback lane.")

    return tuple(providers), reasons


def _find_local_provider_id(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    *,
    model_tag: str,
) -> str:
    candidates = [item for item in provider_capability_truth if item.locality == "local"]
    if not candidates:
        return f"ollama-local:{model_tag}"
    candidates.sort(
        key=lambda item: (
            _availability_rank(item.availability_state),
            1 if item.provider_id.lower().startswith("ollama-local:") else 0,
            1 if item.role_fit == "coder" else 0,
            -float(item.queue_depth) / float(max(1, item.max_safe_concurrency)),
        ),
        reverse=True,
    )
    return candidates[0].provider_id


def _find_secondary_local_provider_id(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    *,
    primary_provider_id: str,
) -> str:
    primary_capability = next(
        (item for item in provider_capability_truth if item.provider_id == primary_provider_id),
        None,
    )
    candidates = [
        item for item in provider_capability_truth if item.locality == "local" and item.provider_id != primary_provider_id
    ]
    if not candidates:
        return primary_provider_id
    candidates.sort(
        key=lambda item: (
            _availability_rank(item.availability_state),
            1 if item.role_fit == "verifier" else 0,
            1 if item.provider_id.lower().startswith(("vllm-local:", "llamacpp-local:")) else 0,
            -float(item.queue_depth) / float(max(1, item.max_safe_concurrency)),
        ),
        reverse=True,
    )
    best_candidate = candidates[0]
    if primary_capability is not None and _availability_rank(best_candidate.availability_state) < _availability_rank(
        primary_capability.availability_state
    ):
        return primary_provider_id
    if _availability_rank(best_candidate.availability_state) < _availability_rank("degraded"):
        return primary_provider_id
    return best_candidate.provider_id


def _find_remote_provider_id(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    *,
    hint: str | None,
    exclude: set[str | None] | None = None,
) -> str:
    excluded = {item for item in list(exclude or set()) if item}
    hinted = [
        item
        for item in provider_capability_truth
        if item.locality == "remote" and item.provider_id not in excluded
    ]
    if hinted:
        hinted.sort(
            key=lambda item: (
                1 if hint and hint in item.provider_id.lower() else 0,
                _availability_rank(item.availability_state),
                1 if item.role_fit == "queen" else 0,
                -float(item.queue_depth) / float(max(1, item.max_safe_concurrency)),
            ),
            reverse=True,
        )
        return hinted[0].provider_id
    if hint == "kimi":
        return "kimi-remote"
    return "openai-compatible-remote"


def _provider_truth_index(
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
) -> dict[str, ProviderCapabilityTruth]:
    return {item.provider_id: item for item in provider_capability_truth if item.provider_id}


def _provider_availability_state(
    provider_id: str,
    truth_index: Mapping[str, ProviderCapabilityTruth],
) -> str:
    if not provider_id:
        return "unregistered"
    capability = truth_index.get(provider_id)
    if capability is None:
        return "unregistered"
    return str(capability.availability_state or "unregistered")


def _availability_rank(state: str) -> int:
    return {
        "ready": 3,
        "degraded": 2,
        "blocked": 1,
        "unregistered": 0,
    }.get(str(state or "").strip().lower(), 0)


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


def _installed_ollama_model_tags(
    *,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...],
    env: Mapping[str, str],
) -> set[str]:
    tags: set[str] = set()
    override_present = _INSTALLED_OLLAMA_MODELS_ENV_KEY in env
    raw_override = str(env.get(_INSTALLED_OLLAMA_MODELS_ENV_KEY) or "").strip()
    if override_present:
        if raw_override.startswith("["):
            try:
                payload = json.loads(raw_override)
            except Exception:
                payload = []
            if isinstance(payload, list):
                tags.update(str(item).strip().lower() for item in payload if str(item).strip())
        elif raw_override:
            tags.update(part.strip().lower() for part in raw_override.split(",") if part.strip())
    for item in provider_capability_truth:
        provider_id = str(item.provider_id or "").strip()
        if provider_id.lower().startswith("ollama-local:"):
            tags.add(provider_id.split(":", 1)[1].strip().lower())
            model_id = str(item.model_id or "").strip()
            if model_id:
                tags.add(model_id.lower())
    if override_present:
        return tags
    binary = shutil.which("ollama")
    if not binary:
        return tags
    try:
        completed = subprocess.run(
            [binary, "list"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception:
        return tags
    lines = [line.rstrip() for line in str(completed.stdout or "").splitlines() if line.strip()]
    if len(lines) <= 1:
        return tags
    for raw_line in lines[1:]:
        parts = [part.strip() for part in re.split(r"\s{2,}", raw_line.strip()) if part.strip()]
        if not parts:
            continue
        tags.add(parts[0].lower())
    return tags


__all__ = [
    "INSTALL_PROFILE_CHOICES",
    "InstallProfileProvider",
    "InstallProfileTruth",
    "InstallProfileVolumeCheck",
    "active_install_profile_id",
    "build_install_profile_truth",
    "default_ollama_models_path",
    "installed_profile_id",
    "normalize_install_profile_id",
    "persist_install_profile_record",
    "required_ollama_models_for_profile",
]
