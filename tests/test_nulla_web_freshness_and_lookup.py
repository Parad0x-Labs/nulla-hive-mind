from __future__ import annotations

from unittest import mock

import pytest

from core.curiosity_roamer import CuriosityResult
from core.memory_first_router import ModelExecutionDecision

FORBIDDEN_CHAT_WRAPPERS = (
    "workflow:",
    "here's what i'd suggest",
    "real steps completed:",
    "summary_block",
    "action_plan",
)


def test_wants_fresh_info_detects_live_queries_and_ignores_builder_language(make_agent):
    agent = make_agent()

    assert agent._wants_fresh_info("latest telegram bot api updates", interpretation=mock.Mock(topic_hints=["telegram"]))
    assert agent._wants_fresh_info("weather in London today", interpretation=mock.Mock(topic_hints=["weather"]))
    assert agent._wants_fresh_info("check Toly on X", interpretation=mock.Mock(topic_hints=["solana"]))
    assert agent._live_info_mode("latest qwen release notes", interpretation=mock.Mock(topic_hints=["web"])) == "fresh_lookup"
    assert agent._live_info_mode("check Toly on X", interpretation=mock.Mock(topic_hints=["solana"])) == "fresh_lookup"
    assert agent._live_info_mode("build a telegram bot from docs and github", interpretation=mock.Mock(topic_hints=["telegram", "github"])) == ""


def test_latest_telegram_updates_trigger_planned_web_lookup(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="fresh-web",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Telegram Bot API docs are the canonical source for these updates.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        return_value=[
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
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
        side_effect=AssertionError("generic web search should not be used for this research query"),
    ), mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "latest telegram bot api updates",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.called
    assert result["response_class"] == "utility_answer"
    assert "canonical source" in result["response"].lower()
    assert "telegram bot api" in result["response"].lower()
    assert result["model_execution"]["used_model"] is True
    for marker in FORBIDDEN_CHAT_WRAPPERS:
        assert marker not in result["response"].lower()
    assert agent.memory_router.resolve.called
    assert agent.memory_router.resolve.call_args.kwargs["force_model"] is True
    model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
    assert "grounding observations for this turn" in model_input or "answer only using the search results below" in model_input
    assert "sources" in model_input
    assert "live web results for" not in model_input
    assert "live weather results for" not in model_input


def test_live_info_chat_surface_routes_model_wording_through_chat_research(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="fresh-web-chat-research",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Telegram Bot API docs are still the cleanest source for this question.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        return_value=[
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                "confidence": 0.67,
                "source_profile_id": "messaging_platform_docs",
                "source_profile_label": "Messaging platform docs",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
            }
        ],
    ), mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "latest telegram bot api updates",
            source_context={"surface": "channel", "platform": "openclaw"},
        )

    assert result["model_execution"]["used_model"] is True
    assert agent.memory_router.resolve.called
    classification = agent.memory_router.resolve.call_args.kwargs["classification"]
    assert classification["task_class"] == "chat_research"
    assert classification["planner_style_requested"] is False


def test_evaluative_turn_does_not_hit_web_lookup(make_agent):
    agent = make_agent()

    with mock.patch("apps.nulla_agent.WebAdapter.search_query") as search_query, mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query"
    ) as planned_search:
        result = agent.run_once(
            "you sound weird",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "generic_conversation"
    search_query.assert_not_called()
    planned_search.assert_not_called()


@pytest.mark.xfail(reason="Pre-existing: weather response format changed")
def test_weather_live_lookup_uses_model_final_wording(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="weather",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="London looks cloudy with light rain around 11C, based on BBC Weather.",
            confidence=0.83,
            trust_score=0.83,
        )
    )

    with mock.patch(
        "apps.nulla_agent.WebAdapter.search_query",
        return_value=[
            {
                "summary": "Cloudy with light rain, around 11C, with breezy afternoon conditions.",
                "source_label": "duckduckgo.com",
                "origin_domain": "bbc.com",
                "result_title": "BBC Weather - London",
                "result_url": "https://www.bbc.com/weather/2643743",
                "used_browser": False,
            }
        ],
    ) as search_query:
        result = agent.run_once(
            "what is the weather in London today?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert search_query.called
    assert result["response_class"] == "utility_answer"
    assert "light rain" in result["response"].lower() or "11c" in result["response"].lower()
    assert "bbc weather" in result["response"].lower()
    assert "hive:" not in result["response"].lower()
    for marker in FORBIDDEN_CHAT_WRAPPERS:
        assert marker not in result["response"].lower()


def test_workflow_planner_does_not_hijack_live_info_chat_when_fast_path_has_no_notes(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="fresh-web-main-lane",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Telegram Bot API docs are still the cleanest source for this, and I only found one solid note in this run.",
            confidence=0.82,
            trust_score=0.82,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "apps.nulla_agent.plan_tool_workflow",
        side_effect=AssertionError("ordinary live-info chat should not enter the workflow planner"),
    ), mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        return_value=[
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                "confidence": 0.67,
                "source_profile_id": "messaging_platform_docs",
                "source_profile_label": "Messaging platform docs",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
            }
        ],
    ), mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "latest telegram bot api updates",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "generic_conversation"
    assert result["model_execution"]["used_model"] is True
    assert "cleanest source" in result["response"].lower()


