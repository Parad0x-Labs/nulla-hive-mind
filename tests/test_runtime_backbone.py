from __future__ import annotations

import os
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
from core.runtime_install_profiles import InstallProfileTruth


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
    registry.list_manifests.return_value = []

    snapshot = build_provider_registry_snapshot(registry)

    assert snapshot.warnings == ("missing health path",)
    assert snapshot.audit_rows == (row,)
    assert snapshot.prewarm_results == tuple()


def test_build_provider_registry_snapshot_auto_registers_kimi_when_configured() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "KIMI_API_KEY": "test-key",
            "KIMI_BASE_URL": "https://kimi.example/v1",
            "NULLA_KIMI_MODEL": "kimi-latest",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry)

    kimi_manifest = manifests[("kimi-remote", "kimi-latest")]
    assert kimi_manifest.adapter_type == "openai_compatible"
    assert kimi_manifest.runtime_config["base_url"] == "https://kimi.example/v1"
    assert kimi_manifest.runtime_config["api_key_env"] == "KIMI_API_KEY"
    assert any(item.provider_id == "kimi-remote:kimi-latest" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_honors_local_only_install_profile_for_remote_gating() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "KIMI_API_KEY": "test-key",
            "NULLA_INSTALL_PROFILE": "local-only",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry, honor_install_profile=True)

    assert any(item.provider_id.startswith("ollama-local:") for item in snapshot.capability_truth)
    assert not any(item.provider_id.startswith("kimi-remote:") for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_registers_helper_ollama_lane_for_local_max() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch("core.runtime_provider_defaults.default_runtime_model_tag", return_value="qwen2.5:14b"), mock.patch.dict(
        os.environ,
        {
            "NULLA_INSTALL_PROFILE": "local-max",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry, honor_install_profile=True)

    assert ("ollama-local", "qwen2.5:14b") in manifests
    assert ("ollama-local", "qwen2.5:7b") in manifests
    assert any(item.provider_id == "ollama-local:qwen2.5:7b" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_includes_local_ollama_prewarm_config() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests
    registry.prewarm_enabled_providers.return_value = [
        {"ok": True, "provider_id": "ollama-local:qwen2.5:14b", "status": "prewarmed", "keep_alive": "15m"}
    ]

    with mock.patch("core.runtime_provider_defaults.default_runtime_model_tag", return_value="qwen2.5:14b"):
        snapshot = build_provider_registry_snapshot(registry, run_prewarm=True)

    manifest = manifests[("ollama-local", "qwen2.5:14b")]
    assert manifest.runtime_config["prewarm"]["strategy"] == "ollama_generate"
    assert snapshot.prewarm_results == (
        {"ok": True, "provider_id": "ollama-local:qwen2.5:14b", "status": "prewarmed", "keep_alive": "15m"},
    )


def test_build_provider_registry_snapshot_accepts_moonshot_aliases_for_kimi() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "MOONSHOT_API_KEY": "test-key",
            "MOONSHOT_BASE_URL": "https://kimi.example/v1",
            "MOONSHOT_MODEL": "kimi-moonshot",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry)

    kimi_manifest = manifests[("kimi-remote", "kimi-moonshot")]
    assert kimi_manifest.runtime_config["base_url"] == "https://kimi.example/v1"
    assert kimi_manifest.runtime_config["api_key_env"] == "MOONSHOT_API_KEY"
    assert any(item.provider_id == "kimi-remote:kimi-moonshot" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_auto_registers_vllm_when_configured() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "VLLM_BASE_URL": "http://127.0.0.1:8100/v1",
            "NULLA_VLLM_MODEL": "qwen2.5:32b-vllm",
            "VLLM_CONTEXT_WINDOW": "65536",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry)

    vllm_manifest = manifests[("vllm-local", "qwen2.5:32b-vllm")]
    assert vllm_manifest.adapter_type == "openai_compatible"
    assert vllm_manifest.runtime_config["base_url"] == "http://127.0.0.1:8100/v1"
    assert vllm_manifest.metadata["orchestration_role"] == "queen"
    assert any(item.provider_id == "vllm-local:qwen2.5:32b-vllm" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_auto_registers_llamacpp_when_configured() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "LLAMACPP_BASE_URL": "http://127.0.0.1:8090/v1",
            "NULLA_LLAMACPP_MODEL": "qwen2.5:14b-gguf",
            "LLAMACPP_CONTEXT_WINDOW": "16384",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry)

    manifest = manifests[("llamacpp-local", "qwen2.5:14b-gguf")]
    assert manifest.adapter_type == "openai_compatible"
    assert manifest.runtime_config["base_url"] == "http://127.0.0.1:8090/v1"
    assert manifest.metadata["orchestration_role"] == "drone"
    assert any(item.provider_id == "llamacpp-local:qwen2.5:14b-gguf" for item in snapshot.capability_truth)


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
    provider_snapshot = ProviderRegistrySnapshot(warnings=("warn",), audit_rows=tuple(), capability_truth=tuple())
    install_profile = InstallProfileTruth(
        profile_id="local-only",
        label="Local only",
        summary="Single local Ollama lane with no remote provider dependency.",
        selection_source="auto",
        selected_model="qwen2.5:32b",
        provider_mix=tuple(),
        estimated_download_gb=36.0,
        estimated_disk_footprint_gb=41.0,
        minimum_free_space_gb=39.0,
        ram_expectation_gb=48.0,
        vram_expectation_gb=20.0,
        ready=True,
        degraded=False,
        single_volume_ready=True,
        reasons=tuple(),
        volume_checks=tuple(),
    )

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
    ) as provider_snapshot_fn, mock.patch(
        "core.runtime_backbone.build_install_profile_truth",
        return_value=install_profile,
    ) as install_profile_fn:
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
    provider_snapshot_fn.assert_called_once_with(
        None,
        runtime_home=None,
        honor_install_profile=True,
        run_prewarm=True,
    )
    install_profile_fn.assert_called_once()
    assert backbone.boot is fake_boot
    assert backbone.local_model_profile.probe is probe
    assert backbone.local_model_profile.tier is tier
    assert backbone.local_model_profile.summary["backend_name"] == "TorchCUDABackend"
    assert backbone.local_model_profile.summary["backend_device"] == "cuda"
    assert backbone.provider_snapshot is provider_snapshot
    assert backbone.install_profile is install_profile


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
    snapshot = ProviderRegistrySnapshot(warnings=tuple(), audit_rows=(row,), capability_truth=tuple())

    fake_context = SimpleNamespace(paths=SimpleNamespace(runtime_home="/tmp/nulla-runtime"))

    with mock.patch("apps.nulla_cli._bootstrap_cli_storage") as bootstrap_storage, mock.patch(
        "apps.nulla_cli.build_runtime_context",
        return_value=fake_context,
    ) as build_context, mock.patch(
        "apps.nulla_cli.build_provider_registry_snapshot",
        return_value=snapshot,
    ) as build_snapshot:
        assert cmd_providers(json_mode=False) == 0

    bootstrap_storage.assert_called_once_with()
    build_context.assert_called_once_with(mode="cli_storage")
    build_snapshot.assert_called_once_with(
        runtime_home="/tmp/nulla-runtime",
        honor_install_profile=True,
    )
    out = capsys.readouterr().out
    assert "NULLA model providers" in out
    assert "local-qwen-http:qwen2.5:14b" in out
