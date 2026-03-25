from __future__ import annotations

import unittest
from unittest import mock

from core.learning.procedure_shards import ProcedureShardV1
from core.model_registry import ModelRegistry
from core.model_teacher_pipeline import ModelTeacherPipeline
from core.orchestration import (
    TaskGraph,
    build_task_envelope,
    get_role_contract,
    merge_task_results,
    request_cancellation,
    resume_task,
    schedule_task_envelopes,
)
from core.provider_routing import resolve_provider_routing_plan_for_envelope
from core.task_router import build_task_envelope_for_request


class OrchestrationPhase1Tests(unittest.TestCase):
    def test_build_task_envelope_uses_role_defaults(self) -> None:
        envelope = build_task_envelope(role="coder", goal="Patch the failing test")

        self.assertEqual(envelope.role, "coder")
        self.assertIn("workspace.write", envelope.tool_permissions)
        self.assertIn("workspace_write", envelope.allowed_side_effects)

    def test_role_contracts_keep_verifier_read_only(self) -> None:
        contract = get_role_contract("verifier")

        self.assertFalse(contract.can_mutate_workspace)
        self.assertIn("workspace.validate", contract.default_tool_permissions)
        self.assertEqual(contract.provider_role, "drone")

    def test_merge_task_results_is_deterministic(self) -> None:
        envelope = build_task_envelope(role="queen", goal="Merge worker results", merge_strategy="highest_score")
        merged = merge_task_results(
            envelope,
            [
                {"task_id": "b", "ok": True, "score": 0.8},
                {"task_id": "a", "ok": True, "score": 0.8},
                {"task_id": "c", "ok": True, "score": 0.9},
            ],
        )

        self.assertEqual(merged["winner"]["task_id"], "c")
        self.assertEqual(merged["strategy"], "highest_score")

    def test_cancel_and_resume_flow_updates_children(self) -> None:
        graph = TaskGraph()
        parent = build_task_envelope(role="queen", goal="Coordinate", task_id="parent")
        child = build_task_envelope(role="coder", goal="Patch", task_id="child", parent_task_id="parent")
        graph.add_task(parent)
        graph.add_task(child)
        graph.mark_status("parent", "running")
        graph.mark_status("child", "running")

        request_cancellation(graph, "parent", reason="user requested")
        self.assertEqual(graph.get("parent").status, "cancel_requested")
        self.assertEqual(graph.get("child").status, "cancel_requested")

        resume_task(graph, "parent")
        self.assertEqual(graph.get("parent").status, "pending")
        self.assertEqual(graph.get("child").status, "pending")

    def test_scheduler_prioritizes_low_latency_tasks(self) -> None:
        fast = build_task_envelope(role="verifier", goal="Verify", task_id="fast", latency_budget="low_latency")
        slow = build_task_envelope(role="coder", goal="Implement", task_id="slow", latency_budget="deep")
        scheduled = schedule_task_envelopes([slow, fast])

        self.assertEqual(scheduled[0].task_id, "fast")

    def test_build_task_envelope_for_request_attaches_reusable_procedure_citations(self) -> None:
        shard = ProcedureShardV1.create(
            task_class="debugging",
            title="Patch Python code and run tests",
            preconditions=["workspace is writable"],
            steps=["apply diff", "run tests"],
            tool_receipts=[],
            validation={"ok": True},
            rollback={},
            privacy_class="local_private",
            shareability="local_only",
            success_signal="verified_success",
        )
        with mock.patch("core.task_router.load_procedure_shards", return_value=[shard]):
            envelope = build_task_envelope_for_request(
                "fix this traceback in my python repo",
                task_id="task-1",
            )

        self.assertEqual(envelope.role, "coder")
        self.assertEqual(envelope.inputs["task_class"], "debugging")
        self.assertIn(shard.procedure_id, envelope.inputs["reused_procedure_ids"])

    def test_provider_routing_plan_for_envelope_exposes_capability_truth(self) -> None:
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
                "capabilities": ["summarize", "structured_json"],
                "runtime_config": {"base_url": "http://127.0.0.1:11434"},
                "metadata": {"deployment_class": "local", "orchestration_role": "drone", "max_safe_concurrency": 2},
                "enabled": True,
            }
        )
        envelope = build_task_envelope(role="coder", goal="Patch tests", task_id="coder-1")

        plan = resolve_provider_routing_plan_for_envelope(
            registry,
            envelope=envelope,
            task_kind="action_plan",
            output_mode="action_plan",
        )

        self.assertEqual(plan.role, "drone")
        self.assertEqual(plan.task_envelope["task_id"], "coder-1")
        self.assertEqual(plan.capability_truth[0].role_fit, "drone")
        self.assertEqual(plan.capability_truth[0].max_safe_concurrency, 2)

    def test_teacher_pipeline_carries_task_envelope_into_provenance(self) -> None:
        registry = ModelRegistry()
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
                "capabilities": ["summarize", "structured_json", "long_context"],
                "runtime_config": {"base_url": "https://kimi.example"},
                "metadata": {"deployment_class": "cloud", "orchestration_role": "queen"},
                "enabled": True,
            }
        )
        pipeline = ModelTeacherPipeline(registry)
        envelope = build_task_envelope(role="queen", goal="Merge worker outputs", task_id="queen-1")
        fake_response = mock.Mock(output_text="Merged answer", confidence=0.88)

        with mock.patch.object(registry, "build_adapter") as build_adapter:
            build_adapter.return_value.invoke.return_value = fake_response
            build_adapter.return_value.get_license_metadata.return_value = {
                "provider_name": "kimi-remote",
                "model_name": "kimi-k2",
                "license_name": "Provider",
                "license_reference": "user-managed",
            }
            candidate = pipeline.run(
                task_kind="action_plan",
                prompt="merge worker outputs",
                output_mode="action_plan",
                task_envelope=envelope,
            )

        assert candidate is not None
        self.assertEqual(candidate.provider_role, "queen")
        self.assertEqual(candidate.provenance["task_envelope"]["task_id"], "queen-1")


if __name__ == "__main__":
    unittest.main()
