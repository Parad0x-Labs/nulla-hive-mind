from __future__ import annotations

import subprocess
from unittest import mock

from core.hardware_tier import MachineProbe
from core.provider_routing import ProviderCapabilityTruth
from installer.provider_probe import build_probe_report, list_ollama_models, remote_env_statuses, render_probe_report


def test_probe_report_prefers_dual_local_stack_on_24gb_host_with_required_models() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[
            {"name": "qwen2.5:14b", "id": "a", "size": "9.0 GB", "modified": "today"},
            {"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"},
        ],
        env_statuses={
            "kimi": {"configured": False},
            "generic_remote": {"configured": False},
            "tether": {"configured": False},
            "qvac": {"configured": False},
        },
    )

    assert report["recommended_stack_id"] == "local_dual_ollama"
    assert report["recommended_install_profile_id"] == "local-max"
    assert report["local_multi_llm_fit"] == "pressure_sensitive"
    dual = next(item for item in report["stacks"] if item["stack_id"] == "local_dual_ollama")
    assert dual["install_profile_id"] == "local-max"
    assert dual["status"] == "ready"


def test_probe_report_marks_kimi_lane_real_but_unwired_remote_lanes_honestly() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[],
        env_statuses={
            "kimi": {"configured": True},
            "generic_remote": {"configured": False},
            "tether": {"configured": True},
            "qvac": {"configured": True},
        },
        provider_capability_truth=(
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
                availability_state="ready",
            ),
        ),
        show_unsupported=True,
    )

    kimi = next(item for item in report["stacks"] if item["stack_id"] == "local_plus_kimi")
    tether = next(item for item in report["unsupported_stacks"] if item["stack_id"] == "local_plus_tether")
    qvac = next(item for item in report["unsupported_stacks"] if item["stack_id"] == "local_plus_qvac")

    assert kimi["status"] == "ready"
    assert "real remote kimi queen lane" in kimi["reason"].lower()
    assert tether["status"] == "not_implemented"
    assert qvac["status"] == "not_implemented"


def test_probe_report_prefers_kimi_on_smaller_host_when_configured_and_ready() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": True},
            "generic_remote": {"configured": False},
            "tether": {"configured": False},
            "qvac": {"configured": False},
        },
        provider_capability_truth=(
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
                availability_state="ready",
            ),
        ),
    )

    assert report["recommended_stack_id"] == "local_plus_kimi"
    assert report["recommended_install_profile_id"] == "hybrid-kimi"
    kimi = next(item for item in report["stacks"] if item["stack_id"] == "local_plus_kimi")
    assert kimi["recommended"] is True
    assert kimi["status"] == "ready"


def test_render_probe_report_surfaces_installed_models_and_recommendation() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": False},
            "generic_remote": {"configured": False},
            "tether": {"configured": False},
            "qvac": {"configured": False},
        },
    )

    rendered = render_probe_report(report)
    assert "recommended install profile" in rendered.lower()
    assert "recommended stack" in rendered.lower()
    assert "qwen2.5:7b" in rendered
    assert "local_only" in rendered
    assert "local_plus_tether" not in rendered


def test_default_probe_report_hides_unsupported_remote_ideas() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": False},
            "generic_remote": {"configured": False},
            "tether": {"configured": True},
            "qvac": {"configured": True},
        },
    )

    assert "unsupported_stacks" not in report
    assert all(item["stack_id"] not in {"local_plus_tether", "local_plus_qvac"} for item in report["stacks"])


def test_list_ollama_models_preserves_size_and_modified_columns(monkeypatch) -> None:
    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                "NAME           ID              SIZE      MODIFIED      \n"
                "qwen2.5:14b    7cdf5a0187d5    9.0 GB    3 minutes ago    \n"
            ),
            stderr="",
        )

    monkeypatch.setattr("installer.provider_probe.subprocess.run", fake_run)
    monkeypatch.setattr("installer.provider_probe.detect_ollama_binary", lambda: "/usr/local/bin/ollama")

    rows = list_ollama_models()

    assert rows == [
        {
            "name": "qwen2.5:14b",
            "id": "7cdf5a0187d5",
            "size": "9.0 GB",
            "modified": "3 minutes ago",
        }
    ]


def test_remote_env_statuses_accepts_moonshot_aliases_for_kimi() -> None:
    with mock.patch.dict(
        "os.environ",
        {
            "MOONSHOT_API_KEY": "test-key",
            "MOONSHOT_BASE_URL": "https://api.moonshot.ai/v1",
            "MOONSHOT_MODEL": "kimi-k2",
        },
        clear=True,
    ):
        statuses = remote_env_statuses()

    assert statuses["kimi"]["api_key_present"] is True
    assert statuses["kimi"]["base_url_present"] is True
    assert statuses["kimi"]["model_present"] is True
    assert statuses["kimi"]["configured"] is True
