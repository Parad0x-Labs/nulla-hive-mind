from __future__ import annotations

from typing import Any

from core.task_router import (
    looks_like_explicit_lookup_request,
    looks_like_live_recency_lookup,
    looks_like_public_entity_lookup_request,
)

_CLOCK_AND_DATE_MARKERS = (
    "what day is it",
    "what day is today",
    "what is the day today",
    "today's date",
    "date today",
    "what time is it",
    "what's the time",
    "time now",
)

_WEATHER_MARKERS = (
    " weather ",
    " weather?",
    "weather ",
    " forecast",
    " temperature",
    " rain ",
    " rain?",
    " raining",
    " rainy",
    " snow ",
    " snow?",
    " snowing",
    " snowy",
    " wind ",
    " windy",
    " humidity",
    " humid ",
    " sunrise",
    " sunset",
    " wheather",
    " wheater",
    " whether today",
    " whether now",
    " whether in ",
)

_NEWS_MARKERS = (
    "latest news",
    "breaking news",
    "headlines",
    "headline",
    "news on",
    "news about",
    "what happened today",
    "what's the latest on",
    "what is the latest on",
    "whats the latest on",
    "latest on ",
    "latest about ",
)

_LIVE_LOOKUP_HINT_MARKERS = (
    "look up",
    "check online",
    "search online",
    "browse",
)

_FRESH_LOOKUP_MARKERS = (
    "release notes",
    "changelog",
    "latest update",
    "latest updates",
    "current version",
    "latest version",
    "status page",
    "current price",
    "price now",
    "price today",
    "price right now",
    "exchange rate",
    "how much is",
    "how much does",
    "worth right now",
    "worth today",
    "worth now",
    "market price",
    "stock price",
    "oil price",
    "gold price",
    "bitcoin price",
    "btc price",
    "eth price",
    "crypto price",
)

_LATEST_DOMAIN_MARKERS = (
    "api",
    "sdk",
    "library",
    "package",
    "release",
    "version",
    "bot",
    "telegram",
    "discord",
    "model",
    "framework",
    "price",
    "stock",
)


def live_info_mode(agent: Any, text: str, *, interpretation: Any) -> str:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return ""
    if agent._looks_like_builder_request(lowered):
        return ""
    if any(marker in lowered for marker in _CLOCK_AND_DATE_MARKERS):
        return ""

    lowered_padded = f" {lowered} "
    if any(marker in lowered_padded for marker in _WEATHER_MARKERS):
        return "weather"
    if any(marker in lowered for marker in _NEWS_MARKERS):
        return "news"
    if looks_like_live_recency_lookup(lowered):
        return "fresh_lookup"
    if looks_like_explicit_lookup_request(lowered) or looks_like_public_entity_lookup_request(lowered):
        return "fresh_lookup"
    if any(marker in lowered for marker in _LIVE_LOOKUP_HINT_MARKERS):
        return "fresh_lookup"
    if any(marker in lowered for marker in _FRESH_LOOKUP_MARKERS):
        return "fresh_lookup"
    if any(marker in lowered for marker in ("latest", "newest", "recent", "just released")) and any(
        marker in lowered for marker in _LATEST_DOMAIN_MARKERS
    ):
        return "fresh_lookup"

    hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
    if "weather" in hints:
        return "weather"
    if "news" in hints:
        return "news"
    if "web" in hints and agent._wants_fresh_info(lowered, interpretation=interpretation):
        return "fresh_lookup"
    return ""


def requires_ultra_fresh_insufficient_evidence(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered or not looks_like_live_recency_lookup(lowered):
        return False
    if not any(
        marker in lowered
        for marker in (
            " minute ago",
            " minutes ago",
            " just now",
            " just happened",
        )
    ):
        return False
    return any(
        marker in lowered
        for marker in (
            "what happened",
            "markets",
            "market",
            "headline",
            "headlines",
            "news",
            "global",
        )
    )


def ultra_fresh_insufficient_evidence_response(*, query: str) -> str:
    clean_query = " ".join(str(query or "").split()).strip() or "that"
    return (
        f"I can't verify `{clean_query}` with enough confidence at minute-by-minute resolution from this runtime. "
        "For claims about what happened just now or five minutes ago, I should treat the result as insufficient evidence unless a timestamped live source confirms it directly."
    )


def normalize_live_info_query(text: str, *, mode: str) -> str:
    clean = " ".join(str(text or "").split()).strip()
    lowered = clean.lower()
    if mode == "weather" and "forecast" not in lowered and "weather" in lowered:
        return f"{clean} forecast"
    if mode == "news" and "latest" not in lowered and "news" in lowered:
        return f"latest {clean}"
    return clean


def live_info_failure_text(*, query: str, mode: str) -> str:
    if mode == "weather":
        return f'I tried the live web lane for "{query}", but no current weather results came back.'
    if mode == "news":
        return f'I tried the live web lane for "{query}", but no current news results came back.'
    return f'I checked the live web lane for "{query}", but I could not ground a current answer confidently.'
