from __future__ import annotations

from unittest import mock

from core.hardware_tier import MachineProbe, QwenTier
from core.provider_routing import ProviderCapabilityTruth
from core.runtime_install_profiles import build_install_profile_truth


def test_auto_profile_prefers_hybrid_kimi_on_smaller_host_when_kimi_is_configured() -> None:
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=12.0,
        gpu_name=None,
        vram_gb=None,
        accelerator="cpu",
    )
    tier = QwenTier("base", "qwen2.5:7b", 7.0, 4.0, 12.0)
    profile = build_install_profile_truth(
        probe=probe,
        tier=tier,
        env={"KIMI_API_KEY": "test-key"},
        provider_capability_truth=(
            ProviderCapabilityTruth(
                provider_id="local-qwen-http:qwen2.5:7b",
                model_id="qwen2.5:7b",
                role_fit="drone",
                context_window=32768,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=14.0,
                ram_budget_gb=12.0,
                vram_budget_gb=0.0,
                quantization="Q4_K_M",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
            ),
            ProviderCapabilityTruth(
                provider_id="kimi-remote:kimi-k2",
                model_id="kimi-k2",
                role_fit="queen",
                context_window=131072,
                tool_support=("tool_calls", "structured_json"),
                structured_output_support=True,
                tokens_per_second=0.0,
                ram_budget_gb=0.0,
                vram_budget_gb=0.0,
                quantization="provider",
                locality="remote",
                privacy_class="remote_provider",
                queue_depth=0,
                max_safe_concurrency=4,
            ),
        ),
        runtime_home="/tmp/nulla-runtime",
    )

    assert profile.profile_id == "hybrid-kimi"
    assert profile.ready is True
    assert any(item.provider_id == "kimi-remote:kimi-k2" and item.role == "queen" for item in profile.provider_mix)


def test_explicit_full_orchestrated_profile_fails_closed_when_keys_and_space_are_missing() -> None:
    probe = MachineProbe(
        cpu_cores=12,
        ram_gb=24.0,
        gpu_name="Apple Silicon",
        vram_gb=24.0,
        accelerator="mps",
    )
    tier = QwenTier("mid", "qwen2.5:14b", 14.0, 10.0, 24.0)
    fake_usage = mock.Mock()
    fake_usage.free = 10 * 1024**3

    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=fake_usage):
        profile = build_install_profile_truth(
            requested_profile="full-orchestrated",
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:14b",
            env={},
            runtime_home="/tmp/nulla-runtime",
        )

    assert profile.profile_id == "full-orchestrated"
    assert profile.ready is False
    assert profile.single_volume_ready is False
    assert any("KIMI_API_KEY" in reason for reason in profile.reasons)
    assert any("single target volume" in reason for reason in profile.reasons)
    assert profile.volume_checks
    assert profile.volume_checks[0].required_gb > profile.volume_checks[0].free_gb


def test_local_max_profile_prefers_ollama_coder_even_when_llamacpp_is_listed_first() -> None:
    probe = MachineProbe(
        cpu_cores=16,
        ram_gb=32.0,
        gpu_name="Apple Silicon",
        vram_gb=18.0,
        accelerator="mps",
    )
    tier = QwenTier("mid", "qwen2.5:14b", 14.0, 10.0, 24.0)
    profile = build_install_profile_truth(
        requested_profile="local-max",
        probe=probe,
        tier=tier,
        selected_model="qwen2.5:14b",
        env={},
        provider_capability_truth=(
            ProviderCapabilityTruth(
                provider_id="llamacpp-local:qwen2.5:14b-gguf",
                model_id="qwen2.5:14b-gguf",
                role_fit="verifier",
                context_window=16384,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=22.0,
                ram_budget_gb=20.0,
                vram_budget_gb=0.0,
                quantization="Q6_K",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
            ),
            ProviderCapabilityTruth(
                provider_id="ollama-local:qwen2.5:14b",
                model_id="qwen2.5:14b",
                role_fit="coder",
                context_window=32768,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=16.0,
                ram_budget_gb=24.0,
                vram_budget_gb=0.0,
                quantization="Q4_K_M",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
            ),
        ),
        runtime_home="/tmp/nulla-runtime",
    )

    assert profile.profile_id == "local-max"
    assert profile.ready is True
    assert any(item.provider_id == "ollama-local:qwen2.5:14b" and item.role == "coder" for item in profile.provider_mix)
    assert any(
        item.provider_id == "llamacpp-local:qwen2.5:14b-gguf" and item.role == "verifier"
        for item in profile.provider_mix
    )