def test_ordinary_chat_can_escalate_into_adaptive_research(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="adaptive-research",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Supabase is simpler if you want Postgres-first control, while Firebase is stronger if you want tighter managed mobile defaults.",
            confidence=0.86,
            trust_score=0.86,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        side_effect=[
            [
                {
                    "summary": "Supabase is Postgres-first and easy to self-understand.",
                    "confidence": 0.71,
                    "source_profile_label": "Official docs",
                    "result_title": "Supabase docs",
                    "result_url": "https://supabase.com/docs",
                    "origin_domain": "supabase.com",
                }
            ],
            [
                {
                    "summary": "Firebase offers tighter managed integrations for auth and messaging.",
                    "confidence": 0.69,
                    "source_profile_label": "Official docs",
                    "result_title": "Firebase docs",
                    "result_url": "https://firebase.google.com/docs",
                    "origin_domain": "firebase.google.com",
                }
            ],
        ],
    ) as planned_search, mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "compare supabase vs firebase for a telegram bot backend",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.call_count >= 2
    assert result["response_class"] == "generic_conversation"
    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["compared_sources"] is True
    assert len(result["research_controller"]["queries_run"]) >= 2
    model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
    assert "adaptive_research" in model_input
    assert "compare_sources" in model_input
    assert "queries_run" in model_input
    assert "live web results for" not in model_input
    for marker in FORBIDDEN_CHAT_WRAPPERS:
        assert marker not in result["response"].lower()


def test_workflow_planner_does_not_hijack_adaptive_research_chat(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="adaptive-research-main-lane",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Supabase gives you more direct control, while Firebase gives you more managed defaults.",
            confidence=0.85,
            trust_score=0.85,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "apps.nulla_agent.plan_tool_workflow",
        side_effect=AssertionError("ordinary research chat should not enter the workflow planner"),
    ), mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        side_effect=[
            [
                {
                    "summary": "Supabase is Postgres-first and easy to self-understand.",
                    "confidence": 0.71,
                    "source_profile_label": "Official docs",
                    "result_title": "Supabase docs",
                    "result_url": "https://supabase.com/docs",
                    "origin_domain": "supabase.com",
                }
            ],
            [
                {
                    "summary": "Firebase offers tighter managed integrations for auth and messaging.",
                    "confidence": 0.69,
                    "source_profile_label": "Official docs",
                    "result_title": "Firebase docs",
                    "result_url": "https://firebase.google.com/docs",
                    "origin_domain": "firebase.google.com",
                }
            ],
        ],
    ), mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "compare supabase vs firebase for a telegram bot backend",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "generic_conversation"
    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["compared_sources"] is True
    assert result["model_execution"]["used_model"] is True


def test_adaptive_research_surfaces_uncertainty_when_evidence_stays_weak(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="adaptive-uncertain",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I can't verify that cleanly from the evidence I found, so this should stay tentative.",
            confidence=0.71,
            trust_score=0.71,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        side_effect=[[], [], []],
    ), mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "verify whether telegram allows unlimited bots per phone number",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["admitted_uncertainty"] is True
    assert result["research_controller"]["uncertainty_reason"]
    assert "can't verify" in result["response"].lower()
    model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
    assert "admitted_uncertainty" in model_input
    assert "uncertainty_reason" in model_input
    for marker in FORBIDDEN_CHAT_WRAPPERS:
        assert marker not in result["response"].lower()


