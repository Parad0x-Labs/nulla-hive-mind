from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.public_hive_bridge import PublicHiveBridgeConfig
from core.tool_intent_executor import execute_tool_intent, runtime_tool_specs, should_attempt_tool_intent


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


if __name__ == "__main__":
    unittest.main()
