from __future__ import annotations

import unittest
import uuid
from unittest import mock

from apps.nulla_agent import NullaAgent
from core.channel_gateway import (
    ChannelRequest,
    build_source_context,
    channel_output_policy,
    channel_session_id,
    process_channel_request,
    render_channel_response,
)
from core.mobile_companion_view import build_mobile_companion_snapshot, render_mobile_companion_snapshot
from ops.mobile_channel_preflight_report import build_mobile_channel_preflight_report
from storage.migrations import run_migrations


class _FakeAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run_once(self, user_input: str, *, session_id_override: str | None = None, source_context: dict[str, object] | None = None) -> dict[str, object]:
        self.calls.append(
            {
                "user_input": user_input,
                "session_id_override": session_id_override,
                "source_context": dict(source_context or {}),
            }
        )
        return {
            "task_id": f"task-{uuid.uuid4().hex}",
            "response": "Safe bounded response for channel testing.",
            "mode": "advice_only",
            "confidence": 0.61,
            "prompt_assembly_report": {"retrieval_confidence": "low", "total_context_budget": 900},
        }


class ChannelGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_channel_session_id_scopes_users_and_platforms(self) -> None:
        a = channel_session_id(
            platform="telegram",
            user_id="user-a",
            channel_id="chat-1",
            persona_id="default",
            device_hint="phone",
        )
        b = channel_session_id(
            platform="telegram",
            user_id="user-b",
            channel_id="chat-1",
            persona_id="default",
            device_hint="phone",
        )
        c = channel_session_id(
            platform="discord",
            user_id="user-a",
            channel_id="chat-1",
            persona_id="default",
            device_hint="phone",
        )
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertIn("telegram", a)
        self.assertIn("discord", c)

    def test_channel_output_policy_is_bounded_and_metadata_first(self) -> None:
        telegram = channel_output_policy("telegram")
        discord = channel_output_policy("discord")
        web = channel_output_policy("web_companion")
        self.assertTrue(telegram.metadata_first)
        self.assertTrue(discord.metadata_first)
        self.assertTrue(web.metadata_first)
        self.assertLess(discord.max_chars, web.max_chars)

    def test_render_channel_response_truncates_when_over_platform_limit(self) -> None:
        text = "x" * 5000
        rendered, truncated = render_channel_response(text, platform="discord")
        self.assertTrue(truncated)
        self.assertIn("[truncated]", rendered)
        self.assertLessEqual(len(rendered), channel_output_policy("discord").max_chars + 20)

    def test_process_channel_request_passes_session_scope_and_source_context(self) -> None:
        agent = _FakeAgent()
        request = ChannelRequest(
            platform="telegram",
            user_id="tester-1",
            channel_id="chat-42",
            text="pls harden tg setup",
            device_hint="phone",
            surface="telegram_bot",
        )
        result = process_channel_request(agent, request)
        self.assertEqual(len(agent.calls), 1)
        call = agent.calls[0]
        self.assertEqual(call["session_id_override"], result.session_id)
        self.assertEqual(call["source_context"]["platform"], "telegram")
        self.assertEqual(call["source_context"]["surface"], "telegram_bot")
        self.assertFalse(result.truncated)

    def test_build_source_context_carries_output_policy(self) -> None:
        request = ChannelRequest(
            platform="web_companion",
            user_id="tester-web",
            channel_id="phone-browser",
            text="show summary",
            surface="web_companion",
        )
        context = build_source_context(request)
        self.assertEqual(context["platform"], "web_companion")
        self.assertTrue(context["output_policy"]["metadata_first"])
        self.assertIn("max_chars", context["output_policy"])

    def test_mobile_companion_snapshot_is_metadata_first(self) -> None:
        snapshot = build_mobile_companion_snapshot(limit_recent=3)
        self.assertEqual(snapshot["privacy_mode"], "metadata_first")
        self.assertFalse(snapshot["archive_included"])
        self.assertFalse(snapshot["remote_payloads_included"])
        self.assertIn("mesh_overview", snapshot)
        rendered = render_mobile_companion_snapshot(snapshot)
        self.assertIn("NULLA MOBILE COMPANION SNAPSHOT", rendered)

    def test_preflight_report_exposes_surface_and_role_posture(self) -> None:
        report = build_mobile_channel_preflight_report()
        self.assertEqual(report["status"], "preflight_ready_for_controlled_testing")
        self.assertEqual(report["device_role_policy"]["phone"], "companion_or_presence_mirror")
        self.assertTrue(report["privacy_posture"]["metadata_first"])

    def test_actual_agent_flow_accepts_channel_session_override(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request = ChannelRequest(
            platform="web_companion",
            user_id="actual-user",
            channel_id="browser-session",
            text="check current local setup status",
            surface="web_companion",
            device_hint="phone",
        )
        with mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
            "apps.nulla_agent.request_relevant_holders", return_value=[]
        ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
            result = process_channel_request(agent, request)
        self.assertTrue(result.session_id.startswith("phone:web_companion"))
        self.assertEqual(result.source_context["platform"], "web_companion")
        self.assertIn("retrieval_confidence", result.prompt_assembly_report)


if __name__ == "__main__":
    unittest.main()