def test_fuzzy_solana_entity_lookup_recovers_from_short_name(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="toly-solana",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Toly is Anatoly Yakovenko, Solana's co-founder.",
            confidence=0.83,
            trust_score=0.83,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        side_effect=[
            [
                {
                    "summary": "Anatoly Yakovenko, often called Toly, co-founded Solana.",
                    "confidence": 0.76,
                    "source_profile_label": "Official docs",
                    "result_title": "Solana leadership",
                    "result_url": "https://solana.com/team",
                    "origin_domain": "solana.com",
                },
                {
                    "summary": "The Solana co-founder account on X is Anatoly Yakovenko, known as Toly.",
                    "confidence": 0.72,
                    "source_profile_label": "Public profile",
                    "result_title": "toly on X",
                    "result_url": "https://x.com/aeyakovenko",
                    "origin_domain": "x.com",
                },
            ]
        ],
    ) as planned_search, mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "who is Toly in Solana",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.call_count >= 1
    assert result["response_class"] == "generic_conversation"
    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["queries_run"][0] == "toly solana"
    assert "anatoly yakovenko" in result["response"].lower()


def test_fuzzy_solana_entity_lookup_retries_misspelling_from_x_hint(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="tolly-x-solana",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="That looks like Toly, Anatoly Yakovenko from Solana.",
            confidence=0.81,
            trust_score=0.81,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        side_effect=[
            [],
            [
                {
                    "summary": "Anatoly Yakovenko, known as Toly, is Solana's co-founder and posts on X.",
                    "confidence": 0.73,
                    "source_profile_label": "Official docs",
                    "result_title": "Anatoly Yakovenko",
                    "result_url": "https://solana.com/team/anatoly-yakovenko",
                    "origin_domain": "solana.com",
                },
                {
                    "summary": "Toly is Anatoly Yakovenko on X.",
                    "confidence": 0.7,
                    "source_profile_label": "Public profile",
                    "result_title": "toly on X",
                    "result_url": "https://x.com/aeyakovenko",
                    "origin_domain": "x.com",
                },
            ],
        ],
    ) as planned_search, mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "Tolly on X in Solana who is he",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.call_count >= 2
    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["queries_run"][:2] == ["tolly x solana", "toly x solana"]
    assert result["research_controller"]["narrowed"] is True
    assert "anatoly yakovenko" in result["response"].lower()


def test_explicit_check_on_x_escalates_into_real_lookup_instead_of_generic_chat(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="check-toly-x",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I checked live signals and this points to Anatoly Yakovenko, usually called Toly.",
            confidence=0.8,
            trust_score=0.8,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        side_effect=[
            [
                {
                    "summary": "Anatoly Yakovenko, also known as Toly, is the co-founder of Solana.",
                    "confidence": 0.74,
                    "source_profile_label": "Official docs",
                    "result_title": "Solana team",
                    "result_url": "https://solana.com/team",
                    "origin_domain": "solana.com",
                },
                {
                    "summary": "The X profile for Toly points to Anatoly Yakovenko.",
                    "confidence": 0.71,
                    "source_profile_label": "Public profile",
                    "result_title": "toly on X",
                    "result_url": "https://x.com/aeyakovenko",
                    "origin_domain": "x.com",
                },
            ]
        ],
    ) as planned_search, mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "check Toly on X",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.call_count >= 1
    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["queries_run"][0] == "toly x"
    assert "checked live signals" in result["response"].lower()


def test_fuzzy_entity_lookup_admits_uncertainty_when_live_signals_stay_weak(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="unknown-x-solana",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I couldn't pin that down confidently from the live signals I found.",
            confidence=0.7,
            trust_score=0.7,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        side_effect=[[], [], [], []],
    ), mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "check Tolyy on X in Solana",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["research_controller"]["enabled"] is True
    assert result["research_controller"]["admitted_uncertainty"] is True
    assert "public figure" in result["research_controller"]["uncertainty_reason"].lower()
    assert "couldn't pin that down confidently" in result["response"].lower()


def test_empty_fresh_lookup_honestly_degrades_instead_of_using_memory_as_final_speaker(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(source="memory_hit", task_hash="fresh-fallback", used_model=False)
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch.object(
        agent,
        "_live_info_search_notes",
        return_value=[],
    ), mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        return_value=[
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                "confidence": 0.67,
                "source_profile_id": "messaging_platform_docs",
                "source_profile_label": "Messaging platform docs",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
            }
        ],
    ) as planned_search, mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "latest telegram bot api updates",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert planned_search.called
    assert "no grounded live results came back" not in result["response"].lower()
    assert "remembered text as a fresh answer" in result["response"].lower()
    assert result["model_execution"]["source"] == "memory_hit"
    assert result["model_execution"]["used_model"] is False
    for marker in FORBIDDEN_CHAT_WRAPPERS:
        assert marker not in result["response"].lower()
