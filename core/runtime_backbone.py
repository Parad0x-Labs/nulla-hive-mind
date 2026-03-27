from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from core.backend_manager import BackendManager
from core.hardware_tier import MachineProbe, QwenTier, probe_machine, select_qwen_tier, tier_summary
from core.model_registry import ModelRegistry, ProviderAuditRow
from core.provider_routing import ProviderCapabilityTruth, provider_capability_truth_for_manifest
from core.runtime_bootstrap import BootstrappedRuntime, bootstrap_runtime_mode
from core.runtime_install_profiles import InstallProfileTruth, active_install_profile_id, build_install_profile_truth
from core.runtime_provider_defaults import ensure_default_runtime_providers


@dataclass(frozen=True)
class ProviderRegistrySnapshot:
    warnings: tuple[str, ...]
    audit_rows: tuple[ProviderAuditRow, ...]
    capability_truth: tuple[ProviderCapabilityTruth, ...]


@dataclass(frozen=True)
class LocalModelProfile:
    probe: MachineProbe
    tier: QwenTier
    summary: dict[str, Any]


@dataclass(frozen=True)
class RuntimeBackbone:
    boot: BootstrappedRuntime
    local_model_profile: LocalModelProfile
    provider_snapshot: ProviderRegistrySnapshot
    install_profile: InstallProfileTruth


def build_provider_registry_snapshot(
    registry: ModelRegistry | None = None,
    *,
    runtime_home: str | None = None,
    requested_profile: str | None = None,
    honor_install_profile: bool = False,
    env: dict[str, str] | None = None,
) -> ProviderRegistrySnapshot:
    active_registry = registry or ModelRegistry()
    env_map = os.environ if env is None else env
    install_profile = ""
    if honor_install_profile:
        install_profile = (
            str(requested_profile or "").strip()
            or active_install_profile_id(runtime_home=runtime_home, env=env_map)
        )
    ensure_default_runtime_providers(
        active_registry,
        env=env_map,
        install_profile=install_profile,
    )
    manifests: tuple[Any, ...]
    try:
        manifests = tuple(active_registry.list_manifests(enabled_only=True))
    except Exception:
        manifests = tuple()
    return ProviderRegistrySnapshot(
        warnings=tuple(active_registry.startup_warnings()),
        audit_rows=tuple(active_registry.provider_audit_rows()),
        capability_truth=tuple(provider_capability_truth_for_manifest(manifest) for manifest in manifests),
    )


def build_runtime_backbone(
    *,
    mode: str,
    workspace_root: str | None = None,
    db_path: str | None = None,
    force_policy_reload: bool = False,
    configure_logging: bool = False,
    resolve_backend: bool = False,
    manager: BackendManager | None = None,
    allow_remote_only: bool | None = None,
    registry: ModelRegistry | None = None,
    machine_probe: MachineProbe | None = None,
) -> RuntimeBackbone:
    boot = bootstrap_runtime_mode(
        mode=mode,
        workspace_root=workspace_root,
        db_path=db_path,
        force_policy_reload=force_policy_reload,
        configure_logging=configure_logging,
        resolve_backend=resolve_backend,
        manager=manager,
        allow_remote_only=allow_remote_only,
    )
    probe = machine_probe or probe_machine()
    tier = select_qwen_tier(probe)
    summary = dict(tier_summary(probe))
    if boot.backend_selection is not None:
        summary["backend_name"] = boot.backend_selection.backend_name
        summary["backend_device"] = boot.backend_selection.device
        summary["backend_reason"] = boot.backend_selection.reason
    provider_snapshot = build_provider_registry_snapshot(
        registry,
        runtime_home=str(getattr(getattr(boot, "context", None), "paths", None).runtime_home)
        if getattr(getattr(boot, "context", None), "paths", None) is not None
        else None,
        honor_install_profile=True,
    )
    install_profile = build_install_profile_truth(
        probe=probe,
        tier=tier,
        provider_capability_truth=provider_snapshot.capability_truth,
        runtime_home=getattr(getattr(boot, "context", None), "paths", None).runtime_home
        if getattr(getattr(boot, "context", None), "paths", None) is not None
        else None,
    )
    return RuntimeBackbone(
        boot=boot,
        local_model_profile=LocalModelProfile(
            probe=probe,
            tier=tier,
            summary=summary,
        ),
        provider_snapshot=provider_snapshot,
        install_profile=install_profile,
    )


__all__ = [
    "LocalModelProfile",
    "ProviderRegistrySnapshot",
    "RuntimeBackbone",
    "build_provider_registry_snapshot",
    "build_runtime_backbone",
]
