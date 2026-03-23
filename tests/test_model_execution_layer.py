from __future__ import annotations

import json
import unittest
import uuid
from typing import Any
from unittest import mock

from adapters.base_adapter import ModelResponse
from apps.nulla_agent import NullaAgent
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.memory_first_router import MemoryFirstRouter, ModelExecutionDecision
from core.model_registry import ModelRegistry
from core.reasoning_engine import build_plan
from core.task_router import classify, create_task_record
from core.tiered_context_loader import TieredContextLoader
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


class ModelExecutionLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in ("model_provider_manifests", "candidate_knowledge_lane", "learning_shards", "local_tasks"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()
        self.registry = ModelRegistry()
        self.router = MemoryFirstRouter(self.registry)
        self.loader = TieredContextLoader()
        self.persona = load_active_persona("default")

    def _insert_local_shard(self) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, created_at, updated_at
                ) VALUES (?, 1, 'security_hardening', ?, ?, ?, ?, 'local_generated', ?, 0.95, 0.88, 0, 0, 'active', '[]', CURRENT_TIMESTAMP, NULL, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    f"shard-{uuid.uuid4().hex}",
                    f"sig-{uuid.uuid4().hex}",
                    "Harden local credentials and prevent password leaks from your setup.",
                    json.dumps(["identify_sensitive_surfaces", "remove_secret_exposure_paths"]),
                    json.dumps({"os": "unknown", "runtime": "python", "shell": "unknown", "version_family": "unknown"}),
                    get_local_peer_id(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _chat_turn_inputs(self, text: str) -> tuple[Any, HumanInputInterpretation, dict[str, Any], Any]:
        task = create_task_record(text)
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="request",
            topic_hints=["chat"],
            reference_targets=[],
            understanding_confidence=0.82,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        context_result = self.loader.load(
            task=task,
            classification=classification,
            interpretation=interpretation,
            persona=self.persona,
            session_id=f"ctx-{uuid.uuid4().hex}",
        )
        return task, interpretation, classification, context_result

    def test_memory_first_hit_skips_model_call(self) -> None:
        self._insert_local_shard()
        task = create_task_record("harden local credentials so passwords never leak")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="request",
            topic_hints=["security hardening", "password leak"],
            reference_targets=[],
            understanding_confidence=0.82,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        context_result = self.loader.load(
            task=task,
            classification=classification,
            interpretation=interpretation,
            persona=self.persona,
            session_id=f"ctx-{uuid.uuid4().hex}",
        )
        with mock.patch.object(self.registry, "build_adapter") as build_adapter:
            decision = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
            )
        self.assertEqual(decision.source, "memory_hit")
        build_adapter.assert_not_called()

    def test_chat_surface_forces_provider_over_exact_cache_hit(self) -> None:
        task, interpretation, classification, context_result = self._chat_turn_inputs("do you think boredom is useful?")
        with mock.patch(
            "core.memory_first_router.get_exact_candidate",
            return_value={
                "provider_name": "cached",
                "model_name": "cached-model",
                "normalized_output": "Cached answer that should not speak directly.",
                "structured_output": None,
                "confidence": 0.88,
                "trust_score": 0.88,
                "candidate_id": "cached-1",
                "validation_state": "valid",
            },
        ), mock.patch("core.memory_first_router.should_revalidate", return_value=False), mock.patch.object(
            self.router,
            "_execute_provider_task",
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="chat-cache-forced",
                provider_id="test-provider",
                used_model=True,
                output_text="Fresh provider answer.",
            ),
        ) as execute_provider:
            decision = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
                force_model=False,
                surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertEqual(decision.source, "provider_execution")
        execute_provider.assert_called_once()

    def test_chat_surface_forces_provider_over_memory_hit(self) -> None:
        self._insert_local_shard()
        task, interpretation, classification, context_result = self._chat_turn_inputs("harden local credentials so passwords never leak")
        with mock.patch.object(
            self.router,
            "_execute_provider_task",
            return_value=ModelExecutionDecision(
                source="no_provider_available",
                task_hash="chat-memory-forced",
                used_model=False,
            ),
        ) as execute_provider:
            decision = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
                force_model=False,
                surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertEqual(decision.source, "no_provider_available")
        execute_provider.assert_called_once()

    def test_provider_registration_and_routing_with_trust(self) -> None:
        self.registry.register_manifest(
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
            }
        )
        manifest = self.registry.select_manifest(
            request=mock.Mock(
                task_kind="action_plan",
                output_mode="action_plan",
                preferred_provider=None,
                preferred_model=None,
                preferred_source_types=[],
                require_license_metadata=True,
                forbid_bundled_weights=True,
                allow_paid_fallback=True,
                exclude_provider_ids=[],
                min_trust=0.0,
            )
        )
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.provider_name, "local-qwen-http")

    def test_existing_local_agent_flow_still_returns_model_execution_metadata(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="local-model-test", persona_id="default")
        agent.start()
        with mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
            "apps.nulla_agent.request_relevant_holders", return_value=[]
        ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
            result = agent.run_once("check current local setup status")
        self.assertIn("model_execution", result)
        self.assertIn("source", result["model_execution"])

    def test_valid_model_candidates_can_become_durable_plans(self) -> None:
        task = create_task_record("design persistent openclaw continuity")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="request",
            topic_hints=["openclaw", "memory"],
            reference_targets=[],
            understanding_confidence=0.84,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        plan = build_plan(
            task,
            classification,
            evidence={
                "model_candidates": [
                    {
                        "summary": "Persist rolling session summaries and retrieve them by relevance.",
                        "resolution_pattern": ["persist_session_summary", "retrieve_relevant_memory"],
                        "score": 0.81,
                        "validation_state": "valid",
                        "provider_name": "local-qwen-http",
                    }
                ]
            },
            persona=self.persona,
        )
        self.assertGreaterEqual(plan.confidence, 0.8)

    def test_summary_block_uses_structured_model_path(self) -> None:
        self.registry.register_manifest(
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
            }
        )
        task = create_task_record("search latest qwen release notes")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="question",
            topic_hints=["research", "news"],
            reference_targets=[],
            understanding_confidence=0.86,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        context_result = self.loader.load(
            task=task,
            classification=classification,
            interpretation=interpretation,
            persona=self.persona,
            session_id=f"ctx-{uuid.uuid4().hex}",
        )
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.estimate_cost_class.return_value = "free_local"
        adapter.get_license_metadata.return_value = {}
        adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"summary":"Grounded summary","bullets":["Check official release notes"]}',
            confidence=0.74,
        )

        with mock.patch.object(self.registry, "build_adapter", return_value=adapter):
            decision = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
                force_model=True,
                surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        adapter.run_structured_task.assert_called_once()
        adapter.run_text_task.assert_not_called()
        self.assertEqual(decision.validation_state, "valid")
        self.assertEqual(decision.structured_output["summary"], "Grounded summary")

    def test_role_aware_summary_execution_prefers_queen_lane(self) -> None:
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
        queen_manifest = self.registry.register_manifest(
            {
                "provider_name": "kimi-cloud-http",
                "model_name": "kimi-latest",
                "source_type": "http",
                "adapter_type": "openai_compatible",
                "license_name": "Provider",
                "license_reference": "user-managed",
                "weight_location": "external",
                "weights_bundled": False,
                "redistribution_allowed": False,
                "runtime_dependency": "remote-openai-compatible-provider",
                "capabilities": ["summarize", "structured_json", "long_context"],
                "runtime_config": {"base_url": "https://kimi.example", "api_key_env": "KIMI_API_KEY"},
                "enabled": True,
                "metadata": {"orchestration_role": "queen"},
            }
        )
        task, interpretation, classification, context_result = self._chat_turn_inputs("research the latest qwen release notes")
        local_adapter = mock.Mock()
        local_adapter.health_check.return_value = {"ok": True}
        local_adapter.estimate_cost_class.return_value = "free_local"
        local_adapter.get_license_metadata.return_value = {}
        local_adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"summary":"Local answer","bullets":["Use local lane"]}',
            confidence=0.61,
        )
        queen_adapter = mock.Mock()
        queen_adapter.health_check.return_value = {"ok": True}
        queen_adapter.estimate_cost_class.return_value = "paid_cloud"
        queen_adapter.get_license_metadata.return_value = {}
        queen_adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"summary":"Queen answer","bullets":["Use stronger synthesis"]}',
            confidence=0.84,
        )

        def _build_adapter(manifest):
            if manifest.provider_id == queen_manifest.provider_id:
                return queen_adapter
            if manifest.provider_id == local_manifest.provider_id:
                return local_adapter
            raise AssertionError(f"unexpected provider {manifest.provider_id}")

        with mock.patch(
            "core.memory_first_router.model_execution_profile",
            return_value={
                "task_kind": "summarization",
                "output_mode": "summary_block",
                "allow_paid_fallback": True,
                "provider_role": "queen",
            },
        ), mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[queen_manifest, local_manifest],
        ) as rank_candidates, mock.patch.object(self.registry, "build_adapter", side_effect=_build_adapter):
            decision = self.router.resolve(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
                force_model=True,
                surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertEqual(rank_candidates.call_args.kwargs["role"], "queen")
        self.assertEqual(decision.provider_name, queen_manifest.provider_name)
        self.assertEqual(decision.details["provider_role"], "queen")
        self.assertEqual(decision.details["ranked_candidates"][0], queen_manifest.provider_id)

    def test_ungrounded_live_lookup_summary_is_downgraded(self) -> None:
        task = create_task_record("check hive mind tasks")
        classification = {"task_class": "research", "confidence_hint": 0.72}
        plan = build_plan(
            task,
            classification,
            evidence={
                "model_candidates": [
                    {
                        "summary": "I checked online and found some real AI hive tasks.",
                        "score": 0.83,
                        "validation_state": "valid",
                        "provider_name": "local-qwen-http",
                    }
                ],
                "web_notes": [],
            },
            persona=self.persona,
        )
        self.assertIn("No verified live lookup result", plan.summary)
        self.assertIn("ungrounded_live_claim", plan.risk_flags)
        self.assertLessEqual(plan.confidence, 0.38)

    def test_tool_intent_uses_structured_model_path(self) -> None:
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
                "capabilities": ["summarize", "structured_json", "tool_intent"],
                "runtime_config": {"base_url": "http://127.0.0.1:1234"},
                "enabled": True,
            }
        )
        task = create_task_record("latest qwen release notes")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="question",
            topic_hints=["research", "news"],
            reference_targets=[],
            understanding_confidence=0.87,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        context_result = self.loader.load(
            task=task,
            classification=classification,
            interpretation=interpretation,
            persona=self.persona,
            session_id=f"ctx-{uuid.uuid4().hex}",
        )
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.estimate_cost_class.return_value = "free_local"
        adapter.get_license_metadata.return_value = {}
        adapter.run_structured_task.return_value = ModelResponse(
            output_text='{"intent":"web.search","arguments":{"query":"latest qwen release notes","limit":2}}',
            confidence=0.78,
        )

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[local_manifest],
        ) as rank_candidates, mock.patch.object(self.registry, "build_adapter", return_value=adapter):
            decision = self.router.resolve_tool_intent(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=self.persona,
                surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        adapter.run_structured_task.assert_called_once()
        adapter.run_text_task.assert_not_called()
        self.assertEqual(rank_candidates.call_args.kwargs["role"], "drone")
        self.assertEqual(decision.validation_state, "valid")
        self.assertEqual(decision.structured_output["intent"], "web.search")
        self.assertEqual(decision.details["provider_role"], "drone")


if __name__ == "__main__":
    unittest.main()
