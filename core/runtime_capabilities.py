from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from core.feature_flags import flag_map
from core.hardware_tier import probe_machine, select_qwen_tier
from core.install_recommendations import build_install_recommendation_truth
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_context import RuntimeContext, build_runtime_context
from core.runtime_install_profiles import build_install_profile_truth, install_profile_runs_local_only


@dataclass(frozen=True)
class RuntimeCapabilityStatus:
    name: str
    state: str
    category: str
    reason: str


def runtime_capability_statuses(context: RuntimeContext | None = None) -> list[RuntimeCapabilityStatus]:
    runtime = context or build_runtime_context(mode="runtime_capabilities")
    flags = flag_map()

    helper_mesh_state = "implemented" if runtime.feature_flags.helper_mesh_enabled else "disabled_by_policy"
    helper_mesh_reason = (
        "Helper coordination lanes are enabled for this runtime."
        if runtime.feature_flags.helper_mesh_enabled
        else "Helper coordination is disabled by runtime policy for this process."
    )
    public_hive_state = "implemented" if runtime.feature_flags.public_hive_enabled else "disabled_by_policy"
    public_hive_reason = (
        "Public/operator Hive surfaces are enabled for this runtime. Write auth truth is exposed separately."
        if runtime.feature_flags.public_hive_enabled
        else "Public Hive surfaces are disabled by runtime policy for this process."
    )
    workspace_write_state = "implemented" if runtime.feature_flags.allow_workspace_writes else "disabled_by_policy"
    sandbox_state = "implemented" if runtime.feature_flags.allow_sandbox_execution else "disabled_by_policy"
    remote_only_state = "implemented" if runtime.feature_flags.allow_remote_only_without_backend else "disabled_by_policy"

    return [
        RuntimeCapabilityStatus(
            name="local_runtime",
            state="implemented",
            category="core",
            reason=flags["LOCAL_STANDALONE"].reason,
        ),
        RuntimeCapabilityStatus(
            name="memory_and_tools",
            state="implemented",
            category="core",
            reason="NULLA can keep context, route tool intent, and execute bounded local/runtime tools.",
        ),
        RuntimeCapabilityStatus(
            name="helper_mesh",
            state=helper_mesh_state,
            category="helper_network",
            reason=helper_mesh_reason,
        ),
        RuntimeCapabilityStatus(
            name="public_hive_surface",
            state=public_hive_state,
            category="surface",
            reason=public_hive_reason,
        ),
        RuntimeCapabilityStatus(
            name="workspace_write_tools",
            state=workspace_write_state,
            category="tooling",
            reason=(
                "Workspace write tools are enabled by runtime policy."
                if runtime.feature_flags.allow_workspace_writes
                else "Workspace write tools are disabled by runtime policy."
            ),
        ),
        RuntimeCapabilityStatus(
            name="sandbox_execution",
            state=sandbox_state,
            category="tooling",
            reason=(
                "Sandboxed command execution is enabled by runtime policy."
                if runtime.feature_flags.allow_sandbox_execution
                else "Sandboxed command execution is disabled by runtime policy."
            ),
        ),
        RuntimeCapabilityStatus(
            name="remote_only_backend_fallback",
            state=remote_only_state,
            category="model_backend",
            reason=(
                "Runtime may stay alive without a healthy local backend and fall back to remote-only behavior."
                if runtime.feature_flags.allow_remote_only_without_backend
                else "Runtime requires a healthy backend and will fail closed if none is available."
            ),
        ),
        RuntimeCapabilityStatus(
            name="simulated_payments",
            state=flags["SIMULATED_PAYMENTS"].state,
            category="future_extension",
            reason=flags["SIMULATED_PAYMENTS"].reason,
        ),
        RuntimeCapabilityStatus(
            name="wan_public_mesh",
            state=flags["EXPERIMENTAL_WAN"].state,
            category="future_extension",
            reason=flags["EXPERIMENTAL_WAN"].reason,
        ),
    ]


def runtime_capability_snapshot(context: RuntimeContext | None = None) -> dict[str, Any]:
    runtime = context or build_runtime_context(mode="runtime_capabilities")
    probe = probe_machine()
    tier = select_qwen_tier(probe)
    provider_snapshot = build_provider_registry_snapshot(
        runtime_home=str(runtime.paths.runtime_home),
        requested_profile=str(os.environ.get("NULLA_INSTALL_PROFILE") or ""),
        honor_install_profile=True,
    )
    install_profile = build_install_profile_truth(
        probe=probe,
        tier=tier,
        provider_capability_truth=provider_snapshot.capability_truth,
        runtime_home=runtime.paths.runtime_home,
    )
    install_recommendation = build_install_recommendation_truth(
        probe=probe,
        tier=tier,
        selected_model=install_profile.selected_model,
        runtime_home=runtime.paths.runtime_home,
    )
    effective_local_only_mode = bool(runtime.feature_flags.local_only_mode) or install_profile_runs_local_only(
        install_profile.profile_id
    )
    statuses = runtime_capability_statuses(runtime)
    active_remote_fallback_available = (
        bool(runtime.feature_flags.allow_remote_only_without_backend)
        and not effective_local_only_mode
        and any(
        item.locality == "remote" for item in provider_snapshot.capability_truth
        )
    )
    capabilities_payload = [asdict(item) for item in statuses]
    if not active_remote_fallback_available:
        for row in capabilities_payload:
            if str(row.get("name") or "").strip() != "remote_only_backend_fallback":
                continue
            if str(row.get("state") or "").strip() == "disabled_by_policy":
                break
            row["state"] = "disabled_by_profile"
            row["reason"] = "Current install profile exposes no active remote model lane, so remote-only fallback is unavailable."
            break
    return {
        "mode": runtime.mode,
        "runtime_home": str(runtime.paths.runtime_home),
        "workspace_root": str(runtime.paths.workspace_root),
        "feature_flags": {
            "local_only_mode": effective_local_only_mode,
            "public_hive_enabled": runtime.feature_flags.public_hive_enabled,
            "helper_mesh_enabled": runtime.feature_flags.helper_mesh_enabled,
            "allow_workspace_writes": runtime.feature_flags.allow_workspace_writes,
            "allow_sandbox_execution": runtime.feature_flags.allow_sandbox_execution,
            "allow_remote_only_without_backend": active_remote_fallback_available,
        },
        "provider_capability_truth": [item.to_dict() for item in provider_snapshot.capability_truth],
        "install_profile": install_profile.to_dict(),
        "install_recommendation": install_recommendation.to_dict(),
        "capabilities": capabilities_payload,
    }
