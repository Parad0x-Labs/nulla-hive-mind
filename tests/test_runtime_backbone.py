from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.nulla_cli import cmd_providers
from core.hardware_tier import MachineProbe, QwenTier
from core.model_registry import ProviderAuditRow
from core.runtime_backbone import (
    ProviderRegistrySnapshot,
    build_provider_registry_snapshot,
    build_runtime_backbone,
)
from core.runtime_bootstrap import BootstrappedRuntime, RuntimeBackendSelection


def test_build_provider_registry_snapshot_collects_rows_and_warnings_from_registry() -> None:
    row = ProviderAuditRow(
        provider_id="local-qwen-http:qwen2.5:14b",
        source_type="http",
        license_name="Apache-2.0",
        license_reference="https://www.apache.org/licenses/LICENSE-2.0",
        runtime_dependency="ollama",
        weight_location="user-supplied",
        weights_bundled=False,
        redistribution_allowed=True,
        warnings=["missing health path"],
    )
    registry = mock.Mock()
    registry.startup_warnings.return_value = ["missing health path"]
    registry.provider_audit_rows.return_value = [row]

    snapshot = build_provider_registry_snapshot(registry)

    assert snapshot.warnings == ("missing health path",)
    assert snapshot.audit_rows == (row,)


def test_build_runtime_backbone_reuses_bootstrap_probe_and_provider_facades() -> None:
    probe = MachineProbe(
        cpu_cores=12,
        ram_gb=48.0,
        gpu_name="NVIDIA",
        vram_gb=24.0,
        accelerator="cuda",
    )
    tier = QwenTier("heavy", "qwen2.5:32b", 32.0, 20.0, 48.0)
    fake_boot = BootstrappedRuntime(
        context=SimpleNamespace(mode="chat"),
        backend_selection=RuntimeBackendSelection(
            backend_name="TorchCUDABackend",
            device="cuda",
            reason="CUDA-capable GPU detected.",
            hardware=SimpleNamespace(os_name="linux", machine="x86_64"),
        ),
    )
    provider_snapshot = ProviderRegistrySnapshot(warnings=("warn",), audit_rows=tuple())

    with mock.patch(
        "core.runtime_backbone.bootstrap_runtime_mode",
        return_value=fake_boot,
    ) as bootstrap_runtime, mock.patch(
        "core.runtime_backbone.probe_machine",
        return_value=probe,
    ) as probe_machine, mock.patch(
        "core.runtime_backbone.select_qwen_tier",
        return_value=tier,
    ) as select_tier, mock.patch(
        "core.runtime_backbone.tier_summary",
        return_value={"accelerator": "cuda", "ram_gb": 48.0, "gpu": "NVIDIA", "vram_gb": 24.0},
    ) as tier_summary_fn, mock.patch(
        "core.runtime_backbone.build_provider_registry_snapshot",
        return_value=provider_snapshot,
    ) as provider_fn:
        backbone = build_runtime_backbone(
            mode="chat",
            force_policy_reload=True,
            resolve_backend=True,
        )

    bootstrap_runtime.assert_called_once_with(
        mode="chat",
        workspace_root=None,
        db_path=None,
        force_policy_reload=True,
        configure_logging=False,
        resolve_backend=True,
        manager=None,
        allow_remote_only=None,
    )
    probe_machine.assert_called_once_with()
    select_tier.assert_called_once_with(probe)
    tier_summary_fn.assert_called_once_with(probe)
    provider_fn.assert_called_once_with(None)
    assert backbone.boot is fake_boot
    assert backbone.local_model_profile.probe is probe
    assert backbone.local_model_profile.tier is tier
    assert backbone.local_model_profile.summary["backend_name"] == "TorchCUDABackend"
    assert backbone.local_model_profile.summary["backend_device"] == "cuda"
    assert backbone.provider_snapshot is provider_snapshot


def test_cmd_providers_renders_provider_snapshot_from_runtime_backbone_facade(capsys) -> None:
    row = ProviderAuditRow(
        provider_id="local-qwen-http:qwen2.5:14b",
        source_type="http",
        license_name="Apache-2.0",
        license_reference="https://www.apache.org/licenses/LICENSE-2.0",
        runtime_dependency="ollama",
        weight_location="user-supplied",
        weights_bundled=False,
        redistribution_allowed=True,
        warnings=[],
    )
    snapshot = ProviderRegistrySnapshot(warnings=tuple(), audit_rows=(row,))

    with mock.patch("apps.nulla_cli._bootstrap_cli_storage") as bootstrap_storage, mock.patch(
        "apps.nulla_cli.build_provider_registry_snapshot",
        return_value=snapshot,
    ) as build_snapshot:
        assert cmd_providers(json_mode=False) == 0

    bootstrap_storage.assert_called_once_with()
    build_snapshot.assert_called_once_with()
    out = capsys.readouterr().out
    assert "NULLA model providers" in out
    assert "local-qwen-http:qwen2.5:14b" in out
