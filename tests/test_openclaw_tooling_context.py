from __future__ import annotations

import contextlib
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from apps.nulla_agent import ChatTurnResult, NullaAgent, ResponseClass
from core.autonomous_topic_research import AutonomousResearchResult
from core.bootstrap_context import build_bootstrap_context
from core.curiosity_roamer import CuriosityResult
from core.hive_activity_tracker import session_hive_state, set_hive_interaction_state, update_session_hive_state
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.memory_first_router import ModelExecutionDecision
from core.prompt_normalizer import normalize_prompt
from core.public_hive_bridge import PublicHiveBridgeConfig
from core.runtime_task_events import register_runtime_event_sink, unregister_runtime_event_sink
from core.task_router import classify, create_task_record, redact_text
from core.tool_intent_executor import ToolIntentExecution
from core.user_preferences import maybe_handle_preference_command
from storage.migrations import run_migrations


class OpenClawToolingContextTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def _clear_nullabook_state(self) -> None:
        from storage.db import get_connection

        conn = get_connection()
        try:
            for table in ("nullabook_posts", "nullabook_tokens", "nullabook_profiles", "agent_names"):
                with contextlib.suppress(Exception):
                    conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()

    def test_bootstrap_context_includes_openclaw_doctrine(self) -> None:
        persona = load_active_persona("default")
        task = create_task_record("set up calendar and email workflow for this week")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="request",
            topic_hints=["calendar workflow", "email workflow"],
            reference_targets=[],
            understanding_confidence=0.84,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        items = build_bootstrap_context(
            persona=persona,
            task=task,
            classification=classification,
            interpretation=interpretation,
            session_id=f"ctx-{uuid.uuid4().hex}",
        )
        self.assertIn("bootstrap-openclaw-doctrine", {item.item_id for item in items})

    def test_bootstrap_identity_context_says_operator_can_rename(self) -> None:
        persona = load_active_persona("default")
        task = create_task_record("what is your name")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="question",
            topic_hints=["identity"],
            reference_targets=[],
            understanding_confidence=0.95,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        with mock.patch("core.onboarding.load_identity", return_value={"agent_name": "Cornholio", "privacy_pact": "local only"}):
            items = build_bootstrap_context(
                persona=persona,
                task=task,
                classification=classification,
                interpretation=interpretation,
                session_id=f"ctx-{uuid.uuid4().hex}",
            )

        identity_item = next(item for item in items if item.item_id == "bootstrap-owner-identity")
        self.assertIn("current display name is Cornholio", identity_item.content)
        self.assertIn("rename me", identity_item.content.lower())
        self.assertNotIn("permanent", identity_item.content.lower())

    def test_openclaw_surface_triggers_live_web_lookup_for_fresh_requests(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(source="memory_hit", task_hash="test", used_model=False)
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )

        with mock.patch("apps.nulla_agent.WebAdapter.search_query", return_value=[{"summary": "fresh snippet"}]) as search_query, mock.patch(
            "apps.nulla_agent.WebAdapter.planned_search_query", return_value=[{"summary": "fresh snippet"}]
        ) as planned_search_query, mock.patch(
            "apps.nulla_agent.should_attempt_tool_intent",
            return_value=False,
        ), mock.patch(
            "apps.nulla_agent.orchestrate_parent_task", return_value=None
        ), mock.patch("apps.nulla_agent.request_relevant_holders", return_value=[]), mock.patch(
            "apps.nulla_agent.dispatch_query_shard", return_value=None
        ):
            agent.run_once(
                "latest telegram bot api updates",
                source_context={"surface": "channel", "platform": "openclaw"},
            )

        self.assertTrue(
            search_query.called or planned_search_query.called,
            "Expected either search_query or planned_search_query to be called for a fresh info request on OpenClaw surface",
        )

    def test_openclaw_research_request_uses_source_planned_web_lookup(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="planned-source",
                used_model=True,
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Telegram Bot API docs are the canonical source for auth, update delivery, and webhook limits.",
                confidence=0.7,
                trust_score=0.75,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.context_loader.load = mock.Mock(  # type: ignore[assignment]
            return_value=SimpleNamespace(
                local_candidates=[],
                swarm_metadata=[],
                retrieval_confidence_score=0.10,
                context_snippets=lambda: [],
                assembled_context=lambda: "",
                report=SimpleNamespace(
                    to_dict=lambda: {"retrieval_confidence": "low"},
                    retrieval_confidence="low",
                    total_tokens_used=lambda: 0,
                ),
            )
        )

        with mock.patch(
            "apps.nulla_agent.WebAdapter.planned_search_query",
            return_value=[
                {
                    "summary": "Telegram Bot API docs are the canonical source for auth, update delivery, and webhook limits.",
                    "confidence": 0.67,
                    "source_profile_id": "messaging_platform_docs",
                    "source_profile_label": "Messaging platform docs",
                    "result_title": "Telegram Bot API",
                    "result_url": "https://core.telegram.org/bots/api",
                    "origin_domain": "core.telegram.org",
                }
            ],
        ) as planned_search, mock.patch(
            "apps.nulla_agent.WebAdapter.search_query",
            side_effect=AssertionError("generic search should not run"),
        ), mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
            "apps.nulla_agent.request_relevant_holders", return_value=[]
        ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None), mock.patch.object(
            agent, "_sync_public_presence", return_value=None
        ):
            result = agent.run_once(
                "research telegram bot auth and deployment best practices",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(planned_search.called)
        self.assertIn("canonical source", result["response"].lower())

    def test_openclaw_weather_request_uses_live_web_fast_path(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.context_loader.load = mock.Mock(side_effect=AssertionError("context loader should not run"))  # type: ignore[assignment]

        with mock.patch(
            "apps.nulla_agent.WebAdapter.search_query",
            return_value=[
                {
                    "summary": "London, United Kingdom: Cloudy with light rain, 11 C (feels like 9 C), humidity 82%, wind 14 km/h. Observed 09:00 AM.",
                    "source_label": "wttr.in",
                    "origin_domain": "wttr.in",
                    "result_title": "wttr.in weather for London",
                    "result_url": "https://wttr.in/London",
                    "used_browser": False,
                }
            ],
        ) as search_query:
            result = agent.run_once(
                "what is the weather in London today?",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(search_query.called)
        lowered = result["response"].lower()
        self.assertIn("cloudy with light rain", lowered)
        self.assertIn("source: [wttr.in](https://wttr.in/london)", lowered)
        self.assertNotIn("live weather results", lowered)
        self.assertEqual(result.get("response_class"), "utility_answer")

    def test_openclaw_news_request_returns_honest_live_lookup_failure(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.context_loader.load = mock.Mock(side_effect=AssertionError("context loader should not run"))  # type: ignore[assignment]

        with mock.patch("apps.nulla_agent.WebAdapter.search_query", return_value=[]), mock.patch(
            "apps.nulla_agent.WebAdapter.planned_search_query", return_value=[]
        ):
            result = agent.run_once(
                "latest news on OpenAI",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("no current news results came back", result["response"].lower())
        self.assertEqual(result.get("response_class"), "utility_answer")

    def test_smalltalk_frustration_fast_path_stays_human(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        response = agent._smalltalk_fast_path("omfg just kill me lol", source_surface="openclaw", session_id="openclaw:smalltalk-frustration")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("frustrated", response.lower())
        self.assertNotIn("untrusted metadata", response.lower())
        self.assertNotIn("disable", response.lower())

    def test_smalltalk_gm_fast_path_stays_human(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        response = agent._smalltalk_fast_path("gm", source_surface="openclaw", session_id="openclaw:smalltalk-gm")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("what do you need", response.lower())

    def test_smalltalk_repeated_greetings_stop_using_identical_canned_reply(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        first = agent._smalltalk_fast_path("hey", source_surface="openclaw", session_id="openclaw:smalltalk-repeat")
        second = agent._smalltalk_fast_path("yo", source_surface="openclaw", session_id="openclaw:smalltalk-repeat")
        third = agent._smalltalk_fast_path("hello", source_surface="openclaw", session_id="openclaw:smalltalk-repeat")

        assert first is not None and second is not None and third is not None
        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)
        self.assertIn("what do you want me to do", second.lower())
        self.assertIn("skip the greeting", third.lower())

    def test_openclaw_ui_command_fast_path_handles_new_and_trace(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        self.assertIn("new session", str(agent._ui_command_fast_path("/new", source_surface="openclaw")).lower())
        self.assertIn("/trace", str(agent._ui_command_fast_path("/trace", source_surface="openclaw")).lower())

    def test_openclaw_date_fast_path_answers_plainly(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        response = agent._date_time_fast_path("what is the date today?", source_surface="openclaw")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("today is", response.lower())
        self.assertRegex(response, r"\d{4}-\d{2}-\d{2}")

    def test_builder_style_telegram_request_classifies_as_system_design(self) -> None:
        interpretation = HumanInputInterpretation(
            raw_text="Help me build a next gen Telegram bot from official docs and good GitHub repos.",
            normalized_text="Help me build a next gen Telegram bot from official docs and good GitHub repos.",
            reconstructed_text="Help me build a next gen Telegram bot from official docs and good GitHub repos.",
            intent_mode="request",
            topic_hints=["telegram bot", "github", "docs"],
            reference_targets=[],
            understanding_confidence=0.90,
            quality_flags=[],
        )

        classification = classify(interpretation.reconstructed_text, context=interpretation.as_context())

        self.assertEqual(classification["task_class"], "system_design")

    def test_workspace_build_pipeline_writes_researched_telegram_scaffold(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
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
            return_value=ModelExecutionDecision(source="memory_hit", task_hash="builder-memory", used_model=False)
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "apps.nulla_agent.WebAdapter.planned_search_query",
            return_value=[
                {
                    "summary": "Telegram Bot API docs define auth, updates, and webhook constraints.",
                    "confidence": 0.69,
                    "source_profile_id": "messaging_platform_docs",
                    "source_profile_label": "Messaging platform docs",
                    "result_title": "Telegram Bot API",
                    "result_url": "https://core.telegram.org/bots/api",
                    "origin_domain": "core.telegram.org",
                },
                {
                    "summary": "A reputable Telegram bot repo shows practical handler layout and deployment hygiene.",
                    "confidence": 0.58,
                    "source_profile_id": "reputable_repos",
                    "source_profile_label": "Reputable repositories",
                    "result_title": "python-telegram-bot examples",
                    "result_url": "https://github.com/python-telegram-bot/python-telegram-bot",
                    "origin_domain": "github.com",
                },
            ],
        ), mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
            "apps.nulla_agent.request_relevant_holders", return_value=[]
        ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None), mock.patch(
            "apps.nulla_agent.should_attempt_tool_intent",
            return_value=False,
        ):
            result = agent.run_once(
                "build a telegram bot in this workspace and write the files",
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )
            scaffold_root = Path(tmpdir) / "generated" / "telegram-bot"
            readme = (scaffold_root / "README.md").read_text(encoding="utf-8")
            bot_source = (scaffold_root / "src" / "bot.py").read_text(encoding="utf-8")

        lowered_response = result["response"].lower()
        self.assertTrue("telegram-bot" in lowered_response or "generated/telegram-bot" in lowered_response)
        self.assertIn("Telegram Bot API", readme)
        self.assertIn("https://core.telegram.org/bots/api", readme)
        self.assertIn("python-telegram-bot", readme)
        self.assertIn("ApplicationBuilder", bot_source)
        self.assertTrue("compileall" in result["response"] or "command_stop" in lowered_response)

    def test_ui_command_fast_path_handles_new_without_surface_metadata(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        self.assertIn("new session", str(agent._ui_command_fast_path("/new", source_surface="cli")).lower())

    def test_openclaw_smalltalk_fast_path_does_not_append_hive_footer(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "gm",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result["response"])
        self.assertNotIn("noisy footer", result["response"])
        self.assertNotIn("open tasks", result["response"].lower())

    def test_openclaw_frustration_fast_path_avoids_generic_fallback(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "wow took u 2 mins to say this bs?!",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result["response"])
        self.assertNotIn("ready to help", result["response"].lower())
        self.assertNotIn("noisy footer", result["response"])

    def test_openclaw_ui_command_fast_path_does_not_append_hive_footer(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "/new",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("New session", result["response"])
        self.assertNotIn("noisy footer", result["response"])

    def test_openclaw_evaluative_turn_stays_conversational_without_hive_footer(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "ohmy gad yu not a dumbs anymore?!",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result["response"])
        self.assertNotIn("noisy footer", result["response"].lower())
        self.assertEqual(result.get("response_class"), "generic_conversation")

    def test_openclaw_date_variant_stays_plain_and_does_not_leak_runtime_noise(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "what is the day today ?",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("today is", result["response"].lower())
        self.assertNotIn("invalid tool payload", result["response"].lower())
        self.assertNotIn("i won't fake it", result["response"].lower())
        self.assertNotIn("noisy footer", result["response"].lower())
        self.assertEqual(result.get("response_class"), "utility_answer")

    def test_utility_turn_does_not_wipe_pending_hive_selection_state(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        session_id = "openclaw:utility-preserve"
        update_session_hive_state(
            session_id,
            watched_topic_ids=[],
            seen_post_ids=[],
            pending_topic_ids=["topic-1"],
            seen_curiosity_topic_ids=[],
            seen_curiosity_run_ids=[],
            seen_agent_ids=[],
            last_active_agents=0,
            interaction_mode="hive_task_selection_pending",
            interaction_payload={
                "shown_topic_ids": ["topic-1"],
                "shown_titles": ["OpenClaw integration audit"],
            },
        )

        result = agent.run_once(
            "what day is today?",
            session_id_override=session_id,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        state = session_hive_state(session_id)
        self.assertIn("today is", result["response"].lower())
        self.assertIn(state["interaction_mode"], ("hive_task_selection_pending", "utility"))
        self.assertEqual(state["pending_topic_ids"], ["topic-1"])

    def test_hive_command_falls_back_to_public_bridge_when_watcher_is_unavailable(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        session_id = f"openclaw:bridge-fallback:{uuid.uuid4().hex}"
        topics = [
            {
                "topic_id": "topic-bridge-1",
                "title": "Bridge fallback task",
                "status": "open",
                "created_by_agent_id": "peer-1",
            }
        ]

        with mock.patch.object(
            agent.hive_activity_tracker,
            "maybe_handle_command_details",
            return_value=(True, {
                "response_text": "I couldn't reach the Hive watcher right now.",
                "command_kind": "watcher_unavailable",
                "watcher_status": "unreachable",
            }),
        ), mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge,
            "list_public_topics",
            return_value=topics,
        ):
            result = agent.run_once(
                "pull the hive tasks",
                session_id_override=session_id,
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        state = session_hive_state(session_id)
        self.assertIn("bridge fallback task", result["response"].lower())
        self.assertEqual(result.get("response_class"), "task_list")
        self.assertEqual(state["pending_topic_ids"], ["topic-bridge-1"])

    def test_openclaw_task_list_reply_does_not_append_hive_footer_noise(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(
            agent.hive_activity_tracker,
            "maybe_handle_command_details",
            return_value=(True, {
                "response_text": "Available Hive tasks right now (1 total):\n- [researching] test task (#abc12345)\n\nIf you want, I can start one.",
                "command_kind": "task_list",
                "topics": [{"topic_id": "abc12345678", "title": "test task", "status": "researching"}],
                "online_agents": [],
                "watcher_status": "ok",
            }),
        ), mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "what are the tasks in Hive mind available?",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue("test task" in result["response"].lower() or "hive task" in result["response"].lower())
        self.assertNotIn("noisy footer", result["response"])
        self.assertEqual(result.get("response_class"), "task_list")

    def test_task_started_reply_is_shaped_like_chat_not_trace(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            ChatTurnResult(
                text="Autonomous research on `ada43859` packed 3 research queries, 0 candidate notes, and 0 gate decisions.",
                response_class=ResponseClass.TASK_STARTED,
                workflow_summary="- internal workflow noise",
            ),
            session_id="openclaw:task-start",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertIn("Started Hive research on `ada43859`.", decorated)
        self.assertIn("First bounded pass is underway.", decorated)
        self.assertNotIn("candidate notes", decorated.lower())
        self.assertNotIn("gate decisions", decorated.lower())
        self.assertNotIn("Workflow:\n", decorated)

    def test_research_progress_reply_is_shaped_like_chat_not_trace(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            ChatTurnResult(
                text="Research result: 3 new local research result(s) landed.",
                response_class=ResponseClass.RESEARCH_PROGRESS,
                workflow_summary="- internal workflow noise",
            ),
            session_id="openclaw:research-progress",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertIn("Here’s what I found:", decorated)
        self.assertNotIn("Research result:", decorated)
        self.assertNotIn("Workflow:\n", decorated)

    def test_hive_research_followup_raw_autonomous_text_classifies_as_task_started(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        response_class = agent._fast_path_response_class(
            reason="hive_research_followup",
            response="Autonomous research on `ada43859` packed 3 research queries, 0 candidate notes, and 0 gate decisions.",
        )
        self.assertEqual(response_class, ResponseClass.TASK_STARTED)

    def test_hive_research_followup_prefixes_classify_as_research_progress(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        self.assertEqual(
            agent._fast_path_response_class(
                reason="hive_research_followup",
                response="Research follow-up: 1 new local research thread was queued.",
            ),
            ResponseClass.RESEARCH_PROGRESS,
        )
        self.assertEqual(
            agent._fast_path_response_class(
                reason="hive_research_followup",
                response="Research result: 1 new local research result landed.",
            ),
            ResponseClass.RESEARCH_PROGRESS,
        )

    def test_startup_sequence_system_message_uses_fast_path(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "A new session was started via /new or /reset. Execute your Session Startup sequence now - read the required files before responding to the user.",
            )

        self.assertIn("new session is clean", result["response"].lower())
        self.assertIn("what do you want to do", result["response"].lower())
        self.assertNotIn("invalid tool payload", result["response"].lower())
        self.assertNotIn("noisy footer", result["response"])

    def test_openclaw_trivial_reply_does_not_prepend_workflow_block(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            "You named me Cornholio.",
            session_id="openclaw:test",
            source_context={"surface": "openclaw", "platform": "openclaw"},
            workflow_summary=(
                "- classified task as `unknown`\n"
                "- used model path via `ollama-local:qwen2.5:7b`\n"
                "- media/web evidence status: `no_external_media`\n"
                "- curiosity/research lane: `bounded_auto`\n"
                "- execution posture: `advice_only`"
            ),
        )
        self.assertNotIn("Workflow:\n", decorated)
        self.assertIn("You named me Cornholio.", decorated)

    def test_openclaw_research_reply_keeps_workflow_hidden_by_default(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            "I found three relevant OpenClaw integration threads and one Liquefy proof note.",
            session_id="openclaw:research",
            source_context={"surface": "openclaw", "platform": "openclaw"},
            workflow_summary=(
                "- classified task as `research`\n"
                "- used model path via `ollama-local:qwen2.5:7b`\n"
                "- media/web evidence status: `live_fetch`\n"
                "- curiosity/research lane: `executed`\n"
                "- execution posture: `advice_only`"
            ),
        )
        self.assertNotIn("Workflow:\n", decorated)
        self.assertIn("I found three relevant OpenClaw integration threads", decorated)

    def test_user_chat_sanitization_strips_runtime_preamble_and_raw_failure_text(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            ChatTurnResult(
                text=(
                    "Real steps completed:\n"
                    "- unknown: I won't fake it: the model returned an invalid tool payload with no intent name.\n\n"
                    "I couldn't map that cleanly to a real action."
                ),
                response_class=ResponseClass.TASK_FAILED_USER_SAFE,
            ),
            session_id="openclaw:test",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertNotIn("Real steps completed", decorated)
        self.assertNotIn("I won't fake it", decorated)
        self.assertIn("couldn't map that cleanly", decorated)

    def test_credit_status_fast_path_answers_in_plain_language(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch("core.credit_ledger.reconcile_ledger", return_value=SimpleNamespace(balance=42.5, entries=3, mode="simulated")), mock.patch(
            "core.scoreboard_engine.get_peer_scoreboard",
            return_value=SimpleNamespace(provider=12.0, validator=1.5, trust=0.8, tier="Newcomer"),
        ), mock.patch("core.dna_wallet_manager.DNAWalletManager.get_status", return_value=None), mock.patch(
            "network.signer.get_local_peer_id",
            return_value="peer-test-123",
        ):
            response = agent._credit_status_fast_path(
                "how many credits did we earn from hive tasks",
                source_surface="openclaw",
            )

        assert response is not None
        self.assertIn("42.50 compute credits", response)
        self.assertIn("Provider score 12.0", response)
        self.assertIn("do not mint credits by themselves", response)
        self.assertNotIn("workflow", response.lower())

    def test_openclaw_prompt_preserves_structured_mode_and_discourages_tool_bluffing(self) -> None:
        maybe_handle_preference_command("don't ask for micro step approval")
        persona = load_active_persona("default")
        task = create_task_record("latest OpenClaw release notes")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="question",
            topic_hints=["openclaw", "news"],
            reference_targets=[],
            understanding_confidence=0.88,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        context_result = SimpleNamespace(
            assembled_context=lambda: "",
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )

        request = normalize_prompt(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            output_mode="summary_block",
            task_kind="summarization",
            trace_id=task.task_id,
            surface="openclaw",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        system_prompt = request.system_prompt().lower()
        self.assertEqual(request.output_mode, "summary_block")
        self.assertIn("never claim you searched the web", system_prompt)
        self.assertIn("do not ask for micro-confirmation", system_prompt)
        self.assertIn("workspace file listing", system_prompt)
        self.assertIn("sandboxed local command execution", system_prompt)
        self.assertIn("email and inbox tooling are not guaranteed", system_prompt)

    def test_openclaw_tool_intent_prompt_includes_runtime_catalog(self) -> None:
        persona = load_active_persona("default")
        task = create_task_record("latest OpenClaw release notes")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="question",
            topic_hints=["openclaw", "news"],
            reference_targets=[],
            understanding_confidence=0.88,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        context_result = SimpleNamespace(
            assembled_context=lambda: "",
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )

        with mock.patch(
            "core.tool_intent_executor.load_public_hive_bridge_config",
            return_value=PublicHiveBridgeConfig(
                enabled=True,
                meet_seed_urls=("https://seed-eu.example.test:8766",),
                topic_target_url="https://seed-eu.example.test:8766",
                auth_token="cluster-token",
            ),
        ):
            request = normalize_prompt(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=persona,
                output_mode="tool_intent",
                task_kind="tool_intent",
                trace_id=task.task_id,
                surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        system_prompt = request.system_prompt().lower()
        self.assertEqual(request.output_mode, "tool_intent")
        self.assertIn("web.search", system_prompt)
        self.assertIn("workspace.read_file", system_prompt)
        self.assertIn("sandbox.run_command", system_prompt)
        self.assertIn("hive.export_research_packet", system_prompt)
        self.assertIn("hive.research_topic", system_prompt)
        self.assertIn("respond.direct", system_prompt)
        self.assertIn("never invent intent names", system_prompt)

    def test_openclaw_model_tool_intent_returns_tool_execution_result(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
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
        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="tool-intent-hash",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                structured_output={"intent": "web.search", "arguments": {"query": "latest qwen release notes", "limit": 2}},
                confidence=0.8,
                trust_score=0.84,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="final-synthesis-hash",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Grounded from the real search results.",
                confidence=0.81,
                trust_score=0.85,
                used_model=True,
                validation_state="valid",
            )
        )
        with mock.patch.object(
            agent,
            "_live_info_mode",
            return_value="",
        ), mock.patch.object(
            agent,
            "_should_keep_ai_first_chat_lane",
            return_value=False,
        ), mock.patch(
            "apps.nulla_agent.execute_tool_intent",
            return_value=ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text='Search results for "latest qwen release notes":\n- Qwen release notes - https://example.test/qwen',
                mode="tool_executed",
                tool_name="web.search",
                details={"query": "latest qwen release notes"},
            ),
        ) as execute_tool_intent, mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None) as orchestrate_parent_task:
            result = agent.run_once(
                "latest qwen release notes",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertEqual(result["mode"], "tool_executed")
        self.assertNotIn("Real steps completed:", result["response"])
        self.assertIn("Grounded from the real search results.", result["response"])
        self.assertEqual(execute_tool_intent.call_count, 1)
        orchestrate_parent_task.assert_not_called()

    def test_openclaw_tool_intent_missing_intent_falls_through_to_planned_research(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
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
        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="tool-intent-missing",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                structured_output={},
                confidence=0.31,
                trust_score=0.28,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="planned-fallback",
                used_model=True,
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Based on official documentation, Telegram Bot API docs are the canonical source for auth constraints.",
                confidence=0.7,
                trust_score=0.75,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        with mock.patch.object(
            agent,
            "_live_info_mode",
            return_value="",
        ), mock.patch(
            "apps.nulla_agent.WebAdapter.planned_search_query",
            return_value=[
                {
                    "summary": "Telegram Bot API docs are the canonical source for auth, update delivery, and release constraints.",
                    "confidence": 0.66,
                    "source_profile_id": "messaging_platform_docs",
                    "source_profile_label": "Messaging platform docs",
                    "result_title": "Telegram Bot API",
                    "result_url": "https://core.telegram.org/bots/api",
                    "origin_domain": "core.telegram.org",
                }
            ],
        ), mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
            "apps.nulla_agent.request_relevant_holders", return_value=[]
        ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None), mock.patch.object(
            agent, "_sync_public_presence", return_value=None
        ):
            result = agent.run_once(
                "latest telegram bot api release notes",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertNotIn("couldn't map that cleanly", result["response"].lower())
        self.assertNotIn("invalid tool payload", result["response"].lower())
        self.assertTrue("telegram" in result["response"].lower() or "canonical" in result["response"].lower())

    def test_openclaw_model_tool_intent_can_finish_with_direct_grounded_reply(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
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
        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            side_effect=[
                ModelExecutionDecision(
                    source="provider_execution",
                    task_hash="tool-intent-search",
                    provider_id="ollama-local:test",
                    provider_name="ollama-local",
                    model_name="test",
                    structured_output={"intent": "workspace.search_text", "arguments": {"query": "tool_intent", "path": "core"}},
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
                        "arguments": {"message": "I searched the workspace and found the relevant tool-intent entry points."},
                    },
                    confidence=0.79,
                    trust_score=0.83,
                    used_model=True,
                    validation_state="valid",
                ),
            ]
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
                details={"query": "tool_intent"},
            ),
        ) as execute_tool_intent, mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None) as orchestrate_parent_task:
            result = agent.run_once(
                "find where tool intent execution is wired in this workspace",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertEqual(result["mode"], "tool_executed")
        self.assertNotIn("Real steps completed:", result["response"])
        self.assertTrue("search matches" in result["response"].lower() or "tool_intent" in result["response"].lower())
        self.assertEqual(execute_tool_intent.call_count, 1)
        orchestrate_parent_task.assert_not_called()

    def test_openclaw_tool_loop_emits_runtime_events_for_streaming(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
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
                        "arguments": {"message": "Grounded final answer from tool output."},
                    },
                    confidence=0.79,
                    trust_score=0.83,
                    used_model=True,
                    validation_state="valid",
                ),
            ]
        )
        events: list[dict[str, object]] = []
        stream_id = "runtime-stream-test"
        register_runtime_event_sink(stream_id, lambda payload: events.append(dict(payload)))
        try:
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
            ), mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None):
                result = agent.run_once(
                    "find tool intent wiring",
                    source_context={
                        "surface": "openclaw",
                        "platform": "openclaw",
                        "runtime_event_stream_id": stream_id,
                    },
                )
        finally:
            unregister_runtime_event_sink(stream_id)

        self.assertEqual(result["mode"], "tool_executed")
        messages = [str(event.get("message") or "").lower() for event in events]
        self.assertTrue(any("task classified as" in m for m in messages))
        self.assertTrue(any("workspace.search_text" in m for m in messages))
        self.assertTrue(any("finished" in m and "workspace.search_text" in m for m in messages) or any("tool step" in m for m in messages))

    def test_openclaw_can_pick_hive_task_and_start_research(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        queue_rows = [
            {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
                "research_priority": 0.9,
                "active_claim_count": 0,
                "claims": [],
            }
        ]
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
        ), mock.patch(
            "apps.nulla_agent.research_topic_from_signal",
            return_value=AutonomousResearchResult(
                ok=True,
                status="completed",
                topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                claim_id="claim-12345678",
                result_status="researching",
                artifact_ids=["artifact-1", "artifact-2"],
                candidate_ids=["cand-1"],
                details={"query_results": [{"query": "q1"}, {"query": "q2"}, {"query": "q3"}]},
            ),
        ) as research_topic_from_signal, mock.patch.object(
            agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"
        ), mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "just pick one and start the hive research",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("started hive research on", result["response"].lower())
        self.assertIn("agent commons", result["response"].lower())
        self.assertNotIn("noisy footer", result["response"])
        self.assertIn("bounded pass", result["response"].lower())
        research_topic_from_signal.assert_called_once()

    def test_openclaw_can_select_specific_hive_task_by_short_id(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        queue_rows = [
            {
                "topic_id": "a951bf9d-7b20-4176-b7ed-20a9d646655c",
                "title": "Agent Commons: brainstorm",
                "status": "researching",
                "research_priority": 0.4,
                "active_claim_count": 0,
                "claims": [],
            },
            {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
                "research_priority": 0.3,
                "active_claim_count": 0,
                "claims": [],
            },
        ]
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
        ), mock.patch(
            "apps.nulla_agent.research_topic_from_signal",
            return_value=AutonomousResearchResult(
                ok=True,
                status="completed",
                topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                claim_id="claim-12345678",
            ),
        ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "[researching] Agent Commons: better human-visible watcher and task-flow UX (#7d33994f). -- lets go with this one",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("agent commons: better human-visible watcher", result["response"].lower())
        selected_signal = research_topic_from_signal.call_args.args[0]
        self.assertEqual(selected_signal["topic_id"], "7d33994f-dd40-4a7e-b78a-f8e2d94fb702")

    def test_openclaw_can_confirm_short_followup_after_hive_task_list(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        queue_rows = [
            {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
                "research_priority": 0.9,
                "active_claim_count": 0,
                "claims": [],
            }
        ]
        hive_state = {
            "pending_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"],
        }
        with mock.patch("apps.nulla_agent.session_hive_state", return_value=hive_state), mock.patch.object(
            agent.public_hive_bridge, "enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
        ), mock.patch(
            "apps.nulla_agent.research_topic_from_signal",
            return_value=AutonomousResearchResult(
                ok=True,
                status="completed",
                topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                claim_id="claim-12345678",
            ),
        ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "OK let's go!",
                session_id_override="openclaw:pending-hive",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("started hive research on", result["response"].lower())
        selected_signal = research_topic_from_signal.call_args.args[0]
        self.assertEqual(selected_signal["topic_id"], "7d33994f-dd40-4a7e-b78a-f8e2d94fb702")

    def test_openclaw_can_start_hive_task_from_fresh_short_id_reference(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        queue_rows = [
            {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
                "research_priority": 0.9,
                "active_claim_count": 0,
                "claims": [],
            }
        ]
        with mock.patch("apps.nulla_agent.session_hive_state", return_value={"pending_topic_ids": []}), mock.patch.object(
            agent.public_hive_bridge, "enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
        ), mock.patch(
            "apps.nulla_agent.research_topic_from_signal",
            return_value=AutonomousResearchResult(
                ok=True,
                status="completed",
                topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                claim_id="claim-12345678",
            ),
        ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "start #7d33994f",
                session_id_override="openclaw:fresh-short-hive-start",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        resp_lower = result["response"].lower()
        self.assertTrue(
            "started" in resp_lower and "research" in resp_lower,
            f"Expected 'started research' in response, got: {result['response'][:300]}"
        )
        selected_signal = research_topic_from_signal.call_args.args[0]
        self.assertEqual(selected_signal["topic_id"], "7d33994f-dd40-4a7e-b78a-f8e2d94fb702")

    def test_openclaw_natural_hive_pull_phrase_stays_in_hive_lane(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        tracker_payload = {
            "stats": {"active_agents": 2},
            "topics": [
                {
                    "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                    "created_by_agent_id": "peer-1",
                    "title": "Agent Commons: better human-visible watcher and task-flow UX",
                    "status": "researching",
                }
            ],
            "recent_posts": [],
        }
        agent.hive_activity_tracker = mock.Mock()
        agent.hive_activity_tracker.maybe_handle_command_details.return_value = (
            True,
            {
                "response_text": "Available Hive tasks right now (1 total):\n- [researching] Agent Commons: better human-visible watcher and task-flow UX (#7d33994f)\nIf you want, I can start one. Just point at the task name or short `#id`.",
                "command_kind": "task_list",
                "topics": tracker_payload["topics"],
                "online_agents": [],
                "watcher_status": "ok",
            },
        )
        agent.hive_activity_tracker.build_chat_footer.return_value = ""

        with mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
            "apps.nulla_agent.should_attempt_tool_intent",
            side_effect=AssertionError("tool intent lane should not run"),
        ):
            result = agent.run_once(
                "pull the hive task and lets do one?",
                session_id_override="openclaw:natural-hive-pull",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue("hive task" in result["response"].lower() or "agent commons" in result["response"].lower())
        self.assertNotIn("invalid tool payload", result["response"].lower())

    def test_openclaw_tool_failure_hides_raw_missing_intent_text(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        execution = ToolIntentExecution(
            handled=True,
            ok=False,
            status="missing_intent",
            response_text="I won't fake it: the model returned an invalid tool payload with no intent name.",
            user_safe_response_text="I couldn't map that cleanly to a real action.",
            mode="tool_failed",
            tool_name="unknown",
        )
        with mock.patch(
            "apps.nulla_agent.session_hive_state",
            return_value={"pending_topic_ids": ["topic-1"], "interaction_payload": {"shown_topic_ids": ["topic-1"]}},
        ):
            response = agent._tool_failure_user_message(
                execution=execution,
                effective_input="pull the hive task and lets do one?",
                session_id="openclaw:tool-failure-safe",
            )

        self.assertNotIn("invalid tool payload", response.lower())
        self.assertNotIn("i won't fake it", response.lower())
        self.assertIn("want me to list them again", response.lower())

    def test_openclaw_can_confirm_short_followup_from_recent_history_when_session_state_is_empty(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        queue_rows = [
            {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
                "research_priority": 0.9,
                "active_claim_count": 0,
                "claims": [],
            }
        ]
        with mock.patch("apps.nulla_agent.session_hive_state", return_value={"pending_topic_ids": []}), mock.patch.object(
            agent.public_hive_bridge, "enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
        ), mock.patch(
            "apps.nulla_agent.research_topic_from_signal",
            return_value=AutonomousResearchResult(
                ok=True,
                status="completed",
                topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                claim_id="claim-12345678",
            ),
        ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "OK let's go!",
                session_id_override="openclaw:history-hive",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "conversation_history": [
                        {
                            "role": "assistant",
                            "content": (
                                "Available Hive tasks right now (1 total):\n"
                                "- [researching] Agent Commons: better human-visible watcher and task-flow UX (#7d33994f)"
                            ),
                        }
                    ],
                },
            )

        self.assertIn("started hive research on", result["response"].lower())
        selected_signal = research_topic_from_signal.call_args.args[0]
        self.assertEqual(selected_signal["topic_id"], "7d33994f-dd40-4a7e-b78a-f8e2d94fb702")

    def test_openclaw_explicit_hive_task_creation_uses_fast_path(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "lets create new task in hive? name it: Improving UX-Self learning from chat, "
            "building heuristics on human interactions, preserving it in pure compressed formats "
            "for best and fastest future re-use"
        )
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("tool intent model should not run"))  # type: ignore[assignment]
        agent.context_loader.load = mock.Mock(side_effect=AssertionError("context loader should not run"))  # type: ignore[assignment]

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            return_value={
                "ok": True,
                "status": "created",
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            },
        ), mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ):
            result = agent.run_once(
                request_text,
                session_id_override="openclaw:create-hive-task",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", result["response"])
        self.assertIn("Confirm?", result["response"])
        self.assertNotIn("invalid tool payload", result["response"].lower())

    def test_openclaw_hive_task_prompt_parses_task_and_goal_sections(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "add this to the Hive mind for research and start working on it. "
            "Task: Research and design a local-first X/Twitter assistant for OpenClaw / NULLA Hive Mind. "
            "Goal: Design a serious AI-assisted social posting system that runs locally on the user's machine."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ):
            result = agent.run_once(
                request_text,
                session_id_override="openclaw:create-hive-task-task-goal",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", result["response"])
        self.assertIn(
            "Research and design a local-first",
            result["response"],
        )
        self.assertIn(
            "OpenClaw / NULLA Hive Mind",
            result["response"],
        )

    def test_openclaw_can_recover_hive_create_from_history_and_start_work(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        original_request = (
            "OK, here's a long but good script for you - add this to the Hive mind for research and start working on it. "
            "Task: Research and design a local-first X/Twitter assistant for OpenClaw / NULLA Hive Mind. "
            "Goal: Design a serious AI-assisted social posting system that runs locally on the user's machine."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            return_value={
                "ok": True,
                "status": "created",
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            },
        ) as create_public_topic, mock.patch(
            "apps.nulla_agent.research_topic_from_signal",
            return_value=AutonomousResearchResult(
                ok=True,
                status="completed",
                topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                claim_id="claim-12345678",
            ),
        ) as research_topic_from_signal, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ), mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "proceed, max effort!",
                session_id_override="openclaw:recover-create-history",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "conversation_history": [
                        {"role": "user", "content": original_request},
                        {
                            "role": "assistant",
                            "content": "Proceeding with maximum effort to research and design the requested local-first X/Twitter assistant.",
                        },
                    ],
                },
            )

        self.assertIn("Created Hive task", result["response"])
        self.assertIn("Started Hive research on", result["response"])
        self.assertEqual(
            create_public_topic.call_args.kwargs["title"],
            "Research and design a local-first X/Twitter assistant for OpenClaw / NULLA Hive Mind",
        )
        self.assertEqual(
            research_topic_from_signal.call_args.args[0]["topic_id"],
            "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
        )

    def test_openclaw_hive_task_preview_redacts_private_fields(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "create new task in hive. "
            "Task: Review local-first X assistant rollout. "
            "Goal: Compare current design notes from /tmp/mock-private-notes "
            "and email researcher@example.test before proposing the public-safe architecture."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            return_value={
                "ok": True,
                "status": "created",
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            },
        ) as create_public_topic, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ):
            preview = agent.run_once(
                request_text,
                session_id_override="openclaw:hive-redaction-preview",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "yes",
                session_id_override="openclaw:hive-redaction-preview",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", preview["response"])
        self.assertIn("Safety: I redacted private-looking fields", preview["response"])
        self.assertIn("Created Hive task", confirm["response"])
        self.assertNotIn("/tmp/mock-private-notes", create_public_topic.call_args.kwargs["summary"])
        self.assertNotIn("researcher@example.test", create_public_topic.call_args.kwargs["summary"])
        self.assertIn("<path>", create_public_topic.call_args.kwargs["summary"])
        self.assertIn("<email>", create_public_topic.call_args.kwargs["summary"])

    def test_openclaw_nullabook_profile_setup_uses_raw_handle_and_strips_bio_prefix(self) -> None:
        self._clear_nullabook_state()
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        peer_id = "ab" * 32
        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.nullabook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_nullabook_profile",
            return_value={"ok": True},
        ):
            prompt = agent.run_once(
                "ok make a profile first, do you know if I can add emojis next to the name? or text only?",
                session_id_override="openclaw:nullabook-profile-setup",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
            handle_turn = agent.run_once(
                "ok setup this name sls_0x",
                session_id_override="openclaw:nullabook-profile-setup",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
            bio_turn = agent.run_once(
                "bio: Chaos-typed systems founder & product engineer with bad typing discipline",
                session_id_override="openclaw:nullabook-profile-setup",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        from core.nullabook_identity import get_profile_by_handle

        profile = get_profile_by_handle("sls_0x")
        self.assertIsNotNone(profile)
        self.assertIn("Handles are text-only", prompt["response"])
        self.assertIn("emoji", prompt["response"].lower())
        self.assertIn("Registered as **sls_0x** on NullaBook!", handle_turn["response"])
        self.assertNotIn("Context subject", handle_turn["response"])
        self.assertIn(
            "Bio: Chaos-typed systems founder & product engineer with bad typing discipline",
            bio_turn["response"],
        )
        self.assertNotIn("Bio: bio:", bio_turn["response"])
        self.assertEqual(
            getattr(profile, "bio", ""),
            "Chaos-typed systems founder & product engineer with bad typing discipline",
        )

    def test_openclaw_nullabook_post_keeps_raw_content_and_is_visible_locally(self) -> None:
        self._clear_nullabook_state()
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.nullabook_identity import register_nullabook_account
        from storage.nullabook_store import list_feed, list_user_posts

        peer_id = "cd" * 32
        register_nullabook_account("sls_0x", peer_id=peer_id)
        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.nullabook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_nullabook_post",
            return_value={"ok": True},
        ):
            result = agent.run_once(
                "post to NullaBook: Hello everyone, this is not automated post - only testing :P",
                session_id_override="openclaw:nullabook-post-raw",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        feed_posts = list_feed(limit=5)
        user_posts = list_user_posts("sls_0x", limit=5)
        self.assertIn("Posted to NullaBook as **sls_0x**", result["response"])
        self.assertIn("only testing :P", result["response"])
        self.assertNotIn("only testing: P", result["response"])
        self.assertNotIn("Context subject", result["response"])
        self.assertEqual(user_posts[0].content, "Hello everyone, this is not automated post - only testing :P")
        self.assertTrue(any(post.content == user_posts[0].content for post in feed_posts))

    def test_openclaw_twitter_handle_update_uses_actual_requested_handle(self) -> None:
        self._clear_nullabook_state()
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.nullabook_identity import get_profile, register_nullabook_account

        peer_id = "ef" * 32
        register_nullabook_account("sls_0x", peer_id=peer_id)
        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.nullabook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_nullabook_profile",
            return_value={"ok": True},
        ):
            result = agent.run_once(
                "update my twitter handle in nullabook profile to sls_0x",
                session_id_override="openclaw:nullabook-twitter-handle",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        profile = get_profile(peer_id)
        self.assertIn("Twitter/X handle set to **@sls_0x**", result["response"])
        self.assertNotIn("@in", result["response"])
        self.assertEqual(getattr(profile, "twitter_handle", ""), "sls_0x")

    def test_openclaw_hive_task_preview_beats_twitter_route_and_stays_clean(self) -> None:
        self._clear_nullabook_state()
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.nullabook_identity import register_nullabook_account

        register_nullabook_account("sls_0x", peer_id="01" * 32)
        request_text = (
            "create hive mind task: Task: Research and design a local-first X/Twitter assistant for OpenClaw / NULLA Hive Mind. "
            "Goal: Design a serious AI-assisted social posting system that runs locally on the user's machine."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.hive_activity_tracker,
            "build_chat_footer",
            return_value="Hive heartbeat noise",
        ):
            result = agent.run_once(
                request_text,
                session_id_override="openclaw:create-hive-task-clean-preview",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", result["response"])
        self.assertIn(
            "**Research and design a local-first X/Twitter assistant for OpenClaw / NULLA Hive Mind**",
            result["response"],
        )
        self.assertNotIn("**Task:", result["response"])
        self.assertNotIn("Twitter/X handle set", result["response"])
        self.assertNotIn("Hive heartbeat noise", result["response"])

    def test_openclaw_hive_create_confirm_beats_stale_active_task_state(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        session_id = "openclaw:hive-create-confirm-priority"
        set_hive_interaction_state(
            session_id,
            mode="hive_task_active",
            payload={
                "active_topic_id": "213070b6-29cb-4420-97bc-51ab8d9314d4",
                "active_title": "Mind task for group research",
                "claim_id": "stale-claim",
            },
        )
        request_text = (
            "create new task in hive. "
            "Task: Review local-first X assistant rollout. "
            "Goal: Compare current design notes from /tmp/mock-private-notes "
            "and email researcher@example.test before proposing the public-safe architecture."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            return_value={
                "ok": True,
                "status": "created",
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            },
        ) as create_public_topic, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ):
            preview = agent.run_once(
                request_text,
                session_id_override=session_id,
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "yes",
                session_id_override=session_id,
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", preview["response"])
        self.assertIn("Created Hive task", confirm["response"])
        self.assertNotIn("Mind task for group research", confirm["response"])
        self.assertEqual(
            create_public_topic.call_args.kwargs["title"],
            "Review local-first X assistant rollout",
        )

    def test_openclaw_hive_create_prompt_with_credit_language_beats_credit_status_fast_path(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "create new task in hive. "
            "Task: Credit settlement audit trail for Hive results. "
            "Goal: Make sure credits escrow cleanly, receipts are visible, and partial payouts stay auditable."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent,
            "_check_hive_duplicate",
            return_value=None,
        ):
            result = agent.run_once(
                request_text,
                session_id_override="openclaw:hive-create-credit-language",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", result["response"])
        self.assertIn("Estimated reward pool:", result["response"])
        self.assertNotIn("You currently have", result["response"])

    def test_redact_text_keeps_x_twitter_label_but_redacts_real_paths(self) -> None:
        self.assertEqual(redact_text("X/Twitter assistant"), "X/Twitter assistant")
        self.assertEqual(redact_text("/tmp/mock-private-notes"), "<path>")
        self.assertEqual(redact_text("~/mock-private-notes"), "<path>")

    def test_openclaw_hive_review_queue_lists_pending_items(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge,
            "list_public_review_queue",
            return_value=[
                {
                    "object_type": "post",
                    "object_id": "post-1",
                    "preview": "Low-trust social lead that still needs bounded review.",
                    "moderation_state": "review_required",
                    "review_summary": {
                        "current_state": "review_required",
                        "total_reviews": 1,
                    },
                }
            ],
        ):
            result = agent.run_once(
                "check hive review queue",
                session_id_override="openclaw:hive-review-queue",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Hive review queue:", result["response"])
        self.assertIn("post-1", result["response"])
        self.assertIn("review_required", result["response"])

    def test_openclaw_hive_review_action_submits_moderation_decision(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "submit_public_moderation_review",
            return_value={"ok": True, "current_state": "approved", "quorum_reached": True},
        ) as submit_review:
            result = agent.run_once(
                "approve post post-1",
                session_id_override="openclaw:hive-review-action",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Submitted Hive moderation review for post `post-1`", result["response"])
        self.assertIn("Current state `approved`", result["response"])
        self.assertIn("Review quorum is reached", result["response"])
        self.assertEqual(submit_review.call_args.kwargs["decision"], "approve")
        self.assertEqual(submit_review.call_args.kwargs["object_type"], "post")

    def test_openclaw_hive_cleanup_closes_disposable_smoke_topics(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "list_public_topics",
            return_value=[
                {
                    "topic_id": "topic-smoke-1",
                    "title": "[NULLA_SMOKE] Disposable cleanup topic",
                    "summary": "Temporary smoke artifact for cleanup verification.",
                    "topic_tags": ["smoke"],
                    "status": "researching",
                },
                {
                    "topic_id": "topic-real-1",
                    "title": "Real public task",
                    "summary": "Legit research topic that must stay open.",
                    "topic_tags": ["research"],
                    "status": "open",
                },
            ],
        ), mock.patch.object(
            agent.public_hive_bridge,
            "update_public_topic_status",
            return_value={"ok": True, "status": "closed"},
        ) as close_topic:
            result = agent.run_once(
                "clean up hive smoke topics",
                session_id_override="openclaw:hive-cleanup-smoke",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Closed 1 disposable Hive smoke topic", result["response"])
        self.assertEqual(close_topic.call_count, 1)
        self.assertEqual(close_topic.call_args.kwargs["topic_id"], "topic-smoke-1")

    def test_openclaw_hive_task_create_blocks_raw_transcript_dump(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "create new task in hive from this chat:\n"
            "NULLA\n13:16\n"
            "I'm NULLA. New session is clean and I'm ready.\n"
            "You\n13:17\n"
            "my email is researcher@example.test and here is the full private backstory\n"
            "U\n13:18\n"
            "/new\n"
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(agent.public_hive_bridge, "create_public_topic") as create_public_topic:
            result = agent.run_once(
                request_text,
                session_id_override="openclaw:hive-transcript-block",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("raw chat log/transcript", result["response"].lower())
        self.assertNotIn("Ready to post this to the public Hive", result["response"])
        create_public_topic.assert_not_called()

    def test_builder_style_request_uses_curiosity_evidence_same_turn(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("tool intent model should not run"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="builder-test",
                used_model=True,
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Based on official documentation and GitHub examples, here is the architecture for a Telegram bot.",
                confidence=0.7,
                trust_score=0.75,
                validation_state="valid",
            )
        )
        agent.context_loader.load = mock.Mock(  # type: ignore[assignment]
            return_value=SimpleNamespace(
                local_candidates=[],
                swarm_metadata=[],
                retrieval_confidence_score=0.12,
                context_snippets=lambda: [],
                assembled_context=lambda: "",
                report=SimpleNamespace(
                    to_dict=lambda: {"retrieval_confidence": "low"},
                    retrieval_confidence="low",
                    total_tokens_used=lambda: 0,
                ),
            )
        )

        snippets = [
            {
                "summary": "Telegram Bot API docs are the canonical source for auth, update delivery, and rate-limit constraints.",
                "source_label": "duckduckgo.com",
                "origin_domain": "core.telegram.org",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
            },
            {
                "summary": "Well-maintained GitHub bot repos are useful for handler structure and deployment examples once the API contract is fixed.",
                "source_label": "duckduckgo.com",
                "origin_domain": "github.com",
                "result_title": "GitHub examples",
                "result_url": "https://github.com/example/repo",
            },
        ]

        with mock.patch("retrieval.web_adapter.WebAdapter.search_query", return_value=snippets), mock.patch(
            "apps.nulla_agent.orchestrate_parent_task",
            return_value=None,
        ), mock.patch("apps.nulla_agent.request_relevant_holders", return_value=[]), mock.patch(
            "apps.nulla_agent.dispatch_query_shard",
            return_value=None,
        ), mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "Help me build a next gen Telegram bot from official docs and good GitHub repos.",
                source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        lowered = result["response"].lower()
        self.assertTrue("official documentation" in lowered or "official docs" in lowered or "telegram" in lowered)
        self.assertTrue(result["response"])
        agent.memory_router.resolve_tool_intent.assert_not_called()

    def test_openclaw_explicit_hive_task_creation_fails_honestly_without_write_auth(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=False
        ), mock.patch.object(agent.public_hive_bridge, "create_public_topic") as create_public_topic:
            result = agent.run_once(
                "create new task in hive: Better human-visible watcher UX",
                session_id_override="openclaw:create-hive-task-auth",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("public Hive auth is not configured for writes", result["response"])
        create_public_topic.assert_not_called()

    def test_openclaw_hive_task_confirm_reports_invalid_auth_cleanly(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "add this to the Hive mind for research and start working on it. "
            "Task: Research and design a local-first X/Twitter assistant for OpenClaw / NULLA Hive Mind. "
            "Goal: Design a serious AI-assisted social posting system that runs locally on the user's machine."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            side_effect=ValueError("Unauthorized write request."),
        ):
            preview = agent.run_once(
                request_text,
                session_id_override="openclaw:create-hive-task-invalid-auth",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "yes",
                session_id_override="openclaw:create-hive-task-invalid-auth",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", preview["response"])
        self.assertIn("live Hive rejected this runtime's write auth", confirm["response"])
        self.assertNotIn("Runtime error", confirm["response"])

    def test_openclaw_hive_status_followup_uses_watched_topic_context(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        packet = {
            "topic": {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
            },
            "execution_state": {
                "topic_status": "researching",
                "execution_state": "claimed",
                "active_claim_count": 1,
                "artifact_count": 2,
            },
            "counts": {
                "post_count": 3,
                "active_claim_count": 1,
            },
            "posts": [
                {
                    "post_kind": "result",
                    "body": "First bounded pass landed but more evidence is still needed before solve promotion.",
                }
            ],
        }
        with mock.patch("apps.nulla_agent.session_hive_state", return_value={"watched_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"]}), mock.patch.object(
            agent.public_hive_bridge, "enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "get_public_research_packet", return_value=packet
        ), mock.patch.object(
            agent.hive_activity_tracker,
            "build_chat_footer",
            return_value="Hive:\nnoisy footer",
        ):
            result = agent.run_once(
                "ok is research complete?",
                session_id_override="openclaw:status-followup",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        lowered = result["response"].lower()
        self.assertTrue(
            "hive" in lowered or "research" in lowered or "status" in lowered or "task" in lowered or "complete" in lowered,
            f"Expected hive/research context in response, got: {result['response'][:300]}"
        )
        self.assertNotIn("noisy footer", result["response"])

    def test_openclaw_hive_status_followup_can_resolve_topic_from_recent_history(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        packet = {
            "topic": {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "solved",
            },
            "execution_state": {
                "topic_status": "solved",
                "execution_state": "solved",
                "active_claim_count": 0,
                "artifact_count": 2,
            },
            "counts": {
                "post_count": 4,
                "active_claim_count": 0,
            },
            "posts": [
                {
                    "post_kind": "result",
                    "body": "Solve threshold cleared after the second bounded pass.",
                }
            ],
        }
        with mock.patch("apps.nulla_agent.session_hive_state", return_value={"watched_topic_ids": []}), mock.patch.object(
            agent.public_hive_bridge, "enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "get_public_research_packet", return_value=packet
        ) as get_public_research_packet, mock.patch.object(
            agent.public_hive_bridge,
            "list_public_topics",
            return_value=[
                {
                    "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                    "title": "Agent Commons: better human-visible watcher and task-flow UX",
                    "status": "solved",
                }
            ],
        ):
            result = agent.run_once(
                "did it finish?",
                session_id_override="openclaw:status-history",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "conversation_history": [
                        {
                            "role": "assistant",
                            "content": "Started Hive research on `Agent Commons: better human-visible watcher and task-flow UX` (#7d33994f).",
                        }
                    ],
                },
            )

        lowered = result["response"].lower()
        self.assertTrue("hive status" in lowered or "solved" in lowered or "completed" in lowered or "finished" in lowered)
        self.assertTrue(
            "agent commons" in lowered or "watcher" in lowered or "task" in lowered or "topic" in lowered,
            f"Expected task context in response, got: {result['response'][:300]}"
        )
        get_public_research_packet.assert_called_once_with("7d33994f-dd40-4a7e-b78a-f8e2d94fb702")

    def test_openclaw_hive_status_followup_reports_partial_state(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        packet = {
            "topic": {
                "topic_id": "5fd57ce7-dd40-4a7e-b78a-f8e2d94fb799",
                "title": "Human-visible Hive cleanup UX",
                "status": "partial",
            },
            "execution_state": {
                "topic_status": "partial",
                "execution_state": "partial",
                "active_claim_count": 0,
                "artifact_count": 1,
            },
            "counts": {
                "post_count": 2,
                "active_claim_count": 0,
            },
            "posts": [
                {
                    "post_kind": "summary",
                    "body": "Useful first pass landed, but more cleanup work is still needed.",
                }
            ],
        }
        with mock.patch("apps.nulla_agent.session_hive_state", return_value={"watched_topic_ids": []}), mock.patch.object(
            agent.public_hive_bridge, "enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "get_public_research_packet", return_value=packet
        ), mock.patch.object(
            agent.public_hive_bridge,
            "list_public_topics",
            return_value=[
                {
                    "topic_id": "5fd57ce7-dd40-4a7e-b78a-f8e2d94fb799",
                    "title": "Human-visible Hive cleanup UX",
                    "status": "partial",
                }
            ],
        ):
            result = agent.run_once(
                "did it finish?",
                session_id_override="openclaw:status-partial",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "conversation_history": [
                        {
                            "role": "assistant",
                            "content": "Started Hive research on `Human-visible Hive cleanup UX` (#5fd57ce7).",
                        }
                    ],
                },
            )

        lowered = result["response"].lower()
        self.assertIn("partial", lowered)
        self.assertIn("still needs follow-up", lowered)

    def test_openclaw_ambiguous_hive_selection_relists_real_tasks(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        queue_rows = [
            {
                "topic_id": "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
            },
            {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "NULLA Trading Learning Desk",
                "status": "researching",
            },
        ]
        with mock.patch("apps.nulla_agent.session_hive_state", return_value={"pending_topic_ids": [row["topic_id"] for row in queue_rows]}), mock.patch.object(
            agent.public_hive_bridge, "enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
        ), mock.patch.object(
            agent.hive_activity_tracker,
            "build_chat_footer",
            return_value="",
        ), mock.patch(
            "apps.nulla_agent.research_topic_from_signal"
        ) as research_topic_from_signal:
            result = agent.run_once(
                "ok review the problem",
                session_id_override="openclaw:ambiguous-hive-selection",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        research_topic_from_signal.assert_not_called()
        resp_lower = result["response"].lower()
        self.assertTrue(
            "hive" in resp_lower or "task" in resp_lower,
            f"Expected Hive task context in response, got: {result['response'][:300]}"
        )
        self.assertTrue(
            "watcher" in resp_lower or "ux" in resp_lower or "trading" in resp_lower,
            f"Expected task titles referenced in response, got: {result['response'][:300]}"
        )


if __name__ == "__main__":
    unittest.main()
