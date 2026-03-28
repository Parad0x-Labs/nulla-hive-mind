from __future__ import annotations

import time
import unittest
from unittest import mock

from core.compute_mode import ComputeBudget
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.memory_first_router import MemoryFirstRouter
from core.model_health import circuit_is_open, get_provider_health, record_provider_failure, reset_provider_health
from core.model_registry import ModelRegistry
from core.task_router import classify, create_task_record
from core.tiered_context_loader import TieredContextResult
from storage.db import get_connection
from storage.migrations import run_migrations


class ProviderFailoverTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        reset_provider_health()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM model_provider_manifests")
            conn.execute("DELETE FROM candidate_knowledge_lane")
            conn.commit()
        finally:
            conn.close()
        self.registry = ModelRegistry()
        self.router = MemoryFirstRouter(self.registry)
        self.persona = load_active_persona("default")
        self.interpretation = HumanInputInterpretation(
            raw_text="design swarm topology",
            normalized_text="design swarm topology",
            reconstructed_text="design swarm topology",
            intent_mode="request",
            topic_hints=["swarm"],
            reference_targets=[],
            understanding_confidence=0.72,
            quality_flags=[],
        )

    def _register_providers(self):
        local_manifest = self.registry.register_manifest(
            {
                "provider_name": "local-qwen-http",
                "model_name": "qwen-local",
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["summarize", "structured_json"],
                "runtime_config": {"base_url": "http://127.0.0.1:1234"},
                "enabled": True,
                "metadata": {"orchestration_role": "drone"},
            }
        )
        cloud_manifest = self.registry.register_manifest(
            {
                "provider_name": "cloud-fallback-http",
                "model_name": "cloud",
                "source_type": "http",
                "adapter_type": "cloud_fallback_provider",
                "license_name": "Provider",
                "license_reference": "user-managed",
                "weight_location": "external",
                "weights_bundled": False,
                "redistribution_allowed": False,
                "runtime_dependency": "remote-openai-compatible-provider",
                "capabilities": ["summarize", "structured_json", "long_context"],
                "runtime_config": {"base_url": "https://provider.example", "api_key_env": "NULLA_CLOUD_API_KEY"},
                "enabled": True,
                "metadata": {"orchestration_role": "queen"},
            }
        )
        return local_manifest, cloud_manifest

    def test_local_provider_failure_triggers_safe_failover(self) -> None:
        local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())
        context_result = TieredContextResult(
            bootstrap_items=[],
            relevant_items=[],
            cold_items=[],
            local_candidates=[],
            swarm_metadata=[],
            report=mock.Mock(retrieval_confidence="low", swarm_metadata_consulted=False, cold_archive_opened=False),
            retrieval_confidence_score=0.15,
            cold_decision=mock.Mock(allow=False),
        )
        context_result.report.to_dict.return_value = {}
        context_result.report.total_tokens_used.return_value = 0
        context_result.assembled_context = lambda: "No strong local memory."

        local_adapter = mock.Mock()
        local_adapter.health_check.return_value = {"ok": True}
        local_adapter.run_structured_task.side_effect = RuntimeError("timeout")

        cloud_adapter = mock.Mock()
        cloud_adapter.health_check.return_value = {"ok": True}
        cloud_adapter.run_structured_task.return_value = mock.Mock(output_text='{"summary":"Use regional meet nodes","steps":["pick regions","sync summaries"]}', confidence=0.81)
        cloud_adapter.get_license_metadata.return_value = {"license_name": "Provider", "license_reference": "user-managed"}
        cloud_adapter.estimate_cost_class.return_value = "paid_cloud"

        def build_adapter(manifest):
            if manifest.provider_name == "local-qwen-http":
                return local_adapter
            return cloud_adapter

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[local_manifest, cloud_manifest],
        ) as rank_candidates, mock.patch.object(self.registry, "build_adapter", side_effect=build_adapter):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=context_result,
                persona=self.persona,
            )

        self.assertTrue(result.used_model)
        self.assertTrue(result.failover_used)
        self.assertEqual(result.provider_name, "cloud-fallback-http")
        self.assertEqual(rank_candidates.call_args.kwargs["role"], "queen")
        self.assertEqual(get_provider_health("local-qwen-http:qwen-local").consecutive_failures, 1)
        self.assertEqual(result.details["provider_role"], "queen")

    def test_requested_model_in_source_context_steers_provider_preferences(self) -> None:
        local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())
        context_result = TieredContextResult(
            bootstrap_items=[],
            relevant_items=[],
            cold_items=[],
            local_candidates=[],
            swarm_metadata=[],
            report=mock.Mock(retrieval_confidence="low", swarm_metadata_consulted=False, cold_archive_opened=False),
            retrieval_confidence_score=0.15,
            cold_decision=mock.Mock(allow=False),
        )
        context_result.report.to_dict.return_value = {}
        context_result.report.total_tokens_used.return_value = 0
        context_result.assembled_context = lambda: "No strong local memory."

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[cloud_manifest, local_manifest],
        ) as rank_candidates, mock.patch.object(
            self.router,
            "_invoke_manifest",
            return_value=(None, None, "forced-stop"),
        ):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=context_result,
                persona=self.persona,
                source_context={"surface": "api", "requested_model": "cloud-fallback-http:cloud"},
            )

        self.assertEqual(result.source, "no_provider_available")
        self.assertEqual(rank_candidates.call_args.kwargs["preferred_provider"], "cloud-fallback-http")
        self.assertEqual(rank_candidates.call_args.kwargs["preferred_model"], "cloud")

    def test_circuit_breaker_trips_and_recovers_after_cooldown(self) -> None:
        state = record_provider_failure(
            "local-qwen-http:qwen-local",
            error="timeout",
            timeout=True,
            failure_threshold=1,
            cooldown_seconds=10,
        )
        self.assertTrue(circuit_is_open("local-qwen-http:qwen-local"))
        with mock.patch("core.model_health.time.time", return_value=state.circuit_open_until + 1):
            self.assertFalse(circuit_is_open("local-qwen-http:qwen-local"))

    def test_local_remote_race_returns_first_successful_winner(self) -> None:
        local_manifest, cloud_manifest = self._register_providers()
        task = create_task_record("design swarm topology with resilient regions")
        classification = classify(task.task_summary, context=self.interpretation.as_context())
        context_result = TieredContextResult(
            bootstrap_items=[],
            relevant_items=[],
            cold_items=[],
            local_candidates=[],
            swarm_metadata=[],
            report=mock.Mock(retrieval_confidence="low", swarm_metadata_consulted=False, cold_archive_opened=False),
            retrieval_confidence_score=0.15,
            cold_decision=mock.Mock(allow=False),
        )
        context_result.report.to_dict.return_value = {}
        context_result.report.total_tokens_used.return_value = 0
        context_result.assembled_context = lambda: "No strong local memory."

        local_adapter = mock.Mock()
        local_adapter.health_check.return_value = {"ok": True}
        local_adapter.run_structured_task.side_effect = lambda *_args, **_kwargs: (time.sleep(0.2), mock.Mock(output_text='{"summary":"local"}', confidence=0.7))[1]

        cloud_adapter = mock.Mock()
        cloud_adapter.health_check.return_value = {"ok": True}
        cloud_adapter.run_structured_task.return_value = mock.Mock(output_text='{"summary":"remote"}', confidence=0.83)
        cloud_adapter.get_license_metadata.return_value = {"license_name": "Provider", "license_reference": "user-managed"}
        cloud_adapter.estimate_cost_class.return_value = "paid_cloud"

        def build_adapter(manifest):
            if manifest.provider_name == "local-qwen-http":
                return local_adapter
            return cloud_adapter

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[local_manifest, cloud_manifest],
        ), mock.patch("core.memory_first_router.get_active_compute_budget", return_value=ComputeBudget(mode="max_push", cpu_threads=8, gpu_memory_fraction=0.9, worker_pool_cap=4, reason="test")), mock.patch.object(
            self.registry,
            "build_adapter",
            side_effect=build_adapter,
        ):
            result = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=self.interpretation,
                context_result=context_result,
                persona=self.persona,
            )

        self.assertEqual(result.source, "provider_race_winner")
        self.assertEqual(result.provider_name, "cloud-fallback-http")
        self.assertTrue(result.failover_used)


if __name__ == "__main__":
    unittest.main()
