from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from core.curiosity_roamer import AdaptiveResearchResult
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.public_hive_bridge import PublicHiveBridgeConfig
from core.tool_intent_executor import execute_tool_intent, plan_tool_workflow, runtime_tool_specs, should_attempt_tool_intent


class ToolIntentExecutorTests(unittest.TestCase):
    def test_builder_style_integration_request_skips_tool_intent_gate(self) -> None:
        should_run = should_attempt_tool_intent(
            "Help me build a next gen Telegram bot from official docs and good GitHub repos.",
            task_class="integration_orchestration",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertFalse(should_run)

    def test_runtime_tool_specs_hide_mutating_hive_tools_without_write_auth(self) -> None:
        with mock.patch(
            "core.tool_intent_executor.load_public_hive_bridge_config",
            return_value=PublicHiveBridgeConfig(
                enabled=True,
                meet_seed_urls=("https://seed-eu.example.test:8766",),
                topic_target_url="https://seed-eu.example.test:8766",
                auth_token=None,
            ),
        ), mock.patch(
            "core.tool_intent_executor.load_hive_activity_tracker_config",
            return_value=HiveActivityTrackerConfig(enabled=True, watcher_api_url="https://watch.example.test/api/dashboard"),
        ):
            intents = {item["intent"] for item in runtime_tool_specs()}

        self.assertIn("hive.list_available", intents)
        self.assertIn("hive.list_research_queue", intents)
        self.assertNotIn("hive.research_topic", intents)
        self.assertNotIn("hive.submit_result", intents)

    def test_execute_web_search_intent_formats_results(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        with mock.patch("core.tool_intent_executor.load_builtin_tools", return_value=None), mock.patch(
            "core.tool_intent_executor.WebAdapter.planned_search_query",
            return_value=[
                {
                    "result_title": "Qwen release notes",
                    "result_url": "https://example.test/qwen",
                    "summary": "Fresh update summary",
                    "source_profile_label": "Official docs",
                },
                {
                    "result_title": "OpenClaw changelog",
                    "result_url": "https://example.test/openclaw",
                    "summary": "OpenClaw runtime changes",
                    "source_profile_label": "Reputable repositories",
                },
            ],
        ):
            result = execute_tool_intent(
                {"intent": "web.search", "arguments": {"query": "latest qwen release notes", "limit": 2}},
                task_id="task-123",
                session_id="session-123",
                source_context={"surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
            )

        self.assertTrue(result.handled)
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "tool_executed")
        self.assertIn("Search results for", result.response_text)
        self.assertIn("https://example.test/qwen", result.response_text)
        self.assertEqual(result.details["observation"]["tool_surface"], "web")
        self.assertEqual(result.details["observation"]["query"], "latest qwen release notes")
        self.assertEqual(result.details["observation"]["results"][0]["url"], "https://example.test/qwen")

    def test_execute_unknown_tool_intent_fails_honestly(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        result = execute_tool_intent(
            {"intent": "fake.magic", "arguments": {}},
            task_id="task-123",
            session_id="session-123",
            source_context={"surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
        )

        self.assertTrue(result.handled)
        self.assertFalse(result.ok)
        self.assertEqual(result.mode, "tool_failed")
        self.assertIn("not wired", result.response_text)
        self.assertEqual(result.details["observation"]["status"], "unsupported")

    def test_execute_hive_submit_result_uses_public_bridge(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        bridge = mock.Mock()
        bridge.submit_public_topic_result.return_value = {
            "ok": True,
            "status": "result_submitted",
            "topic_id": "topic-1234567890abcdef",
            "post_id": "post-123",
        }

        result = execute_tool_intent(
            {
                "intent": "hive.submit_result",
                "arguments": {
                    "topic_id": "topic-1234567890abcdef",
                    "body": "Done. Real event stream is live.",
                    "result_status": "solved",
                    "claim_id": "claim-123",
                },
            },
            task_id="task-123",
            session_id="session-123",
            source_context={"surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
            public_hive_bridge=bridge,
        )

        self.assertTrue(result.handled)
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "tool_executed")
        self.assertIn("marked it `solved`", result.response_text)
        bridge.submit_public_topic_result.assert_called_once()

    def test_execute_hive_export_research_packet_uses_public_bridge(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        bridge = mock.Mock()
        bridge.get_public_research_packet.return_value = {
            "topic": {"topic_id": "topic-1", "title": "Research packet topic"},
            "execution_state": {"execution_state": "claimed"},
            "counts": {"post_count": 3, "evidence_count": 5},
        }

        result = execute_tool_intent(
            {"intent": "hive.export_research_packet", "arguments": {"topic_id": "topic-1"}},
            task_id="task-123",
            session_id="session-123",
            source_context={"surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
            public_hive_bridge=bridge,
        )

        self.assertTrue(result.ok)
        self.assertIn("Exported machine-readable research packet", result.response_text)
        bridge.get_public_research_packet.assert_called_once_with("topic-1")

    def test_execute_hive_research_topic_uses_autonomous_lane(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        bridge = mock.Mock()
        with mock.patch(
            "core.tool_intent_executor.research_topic_from_signal",
            return_value=mock.Mock(
                to_dict=lambda: {
                    "ok": True,
                    "status": "completed",
                    "response_text": "Autonomous research finished.",
                    "artifact_ids": ["artifact-1", "artifact-2"],
                    "candidate_ids": ["candidate-1"],
                }
            ),
        ) as research_topic_from_signal:
            result = execute_tool_intent(
                {"intent": "hive.research_topic", "arguments": {"topic_id": "topic-1"}},
                task_id="task-123",
                session_id="session-123",
                source_context={"surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
                public_hive_bridge=bridge,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "completed")
        self.assertIn("Autonomous research finished", result.response_text)
        research_topic_from_signal.assert_called_once()

    def test_execute_operator_tool_adds_structured_observation(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        dispatch = SimpleNamespace(
            ok=True,
            status="reported",
            response_text="Visible services or startup agents:\n- launchd.test: running",
            details={"services": [{"name": "launchd.test", "state": "running"}]},
            learned_plan=None,
        )

        with mock.patch("core.tool_intent_executor.dispatch_operator_action", return_value=dispatch):
            result = execute_tool_intent(
                {"intent": "operator.inspect_services", "arguments": {}},
                task_id="task-123",
                session_id="session-123",
                source_context={"surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "tool_preview")
        self.assertEqual(result.details["observation"]["tool_surface"], "local_operator")
        self.assertEqual(result.details["observation"]["details"]["services"][0]["name"], "launchd.test")

    def test_execute_web_research_uses_adaptive_controller(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        with mock.patch(
            "core.tool_intent_executor.CuriosityRoamer.adaptive_research",
            return_value=AdaptiveResearchResult(
                enabled=True,
                reason="research_task",
                strategy="compare",
                actions_taken=["initial_search", "compare_sources", "stop_answer"],
                queries_run=["supabase firebase bot backend", "supabase firebase comparison tradeoffs sources"],
                notes=[
                    {
                        "result_title": "Supabase docs",
                        "result_url": "https://supabase.com/docs",
                        "summary": "Supabase is Postgres-first.",
                        "origin_domain": "supabase.com",
                    },
                    {
                        "result_title": "Firebase docs",
                        "result_url": "https://firebase.google.com/docs",
                        "summary": "Firebase has tighter managed integrations.",
                        "origin_domain": "firebase.google.com",
                    },
                ],
                source_domains=["supabase.com", "firebase.google.com"],
                evidence_strength="strong",
                compared_sources=True,
                stop_reason="comparison_ready",
            ),
        ):
            result = execute_tool_intent(
                {"intent": "web.research", "arguments": {"query": "compare supabase vs firebase for a telegram bot backend"}},
                task_id="task-123",
                session_id="session-123",
                source_context={"surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
            )

        self.assertTrue(result.ok)
        self.assertIn("Adaptive web research", result.response_text)
        self.assertEqual(result.details["observation"]["strategy"], "compare")
        self.assertIn("compare_sources", result.details["observation"]["actions_taken"])
        self.assertEqual(result.details["observation"]["hits"][1]["domain"], "firebase.google.com")

    def test_execute_web_research_reports_uncertainty_honestly(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        with mock.patch(
            "core.tool_intent_executor.CuriosityRoamer.adaptive_research",
            return_value=AdaptiveResearchResult(
                enabled=True,
                reason="research_task",
                strategy="verify",
                actions_taken=["initial_search", "verify_claim"],
                queries_run=["verify claim", "claim official source verify"],
                notes=[],
                evidence_strength="none",
                admitted_uncertainty=True,
                uncertainty_reason="No grounded live evidence came back for this question.",
            ),
        ):
            result = execute_tool_intent(
                {"intent": "web.research", "arguments": {"query": "verify a shaky claim"}},
                task_id="task-123",
                session_id="session-123",
                source_context={"surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
            )

        self.assertFalse(result.ok)
        self.assertIn("Uncertainty:", result.response_text)
        self.assertTrue(result.details["observation"]["admitted_uncertainty"])
        self.assertEqual(
            result.details["observation"]["uncertainty_reason"],
            "No grounded live evidence came back for this question.",
        )

    def test_explicit_social_lookup_triggers_tool_intent_gate(self) -> None:
        should_run = should_attempt_tool_intent(
            "check Toly on X",
            task_class="unknown",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(should_run)

    def test_workflow_planner_routes_tool_inventory_questions_to_operator_list(self) -> None:
        first = plan_tool_workflow(
            user_text="what tools do you need to complete this task",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_operator_tool_inventory")
        self.assertEqual(first.next_payload["intent"], "operator.list_tools")

    def test_workflow_planner_bootstraps_workspace_directory_for_start_coding_prompt(self) -> None:
        first = plan_tool_workflow(
            user_text="create a folder called tools and start putting code in there",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/nulla-tools"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_directory_bootstrap")
        self.assertEqual(first.next_payload["intent"], "workspace.ensure_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "tools")

    def test_workflow_planner_bootstraps_nested_workspace_directory_from_path_hint(self) -> None:
        first = plan_tool_workflow(
            user_text="create src/api and write the initial files",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/nulla-tools"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_directory_bootstrap")
        self.assertEqual(first.next_payload["intent"], "workspace.ensure_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "src/api")

    def test_workflow_planner_treats_explicit_social_lookup_as_research(self) -> None:
        first = plan_tool_workflow(
            user_text="check Toly on X",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_entity_lookup_search")
        self.assertEqual(first.next_payload["intent"], "web.search")
        self.assertEqual(first.next_payload["arguments"]["query"], "toly x")

    def test_workflow_planner_compacts_public_entity_lookup_before_first_search(self) -> None:
        first = plan_tool_workflow(
            user_text="who is Toly in Solana?",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_entity_lookup_search")
        self.assertEqual(first.next_payload["intent"], "web.search")
        self.assertEqual(first.next_payload["arguments"]["query"], "toly solana")

    def test_workflow_planner_retries_misspelled_public_entity_lookup_and_then_escalates(self) -> None:
        first = plan_tool_workflow(
            user_text="Tolly on X in Solana who is he",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertEqual(first.next_payload["arguments"]["query"], "tolly x solana")

        second = plan_tool_workflow(
            user_text="Tolly on X in Solana who is he",
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "tolly x solana"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "no_results",
                        "query": "tolly x solana",
                        "result_count": 0,
                        "results": [],
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(second.handled)
        self.assertEqual(second.reason, "planned_entity_lookup_retry")
        self.assertEqual(second.next_payload["intent"], "web.search")
        self.assertEqual(second.next_payload["arguments"]["query"], "toly x solana")

        third = plan_tool_workflow(
            user_text="Tolly on X in Solana who is he",
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "tolly x solana"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "no_results",
                        "query": "tolly x solana",
                        "result_count": 0,
                        "results": [],
                    },
                },
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "toly x solana"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "no_results",
                        "query": "toly x solana",
                        "result_count": 0,
                        "results": [],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(third.handled)
        self.assertEqual(third.reason, "planned_entity_lookup_research")
        self.assertEqual(third.next_payload["intent"], "web.research")
        self.assertEqual(third.next_payload["arguments"]["query"], "toly x solana")

    def test_workflow_planner_can_chain_research_steps(self) -> None:
        first = plan_tool_workflow(
            user_text="compare supabase vs firebase for a telegram bot backend",
            task_class="system_design",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(first.handled)
        self.assertEqual(first.next_payload["intent"], "web.search")

        second = plan_tool_workflow(
            user_text="compare supabase vs firebase for a telegram bot backend",
            task_class="system_design",
            executed_steps=[
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "compare supabase vs firebase for a telegram bot backend"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "executed",
                        "result_count": 2,
                        "results": [
                            {"url": "https://supabase.com/docs", "origin_domain": "supabase.com"},
                            {"url": "https://firebase.google.com/docs", "origin_domain": "firebase.google.com"},
                        ],
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(second.handled)
        self.assertEqual(second.next_payload["intent"], "web.fetch")

        third = plan_tool_workflow(
            user_text="compare supabase vs firebase for a telegram bot backend",
            task_class="system_design",
            executed_steps=[
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "compare supabase vs firebase for a telegram bot backend"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "executed",
                        "result_count": 2,
                        "results": [
                            {"url": "https://supabase.com/docs", "origin_domain": "supabase.com"},
                            {"url": "https://firebase.google.com/docs", "origin_domain": "firebase.google.com"},
                        ],
                    },
                },
                {
                    "tool_name": "web.fetch",
                    "arguments": {"url": "https://supabase.com/docs"},
                    "observation": {
                        "intent": "web.fetch",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "executed",
                        "url": "https://supabase.com/docs",
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(third.handled)
        self.assertEqual(third.next_payload["intent"], "web.research")

    def test_workflow_planner_can_drive_diagnose_run_inspect_retry(self) -> None:
        first = plan_tool_workflow(
            user_text="run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
            task_class="debugging",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(first.handled)
        self.assertEqual(first.next_payload["intent"], "sandbox.run_command")

        second = plan_tool_workflow(
            user_text="run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "sandbox.run_command",
                    "arguments": {"command": "python3 app.py"},
                    "observation": {
                        "intent": "sandbox.run_command",
                        "tool_surface": "sandbox",
                        "ok": True,
                        "status": "executed",
                        "command": "python3 app.py",
                        "returncode": 1,
                        "stdout": "app.py:1 TODO marker still present",
                        "stderr": "",
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(second.handled)
        self.assertEqual(second.next_payload["intent"], "workspace.read_file")

        third = plan_tool_workflow(
            user_text="run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "sandbox.run_command",
                    "arguments": {"command": "python3 app.py"},
                    "observation": {
                        "intent": "sandbox.run_command",
                        "tool_surface": "sandbox",
                        "ok": True,
                        "status": "executed",
                        "command": "python3 app.py",
                        "returncode": 1,
                        "stdout": "app.py:1 TODO marker still present",
                        "stderr": "",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 4,
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(third.handled)
        self.assertEqual(third.next_payload["intent"], "workspace.replace_in_file")

        fourth = plan_tool_workflow(
            user_text="run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "sandbox.run_command",
                    "arguments": {"command": "python3 app.py"},
                    "observation": {
                        "intent": "sandbox.run_command",
                        "tool_surface": "sandbox",
                        "ok": True,
                        "status": "executed",
                        "command": "python3 app.py",
                        "returncode": 1,
                        "stdout": "app.py:1 TODO marker still present",
                        "stderr": "",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                    },
                },
                {
                    "tool_name": "workspace.replace_in_file",
                    "arguments": {"path": "app.py", "old_text": "TODO", "new_text": "DONE"},
                    "observation": {
                        "intent": "workspace.replace_in_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "replacements": 1,
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(fourth.handled)
        self.assertEqual(fourth.next_payload["intent"], "sandbox.run_command")

    def test_workflow_planner_can_stop_early_when_enough_state_is_gathered(self) -> None:
        decision = plan_tool_workflow(
            user_text="latest qwen release notes",
            task_class="research",
            executed_steps=[
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "latest qwen release notes"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "executed",
                        "result_count": 3,
                        "results": [
                            {"url": "https://example.test/qwen-1", "origin_domain": "example.test"},
                            {"url": "https://example.test/qwen-2", "origin_domain": "example-two.test"},
                            {"url": "https://example.test/qwen-3", "origin_domain": "example-three.test"},
                        ],
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(decision.handled)
        self.assertTrue(decision.stop_after)
        self.assertEqual(decision.reason, "research_enough_after_search")

    def test_workflow_planner_can_continue_or_stop_after_workspace_write(self) -> None:
        run_decision = plan_tool_workflow(
            user_text="build a telegram bot in the workspace and then run `python3 -m compileall -q generated/telegram-bot/src`",
            task_class="integration_orchestration",
            executed_steps=[
                {
                    "tool_name": "workspace.write_file",
                    "arguments": {"path": "generated/telegram-bot/src/bot.py"},
                    "observation": {
                        "intent": "workspace.write_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "generated/telegram-bot/src/bot.py",
                        "line_count": 42,
                        "action": "created",
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(run_decision.handled)
        self.assertEqual(run_decision.next_payload["intent"], "sandbox.run_command")

        stop_decision = plan_tool_workflow(
            user_text="build a telegram bot in the workspace and write the files",
            task_class="integration_orchestration",
            executed_steps=[
                {
                    "tool_name": "workspace.write_file",
                    "arguments": {"path": "generated/telegram-bot/src/bot.py"},
                    "observation": {
                        "intent": "workspace.write_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "generated/telegram-bot/src/bot.py",
                        "line_count": 42,
                        "action": "created",
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(stop_decision.handled)
        self.assertTrue(stop_decision.stop_after)
        self.assertEqual(stop_decision.reason, "workspace_stop_after_write")

    def test_workflow_planner_creates_hive_task_for_create_these_tasks(self) -> None:
        decision = plan_tool_workflow(
            user_text="create these tasks on Hive",
            task_class="research",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_hive_create_topic")
        self.assertEqual(decision.next_payload["intent"], "hive.create_topic")
        self.assertIn(decision.next_payload["arguments"]["title"], ("User-requested Hive task", "on Hive"))

    def test_workflow_planner_creates_hive_task_for_proceed_with_task_context(self) -> None:
        decision = plan_tool_workflow(
            user_text="proceed with creating these hive tasks",
            task_class="research",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_hive_create_topic")
        self.assertEqual(decision.next_payload["intent"], "hive.create_topic")

    def test_should_attempt_tool_intent_for_proceed_followup(self) -> None:
        self.assertTrue(should_attempt_tool_intent("proceed with next steps", task_class="research"))
        self.assertTrue(should_attempt_tool_intent("do all and start working", task_class="research"))

    def test_workflow_planner_does_not_invent_nonexistent_email_tool(self) -> None:
        decision = plan_tool_workflow(
            user_text="send an email to ops with the incident summary",
            task_class="integration_orchestration",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertFalse(decision.handled)
        self.assertEqual(decision.reason, "no_workflow_plan")


if __name__ == "__main__":
    unittest.main()
