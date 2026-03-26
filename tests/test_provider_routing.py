from __future__ import annotations

from core.model_health import record_provider_failure, reset_provider_health
from core.model_registry import ModelRegistry
from core.provider_routing import (
    provider_capability_truth_for_manifest,
    rank_provider_candidates,
    resolve_provider_routing_plan,
)
from storage.db import get_connection
from storage.migrations import run_migrations


def _clear_manifests() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()


def _register_default_manifests(registry: ModelRegistry):
    local_manifest = registry.register_manifest(
        {
            "provider_name": "local-qwen-http",
            "model_name": "qwen2.5:14b",
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied",
            "weights_bundled": False,
            "redistribution_allowed": True,
            "runtime_dependency": "ollama",
            "capabilities": ["summarize", "classify", "format", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:11434"},
            "metadata": {"deployment_class": "local", "orchestration_role": "drone"},
            "enabled": True,
        }
    )
    kimi_manifest = registry.register_manifest(
        {
            "provider_name": "kimi-remote",
            "model_name": "kimi-k2",
            "source_type": "http",
            "adapter_type": "openai_compatible",
            "license_name": "Provider",
            "license_reference": "user-managed",
            "weight_location": "external",
            "weights_bundled": False,
            "redistribution_allowed": False,
            "runtime_dependency": "remote-openai-compatible-provider",
            "capabilities": ["summarize", "classify", "format", "long_context", "code_complex", "structured_json"],
            "runtime_config": {"base_url": "https://kimi.example", "api_key_env": "KIMI_API_KEY"},
            "metadata": {"deployment_class": "cloud", "orchestration_role": "queen"},
            "enabled": True,
        }
    )
    remote_manifest = registry.register_manifest(
        {
            "provider_name": "remote-generic",
            "model_name": "helper",
            "source_type": "http",
            "adapter_type": "openai_compatible",
            "license_name": "Provider",
            "license_reference": "user-managed",
            "weight_location": "external",
            "weights_bundled": False,
            "redistribution_allowed": False,
            "runtime_dependency": "remote-openai-compatible-provider",
            "capabilities": ["summarize", "classify", "format"],
            "runtime_config": {"base_url": "https://remote.example"},
            "metadata": {"deployment_class": "cloud"},
            "enabled": True,
        }
    )
    return local_manifest, kimi_manifest, remote_manifest


def test_drone_role_prefers_local_qwen_lane() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    _register_default_manifests(registry)

    ranked = rank_provider_candidates(
        registry,
        task_kind="summarization",
        output_mode="summary_block",
        role="drone",
        swarm_size=2,
    )

    assert ranked
    assert ranked[0].provider_name == "local-qwen-http"
    assert {manifest.provider_name for manifest in ranked[:2]} == {"local-qwen-http", "remote-generic"}


def test_queen_role_prefers_kimi_when_present() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    _register_default_manifests(registry)

    plan = resolve_provider_routing_plan(
        registry,
        task_kind="action_plan",
        output_mode="action_plan",
        role="queen",
        swarm_size=2,
    )

    assert plan.selected is not None
    assert plan.selected.provider_name == "kimi-remote"
    assert plan.candidate_provider_ids[0] == "kimi-remote:kimi-k2"


def test_queen_role_falls_back_to_best_local_when_remote_absent() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    registry.register_manifest(
        {
            "provider_name": "local-qwen-http",
            "model_name": "qwen2.5:32b",
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied",
            "weights_bundled": False,
            "redistribution_allowed": True,
            "runtime_dependency": "ollama",
            "capabilities": ["summarize", "classify", "format", "long_context", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:11434"},
            "metadata": {"deployment_class": "local"},
            "enabled": True,
        }
    )

    plan = resolve_provider_routing_plan(
        registry,
        task_kind="action_plan",
        output_mode="action_plan",
        role="queen",
    )

    assert plan.selected is not None
    assert plan.selected.provider_name == "local-qwen-http"
    assert plan.candidate_provider_ids == ("local-qwen-http:qwen2.5:32b",)


def test_queen_role_prefers_local_vllm_when_no_remote_queen_exists() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    registry.register_manifest(
        {
            "provider_name": "local-qwen-http",
            "model_name": "qwen2.5:14b",
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied",
            "weights_bundled": False,
            "redistribution_allowed": True,
            "runtime_dependency": "ollama",
            "capabilities": ["summarize", "classify", "format", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:11434"},
            "metadata": {"deployment_class": "local", "orchestration_role": "drone"},
            "enabled": True,
        }
    )
    registry.register_manifest(
        {
            "provider_name": "vllm-local",
            "model_name": "qwen2.5:32b-vllm",
            "source_type": "http",
            "adapter_type": "openai_compatible",
            "license_name": "User-managed",
            "license_reference": "user-managed",
            "weight_location": "external",
            "weights_bundled": False,
            "redistribution_allowed": False,
            "runtime_dependency": "vllm",
            "capabilities": ["summarize", "classify", "format", "long_context", "code_complex", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:8100/v1", "context_window": 65536},
            "metadata": {"deployment_class": "local", "orchestration_role": "queen", "context_window": 65536},
            "enabled": True,
        }
    )

    plan = resolve_provider_routing_plan(
        registry,
        task_kind="action_plan",
        output_mode="action_plan",
        role="queen",
    )

    assert plan.selected is not None
    assert plan.selected.provider_name == "vllm-local"
    assert plan.candidate_provider_ids[0] == "vllm-local:qwen2.5:32b-vllm"


def test_drone_role_can_use_local_llamacpp_lane_when_qwen_is_absent() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    registry.register_manifest(
        {
            "provider_name": "llamacpp-local",
            "model_name": "qwen2.5:14b-gguf",
            "source_type": "http",
            "adapter_type": "openai_compatible",
            "license_name": "User-managed",
            "license_reference": "user-managed",
            "weight_location": "external",
            "weights_bundled": False,
            "redistribution_allowed": False,
            "runtime_dependency": "llama.cpp",
            "capabilities": ["summarize", "classify", "format", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:8090/v1", "context_window": 16384},
            "metadata": {"deployment_class": "local", "orchestration_role": "drone", "context_window": 16384},
            "enabled": True,
        }
    )
    registry.register_manifest(
        {
            "provider_name": "remote-generic",
            "model_name": "helper",
            "source_type": "http",
            "adapter_type": "openai_compatible",
            "license_name": "Provider",
            "license_reference": "user-managed",
            "weight_location": "external",
            "weights_bundled": False,
            "redistribution_allowed": False,
            "runtime_dependency": "remote-openai-compatible-provider",
            "capabilities": ["summarize", "classify", "format"],
            "runtime_config": {"base_url": "https://remote.example"},
            "metadata": {"deployment_class": "cloud"},
            "enabled": True,
        }
    )

    ranked = rank_provider_candidates(
        registry,
        task_kind="summarization",
        output_mode="summary_block",
        role="drone",
        swarm_size=2,
    )

    assert ranked
    assert ranked[0].provider_name == "llamacpp-local"
    assert ranked[0].provider_id == "llamacpp-local:qwen2.5:14b-gguf"


def test_provider_routing_skips_circuit_open_candidates() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    _register_default_manifests(registry)
    record_provider_failure(
        "kimi-remote:kimi-k2",
        error="timeout",
        timeout=True,
        failure_threshold=1,
        cooldown_seconds=60,
    )

    plan = resolve_provider_routing_plan(
        registry,
        task_kind="action_plan",
        output_mode="action_plan",
        role="queen",
        swarm_size=2,
    )

    assert plan.selected is not None
    assert plan.selected.provider_name != "kimi-remote"
    assert any(item["reason"] == "provider_circuit_open" for item in plan.rejected_candidates)
    assert any("circuit-open" in note for note in plan.selection_notes)


def test_provider_capability_truth_marks_recent_failures_as_degraded() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    local_manifest, _, _ = _register_default_manifests(registry)
    record_provider_failure(
        local_manifest.provider_id,
        error="health_check_failed",
        failure_threshold=5,
        cooldown_seconds=60,
    )

    capability = provider_capability_truth_for_manifest(local_manifest)

    assert capability.availability_state == "degraded"
    assert capability.circuit_open is False
    assert capability.last_error == "health_check_failed"
