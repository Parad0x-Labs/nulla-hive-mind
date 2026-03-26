from __future__ import annotations

from pathlib import Path
from unittest import mock

from core.hardware_tier import MachineProbe, QwenTier
from core.provider_routing import ProviderCapabilityTruth
from core.runtime_backbone import ProviderRegistrySnapshot
from core.runtime_capabilities import runtime_capability_snapshot, runtime_capability_statuses
from core.runtime_context import RuntimeContext, RuntimeFeatureFlags, RuntimePaths
from core.runtime_install_profiles import InstallProfileTruth


def _context(**feature_overrides: bool) -> RuntimeContext:
    flags = RuntimeFeatureFlags(
        local_only_mode=feature_overrides.get("local_only_mode", False),
        public_hive_enabled=feature_overrides.get("public_hive_enabled", True),
        helper_mesh_enabled=feature_overrides.get("helper_mesh_enabled", True),
        allow_workspace_writes=feature_overrides.get("allow_workspace_writes", False),
        allow_sandbox_execution=feature_overrides.get("allow_sandbox_execution", False),
        allow_remote_only_without_backend=feature_overrides.get("allow_remote_only_without_backend", True),
    )
    paths = RuntimePaths(
        project_root=Path("/tmp/project"),
        runtime_home=Path("/tmp/runtime"),
        data_dir=Path("/tmp/runtime/data"),
        config_home_dir=Path("/tmp/runtime/config"),
        docs_dir=Path("/tmp/project/docs"),
        project_config_dir=Path("/tmp/project/config"),
        workspace_root=Path("/tmp/project/workspace"),
        db_path=Path("/tmp/runtime/data/nulla.db"),
    )
    return RuntimeContext(
        mode="test",
        paths=paths,
        log_level="INFO",
        json_logs=True,
        feature_flags=flags,
    )


def test_runtime_capability_statuses_reflect_policy_disabled_surfaces() -> None:
    statuses = {item.name: item for item in runtime_capability_statuses(_context(public_hive_enabled=False, helper_mesh_enabled=False))}

    assert statuses["public_hive_surface"].state == "disabled_by_policy"
    assert statuses["helper_mesh"].state == "disabled_by_policy"
    assert statuses["simulated_payments"].state == "simulated"


def test_runtime_capability_snapshot_exposes_feature_flags_and_capability_rows() -> None:
    install_profile = InstallProfileTruth(
        profile_id="local-only",
        label="Local only",
        summary="Single local Ollama lane with no remote provider dependency.",
        selection_source="auto",
        selected_model="qwen2.5:7b",
        provider_mix=tuple(),
        estimated_download_gb=8.0,
        estimated_disk_footprint_gb=12.0,
        minimum_free_space_gb=11.0,
        ram_expectation_gb=12.0,
        vram_expectation_gb=4.0,
        ready=True,
        degraded=False,
        single_volume_ready=True,
        reasons=tuple(),
        volume_checks=tuple(),
    )
    provider_snapshot = ProviderRegistrySnapshot(
        warnings=tuple(),
        audit_rows=tuple(),
        capability_truth=(
            ProviderCapabilityTruth(
                provider_id="local-qwen-http:qwen2.5:7b",
                model_id="qwen2.5:7b",
                role_fit="drone",
                context_window=32768,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=12.0,
                ram_budget_gb=12.0,
                vram_budget_gb=0.0,
                quantization="Q4_K_M",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
                availability_state="ready",
                circuit_open=False,
                last_error=None,
            ),
        ),
    )

    with mock.patch("core.runtime_capabilities.probe_machine", return_value=MachineProbe(8, 12.0, None, None, "cpu")), mock.patch(
        "core.runtime_capabilities.select_qwen_tier",
        return_value=QwenTier("base", "qwen2.5:7b", 7.0, 4.0, 12.0),
    ), mock.patch(
        "core.runtime_capabilities.build_provider_registry_snapshot",
        return_value=provider_snapshot,
    ), mock.patch(
        "core.runtime_capabilities.build_install_profile_truth",
        return_value=install_profile,
    ):
        snapshot = runtime_capability_snapshot(
            _context(allow_workspace_writes=True, allow_sandbox_execution=True, allow_remote_only_without_backend=False)
        )

    assert snapshot["mode"] == "test"
    assert snapshot["feature_flags"]["allow_workspace_writes"] is True
    assert snapshot["feature_flags"]["allow_sandbox_execution"] is True
    assert snapshot["feature_flags"]["allow_remote_only_without_backend"] is False
    assert snapshot["install_profile"]["profile_id"] == "local-only"
    assert snapshot["provider_capability_truth"][0]["provider_id"] == "local-qwen-http:qwen2.5:7b"
    assert snapshot["provider_capability_truth"][0]["availability_state"] == "ready"
    assert snapshot["provider_capability_truth"][0]["circuit_open"] is False
    capabilities = {item["name"]: item for item in snapshot["capabilities"]}
    assert capabilities["workspace_write_tools"]["state"] == "implemented"
    assert capabilities["sandbox_execution"]["state"] == "implemented"
    assert capabilities["remote_only_backend_fallback"]["state"] == "disabled_by_policy"
