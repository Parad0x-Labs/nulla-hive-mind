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


def test_select_qwen_tier_uses_ram_thresholds_for_apple_unified_memory(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "base"
    assert tier.ollama_tag == "qwen2.5:7b"


def test_select_qwen_tier_unlocks_14b_on_higher_ram_apple_unified_memory(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(cpu_cores=12, ram_gb=36.0, gpu_name="Apple Silicon", vram_gb=36.0, accelerator="mps")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "mid"
    assert tier.ollama_tag == "qwen2.5:14b"


def test_select_qwen_tier_keeps_discrete_vram_selection_for_non_mps(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(cpu_cores=16, ram_gb=16.0, gpu_name="NVIDIA", vram_gb=24.0, accelerator="cuda")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "heavy"
    assert tier.ollama_tag == "qwen2.5:32b"
