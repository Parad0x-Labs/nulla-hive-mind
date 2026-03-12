from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from apps.nulla_agent import NullaAgent
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.memory_first_router import ModelExecutionDecision
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_checkpoint,
    latest_resumable_checkpoint,
    list_runtime_session_events,
    mark_stale_runtime_checkpoints_interrupted,
    reset_runtime_continuity_state,
)
from core.tool_intent_executor import ToolIntentExecution, execute_tool_intent
from storage.migrations import run_migrations


class RuntimeContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "runtime-continuity.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_stale_running_checkpoint_is_marked_interrupted(self) -> None:
        checkpoint = create_runtime_checkpoint(
            session_id="openclaw:resume-test",
            request_text="inspect the repo and keep going",
            source_context={"runtime_session_id": "openclaw:resume-test"},
        )

        changed = mark_stale_runtime_checkpoints_interrupted()

        self.assertEqual(changed, 1)
        resumable = latest_resumable_checkpoint("openclaw:resume-test")
        self.assertIsNotNone(resumable)
        assert resumable is not None
        self.assertEqual(resumable["checkpoint_id"], checkpoint["checkpoint_id"])
        self.assertEqual(resumable["status"], "interrupted")
        events = list_runtime_session_events("openclaw:resume-test", after_seq=0, limit=10)
        self.assertTrue(any(event["event_type"] == "task_interrupted" for event in events))

    def test_mutating_tool_receipt_reuses_prior_execution(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        bridge = mock.Mock()
        bridge.submit_public_topic_result.return_value = {
            "ok": True,
            "status": "result_submitted",
            "topic_id": "topic-1234567890abcdef",
            "post_id": "post-123",
        }
        payload = {
            "intent": "hive.submit_result",
            "arguments": {
                "topic_id": "topic-1234567890abcdef",
                "body": "Done. Resume-safe receipts are wired.",
                "result_status": "solved",
                "claim_id": "claim-123",
            },
        }

        first = execute_tool_intent(
            payload,
            task_id="task-123",
            session_id="openclaw:receipt",
            source_context={"surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
            public_hive_bridge=bridge,
            checkpoint_id="runtime-checkpoint-1",
            step_index=0,
        )
        second = execute_tool_intent(
            payload,
            task_id="task-123",
            session_id="openclaw:receipt",
            source_context={"surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
            public_hive_bridge=bridge,
            checkpoint_id="runtime-checkpoint-1",
            step_index=0,
        )

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(bridge.submit_public_topic_result.call_count, 1)
        self.assertTrue(second.details.get("from_receipt"))

    def test_agent_continue_resumes_pending_tool_step(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="final-synthesis",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Grounded final answer after resume.",
                confidence=0.82,
                trust_score=0.84,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            side_effect=[
                ModelExecutionDecision(
                    source="provider_execution",
                    task_hash="tool-intent-search",
                    provider_id="ollama-local:test",
                    provider_name="ollama-local",
                    model_name="test",
                    structured_output={"intent": "workspace.search_text", "arguments": {"query": "tool_intent"}},
                    confidence=0.8,
                    trust_score=0.84,
                    used_model=True,
                    validation_state="valid",
                ),
                ModelExecutionDecision(
                    source="provider_execution",
                    task_hash="tool-intent-direct",
                    provider_id="ollama-local:test",
                    provider_name="ollama-local",
                    model_name="test",
                    structured_output={
                        "intent": "respond.direct",
                        "arguments": {"message": "Grounded final answer after resume."},
                    },
                    confidence=0.79,
                    trust_score=0.83,
                    used_model=True,
                    validation_state="valid",
                ),
            ]
        )

        with mock.patch("apps.nulla_agent.execute_tool_intent", side_effect=RuntimeError("tool crashed mid-step")), mock.patch(
            "apps.nulla_agent.orchestrate_parent_task",
            return_value=None,
        ):
            with self.assertRaises(RuntimeError):
                agent.run_once(
                    "find tool intent wiring",
                    session_id_override="openclaw:resume-agent",
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )

        resumable = latest_resumable_checkpoint("openclaw:resume-agent")
        self.assertIsNotNone(resumable)
        assert resumable is not None
        self.assertEqual(resumable["status"], "interrupted")

        with mock.patch(
            "apps.nulla_agent.execute_tool_intent",
            return_value=ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text='Search matches for "tool_intent":\n- core/tool_intent_executor.py:42 def execute_tool_intent(',
                mode="tool_executed",
                tool_name="workspace.search_text",
                details={"query": "tool_intent"},
            ),
        ) as execute_tool_intent, mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None):
            result = agent.run_once(
                "continue",
                session_id_override="openclaw:resume-agent",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertEqual(result["mode"], "tool_executed")
        self.assertIn("Grounded final answer after resume.", result["response"])
        self.assertEqual(execute_tool_intent.call_count, 1)
        resumed = latest_resumable_checkpoint("openclaw:resume-agent")
        self.assertIsNone(resumed)


if __name__ == "__main__":
    unittest.main()
