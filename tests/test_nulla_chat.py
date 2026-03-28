from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.hardware_tier import MachineProbe


def test_bootstrap_agent_ensures_default_provider_before_runtime_start() -> None:
    from apps import nulla_chat

    fake_boot = SimpleNamespace(
        backend_selection=SimpleNamespace(backend_name="local_ollama", device="cpu")
    )
    fake_compute = mock.Mock()
    fake_compute.budget = SimpleNamespace(mode="interactive", cpu_threads=4, gpu_memory_fraction=0.5)
    fake_registry = mock.Mock()
    fake_registry.startup_warnings.return_value = []
    fake_registry.prewarm_enabled_providers.return_value = []
    fake_agent = mock.Mock()
    fake_agent.start.return_value = SimpleNamespace(
        backend_name="local_ollama",
        device="openclaw",
        persona_id="default",
    )

    with mock.patch("apps.nulla_chat.bootstrap_runtime_mode", return_value=fake_boot), mock.patch(
        "apps.nulla_chat.is_first_boot",
        return_value=False,
    ), mock.patch(
        "apps.nulla_chat.probe_machine",
        return_value=MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps"),
    ), mock.patch(
        "apps.nulla_chat.select_qwen_tier",
        return_value=SimpleNamespace(tier_name="mid", ollama_tag="qwen2.5:14b"),
    ), mock.patch(
        "apps.nulla_chat.tier_summary",
        return_value={"accelerator": "mps", "ram_gb": 24.0, "gpu": "Apple Silicon", "vram_gb": 24.0},
    ), mock.patch(
        "apps.nulla_chat.ComputeModeDaemon",
        return_value=fake_compute,
    ), mock.patch(
        "apps.nulla_chat.ModelRegistry",
        return_value=fake_registry,
    ), mock.patch(
        "apps.nulla_chat.ensure_default_provider",
    ) as ensure_mock, mock.patch(
        "apps.nulla_chat.NullaAgent",
        return_value=fake_agent,
    ), mock.patch(
        "apps.nulla_chat.get_agent_display_name",
        return_value="NULLA",
    ):
        result = nulla_chat._bootstrap_agent(persona_id="default", device="openclaw")

    assert result is fake_agent
    ensure_mock.assert_called_once_with(fake_registry, "qwen2.5:14b")
    fake_agent.start.assert_called_once()
