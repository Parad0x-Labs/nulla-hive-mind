from __future__ import annotations

from core.model_registry import ModelRegistry
from core.provider_routing import rank_provider_candidates, resolve_provider_routing_plan
from storage.db import get_connection
from storage.migrations import run_migrations


def _clear_manifests() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()


def _register_default_manifests(registry: ModelRegistry) -> None:
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


def test_drone_role_prefers_local_qwen_lane() -> None:
    run_migrations()
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
