from __future__ import annotations

from typing import Any

from retrieval.web_adapter import WebAdapter


def live_info_search_notes(
    agent: Any,
    *,
    query: str,
    live_mode: str,
    interpretation: Any,
) -> list[dict[str, Any]]:
    topic_hints = [str(item).strip().lower() for item in getattr(interpretation, "topic_hints", []) or [] if str(item).strip()]
    if live_mode == "weather":
        return WebAdapter.search_query(
            query,
            limit=3,
            source_label="duckduckgo.com",
        )
    if live_mode == "news":
        return WebAdapter.search_query(
            query,
            limit=3,
            source_label="duckduckgo.com",
        )
    if live_mode == "fresh_lookup":
        quote_note = agent._try_live_quote_note(query)
        if quote_note:
            return [quote_note]
    return WebAdapter.planned_search_query(
        query,
        limit=3,
        task_class="research",
        topic_kind="general" if live_mode == "fresh_lookup" else None,
        topic_hints=topic_hints,
        source_label="duckduckgo.com",
    )


def try_live_quote_note(query: str) -> dict[str, Any] | None:
    try:
        from tools.web.web_research import lookup_live_quote

        quote = lookup_live_quote(query, timeout_s=8)
        if quote is None:
            return None
        return quote.to_note()
    except Exception:
        return None
