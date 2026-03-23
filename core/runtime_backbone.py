from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.backend_manager import BackendManager
from core.hardware_tier import MachineProbe, QwenTier, probe_machine, select_qwen_tier, tier_summary
from core.model_registry import ModelRegistry, ProviderAuditRow
from core.runtime_bootstrap import BootstrappedRuntime, bootstrap_runtime_mode


@dataclass(frozen=True)
class ProviderRegistrySnapshot:
    warnings: tuple[str, ...]
    audit_rows: tuple[ProviderAuditRow, ...]


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


def build_provider_registry_snapshot(
    registry: ModelRegistry | None = None,
) -> ProviderRegistrySnapshot:
    active_registry = registry or ModelRegistry()
    return ProviderRegistrySnapshot(
        warnings=tuple(active_registry.startup_warnings()),
        audit_rows=tuple(active_registry.provider_audit_rows()),
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
    provider_snapshot = build_provider_registry_snapshot(registry)
    return RuntimeBackbone(
        boot=boot,
        local_model_profile=LocalModelProfile(
            probe=probe,
            tier=tier,
            summary=summary,
        ),
        provider_snapshot=provider_snapshot,
    )


__all__ = [
    "LocalModelProfile",
    "ProviderRegistrySnapshot",
    "RuntimeBackbone",
    "build_provider_registry_snapshot",
    "build_runtime_backbone",
]
