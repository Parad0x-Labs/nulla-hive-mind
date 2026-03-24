from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.agent_runtime import (
    fast_live_info,
    fast_live_info_mode_policy,
    fast_live_info_price,
    fast_live_info_rendering,
    fast_live_info_router,
    fast_live_info_runtime,
    fast_live_info_search,
)


def _build_agent() -> SimpleNamespace:
    return SimpleNamespace(
        _try_live_quote_note=lambda _query: None,
        _looks_like_builder_request=lambda _text: False,
        _wants_fresh_info=lambda _text, interpretation: True,
    )


def test_fast_live_info_facade_reexports_split_modules() -> None:
    assert fast_live_info.maybe_handle_live_info_fast_path is fast_live_info_router.maybe_handle_live_info_fast_path
    assert fast_live_info_router.maybe_handle_live_info_fast_path is fast_live_info_runtime.maybe_handle_live_info_fast_path
    assert fast_live_info.live_info_mode is fast_live_info_router.live_info_mode
    assert fast_live_info_router.live_info_mode is fast_live_info_mode_policy.live_info_mode
    assert fast_live_info.live_info_search_notes is fast_live_info_search.live_info_search_notes
    assert fast_live_info.try_live_quote_note is fast_live_info_search.try_live_quote_note
    assert fast_live_info.render_live_info_response is fast_live_info_rendering.render_live_info_response
    assert fast_live_info.render_weather_response is fast_live_info_rendering.render_weather_response
    assert fast_live_info.render_news_response is fast_live_info_rendering.render_news_response
    assert fast_live_info.unresolved_price_lookup_response is fast_live_info_price.unresolved_price_lookup_response


def test_live_info_search_notes_prefers_live_quote_notes_for_fresh_lookup() -> None:
    agent = _build_agent()
    interpretation = SimpleNamespace(topic_hints=["web"])

    agent._try_live_quote_note = mock.Mock(  # type: ignore[assignment]
        return_value={"live_quote": {"asset_key": "btc", "asset_name": "BTC", "value": 1.0}},
    )

    with mock.patch(
        "core.agent_runtime.fast_live_info_search.WebAdapter.planned_search_query",
        side_effect=AssertionError("planned search should not run when a quote note is available"),
    ):
        notes = fast_live_info_search.live_info_search_notes(
            agent,
            query="BTC price now",
            live_mode="fresh_lookup",
            interpretation=interpretation,
        )

    assert notes == [{"live_quote": {"asset_key": "btc", "asset_name": "BTC", "value": 1.0}}]


def test_live_info_rendering_and_price_helpers_stay_grounded() -> None:
    notes = [
        {
            "result_title": "Example result",
            "origin_domain": "example.com",
            "summary": "Fresh update from the example domain.",
            "result_url": "https://example.com/result",
        }
    ]

    assert fast_live_info_rendering.render_weather_response(query="weather in London", notes=notes).startswith(
        "Weather in London:"
    )
    assert "Latest coverage on" in fast_live_info_rendering.render_news_response(
        query="latest news on London",
        notes=notes,
    )
    assert fast_live_info_price.extract_price_lookup_subject("What is the price of BTC now?") == "BTC"
