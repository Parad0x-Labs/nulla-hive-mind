from __future__ import annotations

import json
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
    assert report["recommended_install_profile_display_id"] == "ollama-max (local-max)"
    assert report["local_multi_llm_fit"] == "pressure_sensitive"
    dual = next(item for item in report["stacks"] if item["stack_id"] == "local_dual_ollama")
    assert dual["install_profile_id"] == "local-max"
    assert dual["install_profile_display_id"] == "ollama-max (local-max)"
    assert dual["status"] == "ready"


def test_probe_report_marks_kimi_and_tether_lanes_real_but_leaves_qvac_honest() -> None:
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
    tether = next(item for item in report["stacks"] if item["stack_id"] == "local_plus_tether")
    qvac = next(item for item in report["unsupported_stacks"] if item["stack_id"] == "local_plus_qvac")

    assert kimi["status"] == "ready"
    assert "real remote kimi queen lane" in kimi["reason"].lower()
    assert tether["status"] == "misconfigured"
    assert tether["install_profile_id"] == "hybrid-tether"
    assert tether["install_profile_display_id"] == "ollama+tether (hybrid-tether)"
    assert "did not register a usable tether lane" in tether["reason"].lower()
    assert qvac["status"] == "not_implemented"


def test_probe_report_prefers_generic_remote_fallback_when_smaller_host_has_real_remote_lane() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": False},
            "generic_remote": {"configured": True},
            "tether": {"configured": False},
            "qvac": {"configured": False},
        },
        provider_capability_truth=(
            ProviderCapabilityTruth(
                provider_id="openai-compatible-remote:gpt-4.1-mini",
                model_id="gpt-4.1-mini",
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
                max_safe_concurrency=2,
                availability_state="ready",
            ),
        ),
    )

    assert report["recommended_stack_id"] == "local_plus_remote_openai_compatible"
    assert report["recommended_install_profile_id"] == "hybrid-fallback"
    fallback = next(item for item in report["stacks"] if item["stack_id"] == "local_plus_remote_openai_compatible")
    assert fallback["recommended"] is True
    assert fallback["status"] == "ready"
    assert fallback["install_profile_display_id"] == "hybrid-fallback"


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
    assert report["recommended_install_profile_display_id"] == "ollama+kimi (hybrid-kimi)"
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
    assert "ollama-only (local-only)" in rendered
    assert "local_plus_remote_openai_compatible" in rendered
    assert "local_plus_tether" in rendered
    assert "ollama+tether (hybrid-tether)" in rendered
    assert "needs_config" in rendered


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
    assert any(item["stack_id"] == "local_plus_tether" for item in report["stacks"])
    assert all(item["stack_id"] != "local_plus_qvac" for item in report["stacks"])


def test_list_ollama_models_preserves_size_and_modified_columns(monkeypatch) -> None:
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_api", lambda *args, **kwargs: [])
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_manifests", lambda: [])

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


def test_list_ollama_models_prefers_tags_api_and_avoids_cli_shellout(monkeypatch) -> None:
    monkeypatch.setattr("installer.provider_probe.shutil.which", lambda name: "/usr/bin/curl" if name == "curl" else "")
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_manifests", lambda: [])
    monkeypatch.setattr(
        "installer.provider_probe.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                '{"models":[{"name":"qwen2.5:14b","digest":"7cdf5a0187d5abcd","size":8988124069,'
                '"modified_at":"2026-03-28T09:38:28Z"}]}'
            ),
            stderr="",
        ),
    )

    rows = list_ollama_models("/usr/local/bin/ollama")

    assert rows == [
        {
            "name": "qwen2.5:14b",
            "id": "7cdf5a0187d5",
            "size": "8.4 GB",
            "modified": "2026-03-28T09:38:28Z",
        }
    ]


def test_list_ollama_models_falls_back_to_manifest_inventory(monkeypatch, tmp_path) -> None:
    manifest_root = tmp_path / "models" / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5"
    manifest_root.mkdir(parents=True)
    (manifest_root / "14b").write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "mediaType": "application/vnd.ollama.image.model",
                        "digest": "sha256:2049f5674b1e92b4464e5729975c9689fcfbf0b0e4443ccf10b5339f370f9a54",
                        "size": 8988110688,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (manifest_root / "7b").write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "mediaType": "application/vnd.ollama.image.model",
                        "digest": "sha256:2bada8a7450677000f678be90653b85d364de7db25eb5ea54136ada5f3933730",
                        "size": 4683073952,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_api", lambda *args, **kwargs: [])
    monkeypatch.setattr("installer.provider_probe.default_ollama_models_path", lambda: (tmp_path / "models").resolve())

    rows = list_ollama_models("/usr/local/bin/ollama")

    assert [row["name"] for row in rows] == ["qwen2.5:14b", "qwen2.5:7b"]


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
