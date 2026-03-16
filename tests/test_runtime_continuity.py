from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.nulla_agent import NullaAgent
from core.curiosity_roamer import CuriosityResult
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.media_analysis_pipeline import MediaAnalysisResult
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

    def test_immediate_tool_loop_history_uses_structured_observation_message(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        tool_decision = ModelExecutionDecision(
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
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            side_effect=[tool_decision, tool_decision]
        )
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="final-synthesis",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Grounded final answer after structured tool observation.",
                confidence=0.82,
                trust_score=0.84,
                used_model=True,
                validation_state="valid",
            )
        )

        with mock.patch(
            "apps.nulla_agent.execute_tool_intent",
            return_value=ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text='Search matches for "tool_intent":\n- core/tool_intent_executor.py:42 def execute_tool_intent(',
                mode="tool_executed",
                tool_name="workspace.search_text",
                details={
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "workspace.search_text",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "query": "tool_intent",
                        "matches": [
                            {
                                "path": "core/tool_intent_executor.py",
                                "line": 42,
                                "snippet": "def execute_tool_intent(",
                            }
                        ],
                    }
                },
            ),
        ), mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None):
            result = agent.run_once(
                "find tool intent wiring",
                session_id_override="openclaw:structured-tool-loop",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertEqual(result["mode"], "tool_executed")
        self.assertIn("Grounded final answer after structured tool observation.", result["response"])

        self.assertEqual(agent.memory_router.resolve_tool_intent.call_count, 0)
        second_tool_source_context = agent.memory_router.resolve.call_args.kwargs["source_context"]
        second_tool_history = list(second_tool_source_context.get("conversation_history") or [])
        self.assertTrue(second_tool_history)
        self.assertEqual(second_tool_history[-1]["role"], "user")
        self.assertIn("Grounding observations for this turn", second_tool_history[-1]["content"])
        self.assertIn('"tool_surface": "workspace"', second_tool_history[-1]["content"])
        self.assertIn('"query": "tool_intent"', second_tool_history[-1]["content"])
        self.assertNotIn("Real tool result from", second_tool_history[-1]["content"])

    def test_merge_runtime_source_contexts_upgrades_legacy_tool_prose_to_observation_message(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        merged = agent._merge_runtime_source_contexts(
            {
                "conversation_history": [
                    {
                        "role": "assistant",
                        "content": (
                            "Real tool result from `workspace.search_text`:\n"
                            'Search matches for "tool_intent":\n'
                            "- core/tool_intent_executor.py:42 def execute_tool_intent("
                        ),
                    }
                ]
            },
            {"surface": "openclaw", "platform": "openclaw"},
        )

        history = list(merged.get("conversation_history") or [])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["role"], "user")
        self.assertIn("Grounding observations for this turn", history[0]["content"])
        self.assertIn('"tool_surface": "workspace"', history[0]["content"])
        self.assertNotIn("Real tool result from", history[0]["content"])

    @pytest.mark.xfail(reason="Pre-existing: workflow planner output format changed")
    def test_workflow_planner_can_chain_real_tools_without_reasking_model_each_step(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("workflow planner should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="planner-final",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="I ran the check, fixed the marker in app.py, and the retry passed.",
                confidence=0.88,
                trust_score=0.89,
                used_model=True,
                validation_state="valid",
            )
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text(
                "from pathlib import Path\n"
                "text = Path(__file__).read_text(encoding='utf-8')\n"
                "if 'TODO' in text:\n"
                "    print('app.py:1 TODO marker still present')\n"
                "    raise SystemExit(1)\n"
                "print('clean')\n",
                encoding="utf-8",
            )
            with mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None):
                result = agent.run_once(
                    "run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
                    session_id_override="openclaw:workflow-planner",
                    source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
                )

            self.assertEqual(result["mode"], "tool_executed")
            self.assertIn("retry passed", result["response"].lower())
            self.assertEqual(agent.memory_router.resolve_tool_intent.call_count, 0)
            self.assertIn("DONE", (workspace / "app.py").read_text(encoding="utf-8"))
            final_history = list(agent.memory_router.resolve.call_args.kwargs["source_context"].get("conversation_history") or [])
            joined_history = "\n".join(str(item.get("content") or "") for item in final_history)
            self.assertIn('"intent": "workspace.replace_in_file"', joined_history)
            self.assertIn('"intent": "sandbox.run_command"', joined_history)

    def test_builder_controller_runs_bounded_scaffold_loop_without_reasking_model(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="builder-final",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="I finished a bounded Telegram build loop and the compile check passed.",
                confidence=0.86,
                trust_score=0.87,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
            return_value=MediaAnalysisResult(False, reason="no_external_media")
        )
        agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]
        agent._should_run_builder_controller = mock.Mock(return_value=True)  # type: ignore[assignment]
        agent._should_run_builder_controller = mock.Mock(return_value=True)  # type: ignore[assignment]

        def _execute_builder_step(
            payload,
            *,
            task_id,
            session_id,
            source_context,
            hive_activity_tracker,
            public_hive_bridge=None,
            checkpoint_id=None,
            step_index=0,
        ):
            tool_name = str(payload.get("intent") or "")
            arguments = dict(payload.get("arguments") or {})
            if tool_name == "workspace.write_file":
                path = str(arguments["path"])
                content = str(arguments.get("content") or "")
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text=f"Created file `{path}` with {len(content.splitlines())} lines.",
                    mode="tool_executed",
                    tool_name="workspace.write_file",
                    details={
                        "artifacts": [
                            {
                                "artifact_type": "file_diff",
                                "path": path,
                                "action": "created",
                                "line_count": len(content.splitlines()),
                                "diff_preview": f"--- a/{path}\n+++ b/{path}\n@@\n+created",
                            }
                        ],
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "workspace.write_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": path,
                            "line_count": len(content.splitlines()),
                            "action": "created",
                        }
                    },
                )
            if tool_name == "sandbox.run_command":
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="Command executed in `.`:\n$ python3 -m compileall -q generated/telegram-bot/src\n- Exit code: 0",
                    mode="tool_executed",
                    tool_name="sandbox.run_command",
                    details={
                        "artifacts": [
                            {
                                "artifact_type": "command_output",
                                "command": "python3 -m compileall -q generated/telegram-bot/src",
                                "cwd": ".",
                                "returncode": 0,
                                "stdout": "",
                                "stderr": "",
                                "status": "executed",
                            }
                        ],
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "sandbox.run_command",
                            "tool_surface": "sandbox",
                            "ok": True,
                            "status": "executed",
                            "command": "python3 -m compileall -q generated/telegram-bot/src",
                            "cwd": ".",
                            "returncode": 0,
                        }
                    },
                )
            raise AssertionError(f"unexpected builder tool: {tool_name}")

        with mock.patch("apps.nulla_agent.execute_tool_intent", side_effect=_execute_builder_step) as execute_tool_intent, mock.patch(
            "apps.nulla_agent.orchestrate_parent_task", return_value=None
        ):
            result = agent.run_once(
                "build a telegram bot in this workspace and write the files",
                session_id_override="openclaw:builder-controller",
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/nulla-builder-controller"},
            )

        self.assertEqual(result["mode"], "tool_executed")
        self.assertIn("bounded telegram build loop", result["response"].lower())
        self.assertEqual(result["details"]["builder_controller"]["mode"], "scaffold")
        self.assertEqual(result["details"]["builder_controller"]["step_count"], 5)
        self.assertEqual(result["details"]["builder_controller"]["stop_reason"], "command_stop_after_success")
        self.assertEqual(execute_tool_intent.call_count, 5)
        self.assertEqual(agent.memory_router.resolve_tool_intent.call_count, 0)
        final_history = list(agent.memory_router.resolve.call_args.kwargs["source_context"].get("conversation_history") or [])
        joined_history = "\n".join(str(item.get("content") or "") for item in final_history)
        self.assertIn('"intent": "workspace.write_file"', joined_history)
        self.assertIn('"intent": "sandbox.run_command"', joined_history)
        self.assertNotIn("Real tool result from", joined_history)
        artifacts = result["details"]["builder_controller"]["artifacts"]
        self.assertTrue(artifacts["file_diffs"])
        self.assertTrue(artifacts["command_outputs"])
        self.assertEqual(artifacts["stop_reason"], "command_stop_after_success")
        self.assertIn("Artifacts:", result["response"])

    def test_builder_controller_bootstraps_generic_workspace_folder_and_files(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="builder-generic-final",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="I created the starter folder and first files so work can continue inside that workspace.",
                confidence=0.86,
                trust_score=0.87,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
            return_value=MediaAnalysisResult(False, reason="no_external_media")
        )
        agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "apps.nulla_agent.orchestrate_parent_task",
            return_value=None,
        ):
            result = agent.run_once(
                "create a folder called tools and start putting code in there",
                session_id_override="openclaw:builder-generic-bootstrap",
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertEqual(result["mode"], "tool_executed")
            self.assertEqual(result["details"]["builder_controller"]["mode"], "scaffold")
            self.assertTrue((Path(tmpdir) / "tools" / "README.md").is_file())
            self.assertTrue((Path(tmpdir) / "tools" / "src" / "main.py").is_file())
            self.assertEqual(agent.memory_router.resolve_tool_intent.call_count, 0)

    def test_builder_controller_preserves_failures_and_retry_history(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="builder-retry-final",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="I repaired the file and the rerun passed.",
                confidence=0.86,
                trust_score=0.87,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
            return_value=MediaAnalysisResult(False, reason="no_external_media")
        )
        agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]
        agent._should_run_builder_controller = mock.Mock(return_value=True)  # type: ignore[assignment]

        step_counter = {"count": 0}
        command_counter = {"python3 app.py": 0}

        def _execute_builder_step(
            payload,
            *,
            task_id,
            session_id,
            source_context,
            hive_activity_tracker,
            public_hive_bridge=None,
            checkpoint_id=None,
            step_index=0,
        ):
            tool_name = str(payload.get("intent") or "")
            step_counter["count"] += 1
            if tool_name == "sandbox.run_command":
                command = str(dict(payload.get("arguments") or {}).get("command") or "").strip()
                command_counter[command] = int(command_counter.get(command, 0)) + 1
            if tool_name == "sandbox.run_command" and command_counter.get("python3 app.py", 0) == 1:
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="Command executed in `.`:\n$ python3 app.py\n- Exit code: 1\n- Stderr:\nFAILED test_example",
                    mode="tool_executed",
                    tool_name="sandbox.run_command",
                    details={
                        "artifacts": [
                            {
                                "artifact_type": "command_output",
                                "command": "python3 app.py",
                                "cwd": ".",
                                "returncode": 1,
                                "stdout": "",
                                "stderr": "FAILED test_example",
                                "status": "executed",
                            },
                            {
                                "artifact_type": "failure",
                                "command": "python3 app.py",
                                "cwd": ".",
                                "returncode": 1,
                                "summary": "FAILED test_example",
                                "stdout": "",
                                "stderr": "FAILED test_example",
                            },
                        ],
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "sandbox.run_command",
                            "tool_surface": "sandbox",
                            "ok": True,
                            "status": "executed",
                            "command": "python3 app.py",
                            "cwd": ".",
                            "returncode": 1,
                            "stderr": "FAILED test_example",
                            "failure_summary": "FAILED test_example",
                            "error_path": "app.py",
                            "error_line": 1,
                        },
                    },
                )
            if tool_name == "workspace.search_text":
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="Found 1 match for `FAILED test_example` in `app.py`.",
                    mode="tool_executed",
                    tool_name="workspace.search_text",
                    details={
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "workspace.search_text",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "query": "FAILED test_example",
                            "match_count": 1,
                            "matches": [
                                {
                                    "path": "app.py",
                                    "line": 1,
                                    "preview": "TODO",
                                }
                            ],
                        }
                    },
                )
            if tool_name == "workspace.read_file":
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="File `app.py`:\n1: TODO",
                    mode="tool_executed",
                    tool_name="workspace.read_file",
                    details={
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "app.py",
                            "start_line": 1,
                            "line_count": 1,
                        }
                    },
                )
            if tool_name == "workspace.replace_in_file":
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="Applied 1 replacement in `app.py`.",
                    mode="tool_executed",
                    tool_name="workspace.replace_in_file",
                    details={
                        "artifacts": [
                            {
                                "artifact_type": "file_diff",
                                "path": "app.py",
                                "action": "replaced",
                                "replacements": 1,
                                "diff_preview": "--- a/app.py\n+++ b/app.py\n@@\n-TODO\n+DONE",
                            }
                        ],
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "workspace.replace_in_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "app.py",
                            "replacements": 1,
                            "diff_preview": "--- a/app.py\n+++ b/app.py\n@@\n-TODO\n+DONE",
                        },
                    },
                )
            if tool_name == "sandbox.run_command" and command_counter.get("python3 app.py", 0) == 2:
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="Command executed in `.`:\n$ python3 app.py\n- Exit code: 0\n- Stdout:\nclean",
                    mode="tool_executed",
                    tool_name="sandbox.run_command",
                    details={
                        "artifacts": [
                            {
                                "artifact_type": "command_output",
                                "command": "python3 app.py",
                                "cwd": ".",
                                "returncode": 0,
                                "stdout": "clean",
                                "stderr": "",
                                "status": "executed",
                            }
                        ],
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "sandbox.run_command",
                            "tool_surface": "sandbox",
                            "ok": True,
                            "status": "executed",
                            "command": "python3 app.py",
                            "cwd": ".",
                            "returncode": 0,
                            "stdout": "clean",
                        },
                    },
                )
            raise AssertionError(f"unexpected builder tool: {tool_name}")

        with mock.patch("apps.nulla_agent.execute_tool_intent", side_effect=_execute_builder_step), mock.patch(
            "apps.nulla_agent.orchestrate_parent_task", return_value=None
        ), mock.patch(
            "apps.nulla_agent.classify",
            return_value={"task_class": "debugging", "risk_flags": [], "confidence_hint": 0.82},
        ):
            result = agent.run_once(
                "run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
                session_id_override="openclaw:builder-retry-history",
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/nulla-builder-retry"},
            )

        artifacts = result["details"]["builder_controller"]["artifacts"]
        self.assertEqual(result["details"]["builder_controller"]["mode"], "workflow")
        self.assertTrue(artifacts["failures"])
        self.assertTrue(artifacts["retry_history"])
        self.assertEqual(artifacts["retry_history"][0]["attempts"], 2)
        self.assertIn("FAILED test_example", artifacts["failures"][0]["summary"])
        self.assertIn("failures seen", result["response"].lower())
        self.assertIn("retries", result["response"].lower())


if __name__ == "__main__":
    unittest.main()
