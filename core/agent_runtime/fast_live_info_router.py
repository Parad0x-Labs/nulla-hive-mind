from __future__ import annotations

from typing import Any

from core import audit_logger, policy_engine
from core.identity_manager import load_active_persona
from core.task_router import (
    looks_like_explicit_lookup_request,
    looks_like_live_recency_lookup,
    looks_like_public_entity_lookup_request,
)


def maybe_handle_live_info_fast_path(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    interpretation: Any,
    response_class: Any,
) -> dict[str, Any] | None:
    live_mode = agent._live_info_mode(user_input, interpretation=interpretation)
    if not live_mode:
        return None
    if not policy_engine.allow_web_fallback():
        disabled_response = (
            "Live web lookup is disabled on this runtime, so I can't verify current prices, "
            "weather, or latest-news requests honestly."
        )
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=disabled_response,
            confidence=0.82,
            source_context=source_context,
            reason="live_info_fast_path",
        )

    query = agent._normalize_live_info_query(user_input, mode=live_mode)
    if agent._requires_ultra_fresh_insufficient_evidence(user_input):
        response = agent._ultra_fresh_insufficient_evidence_response(query=query)
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.9,
            source_context=source_context,
            reason="live_info_insufficient_evidence",
        )
    try:
        notes = agent._live_info_search_notes(
            query=query,
            live_mode=live_mode,
            interpretation=interpretation,
        )
        if not notes and query != str(user_input or "").strip():
            notes = agent._live_info_search_notes(
                query=str(user_input or "").strip(),
                live_mode=live_mode,
                interpretation=interpretation,
            )
    except Exception as exc:
        audit_logger.log(
            "agent_live_info_fast_path_error",
            target_id=session_id,
            target_type="session",
            details={"error": str(exc), "query": query, "mode": live_mode},
        )
        notes = []
    if not notes and live_mode == "fresh_lookup":
        unresolved_price = agent._unresolved_price_lookup_response(query=query, notes=notes, mode=live_mode)
        if unresolved_price:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=unresolved_price,
                confidence=0.84,
                source_context=source_context,
                reason="live_info_fast_path",
            )
        return None
    unresolved_price = agent._unresolved_price_lookup_response(query=query, notes=notes, mode=live_mode)
    if unresolved_price:
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=unresolved_price,
            confidence=0.84,
            source_context=source_context,
            reason="live_info_fast_path",
        )
    response = (
        agent._render_live_info_response(query=query, notes=notes, mode=live_mode)
        if notes
        else agent._live_info_failure_text(query=query, mode=live_mode)
    )
    structured_modes = {"weather", "news"}
    has_live_quote = agent._first_live_quote(notes) is not None
    if agent._is_chat_truth_surface(source_context) and live_mode not in structured_modes and not has_live_quote:
        return agent._chat_surface_model_wording_result(
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            persona=load_active_persona(agent.persona_id),
            interpretation=interpretation,
            task_class="research",
            response_class=response_class,
            reason="live_info_model_wording",
            model_input=agent._chat_surface_live_info_model_input(
                user_input=user_input,
                query=query,
                mode=live_mode,
                notes=notes,
                runtime_note="" if notes else response,
            ),
            fallback_response=(
                "I pulled live evidence for this turn, but I couldn't produce a clean final synthesis in this run."
                if notes
                else response
            ),
            tool_backing_sources=["web_lookup"] if notes else [],
        )
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.86 if notes else 0.52,
        source_context=source_context,
        reason="live_info_fast_path",
    )


def live_info_mode(agent: Any, text: str, *, interpretation: Any) -> str:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return ""
    if agent._looks_like_builder_request(lowered):
        return ""
    if any(
        marker in lowered
        for marker in (
            "what day is it",
            "what day is today",
            "what is the day today",
            "today's date",
            "date today",
            "what time is it",
            "what's the time",
            "time now",
        )
    ):
        return ""
    weather_markers = (
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
    lowered_padded = f" {lowered} "
    news_markers = (
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
    if any(marker in lowered_padded for marker in weather_markers):
        return "weather"
    if any(marker in lowered for marker in news_markers):
        return "news"
    if looks_like_live_recency_lookup(lowered):
        return "fresh_lookup"
    if looks_like_explicit_lookup_request(lowered) or looks_like_public_entity_lookup_request(lowered):
        return "fresh_lookup"
    if any(
        marker in lowered
        for marker in (
            "look up",
            "check online",
            "search online",
            "browse",
        )
    ):
        return "fresh_lookup"
    if any(
        marker in lowered
        for marker in (
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
    ):
        return "fresh_lookup"
    if any(marker in lowered for marker in ("latest", "newest", "recent", "just released")) and any(
        marker in lowered
        for marker in (
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
