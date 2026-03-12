from __future__ import annotations

from core.hardware_tier import MachineProbe, select_qwen_tier


def test_select_qwen_tier_honors_env_override(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OLLAMA_MODEL", "qwen2.5:7b")
    probe = MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "base"
    assert tier.ollama_tag == "qwen2.5:7b"


def test_select_qwen_tier_supports_custom_override_tag(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OLLAMA_MODEL", "ollama/custom-qwen")
    probe = MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "override"
    assert tier.ollama_tag == "custom-qwen"
