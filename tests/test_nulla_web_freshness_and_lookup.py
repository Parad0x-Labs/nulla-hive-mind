from __future__ import annotations

from unittest import mock

from core.curiosity_roamer import CuriosityResult
from core.memory_first_router import ModelExecutionDecision


def test_wants_fresh_info_detects_live_queries_and_ignores_builder_language(make_agent):
    agent = make_agent()

    assert agent._wants_fresh_info("latest telegram bot api updates", interpretation=mock.Mock(topic_hints=["telegram"]))
    assert agent._wants_fresh_info("weather in London today", interpretation=mock.Mock(topic_hints=["weather"]))
    assert agent._live_info_mode("latest qwen release notes", interpretation=mock.Mock(topic_hints=["web"])) == "fresh_lookup"
    assert agent._live_info_mode("build a telegram bot from docs and github", interpretation=mock.Mock(topic_hints=["telegram", "github"])) == ""


def test_latest_telegram_updates_trigger_planned_web_lookup(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("fresh lookup fast path should not load retrieval context")  # type: ignore[attr-defined]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(source="memory_hit", task_hash="fresh-web", used_model=False)
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


def test_weather_fast_path_uses_live_lookup_without_loading_context(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("weather fast path should not load context")  # type: ignore[attr-defined]

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
    assert "live weather results" in result["response"].lower()
    assert "bbc weather - london" in result["response"].lower()
    assert "hive:" not in result["response"].lower()


def test_empty_fresh_lookup_falls_back_to_full_research_path(make_agent, context_result_factory):
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
    assert "telegram bot api" in result["response"].lower()
