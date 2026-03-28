from __future__ import annotations

import core.hardware_tier as hardware_tier
from core.hardware_tier import MachineProbe, clear_probe_machine_cache, probe_machine, select_qwen_tier


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

    assert tier.tier_name == "mid"
    assert tier.ollama_tag == "qwen2.5:14b"


def test_select_qwen_tier_keeps_discrete_vram_selection_for_non_mps(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_OLLAMA_MODEL", raising=False)
    probe = MachineProbe(cpu_cores=16, ram_gb=16.0, gpu_name="NVIDIA", vram_gb=24.0, accelerator="cuda")

    tier = select_qwen_tier(probe)

    assert tier.tier_name == "heavy"
    assert tier.ollama_tag == "qwen2.5:32b"


def test_probe_machine_reuses_cached_snapshot(monkeypatch) -> None:
    calls = {"ram": 0, "gpu": 0}

    def _fake_ram() -> float:
        calls["ram"] += 1
        return 24.0

    def _fake_gpu() -> tuple[str | None, float | None, str]:
        calls["gpu"] += 1
        return "Apple Silicon", 24.0, "mps"

    monkeypatch.setattr(hardware_tier, "_detect_ram_gb", _fake_ram)
    monkeypatch.setattr(hardware_tier, "_detect_gpu", _fake_gpu)

    clear_probe_machine_cache()
    first = probe_machine()
    second = probe_machine()
    clear_probe_machine_cache()

    assert first.ram_gb == 24.0
    assert second.accelerator == "mps"
    assert calls == {"ram": 1, "gpu": 1}


def test_probe_machine_force_refresh_reprobes(monkeypatch) -> None:
    values = iter(
        [
            (24.0, ("Apple Silicon", 24.0, "mps")),
            (48.0, ("Apple Silicon", 48.0, "mps")),
        ]
    )

    def _fake_ram() -> float:
        return current[0]

    def _fake_gpu() -> tuple[str | None, float | None, str]:
        return current[1]

    current = next(values)
    monkeypatch.setattr(hardware_tier, "_detect_ram_gb", _fake_ram)
    monkeypatch.setattr(hardware_tier, "_detect_gpu", _fake_gpu)

    clear_probe_machine_cache()
    first = probe_machine()
    current = next(values)
    second = probe_machine(force_refresh=True)
    clear_probe_machine_cache()

    assert first.ram_gb == 24.0
    assert second.ram_gb == 48.0
