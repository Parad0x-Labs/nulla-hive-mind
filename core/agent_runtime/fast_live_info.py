from __future__ import annotations

import re
from typing import Any

from core import audit_logger, policy_engine
from core.identity_manager import load_active_persona
from core.live_quote_contract import LiveQuoteResult, validate_live_quote_payload
from core.task_router import (
    looks_like_explicit_lookup_request,
    looks_like_live_recency_lookup,
    looks_like_public_entity_lookup_request,
)
from retrieval.web_adapter import WebAdapter


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


def render_live_info_response(*, query: str, notes: list[dict[str, Any]], mode: str) -> str:
    if mode == "weather":
        return render_weather_response(query=query, notes=notes)
    if mode == "news":
        return render_news_response(query=query, notes=notes)
    live_quote = first_live_quote(notes)
    if mode == "fresh_lookup" and live_quote is not None:
        return live_quote.answer_text()
    label = {
        "news": "Live news results",
        "fresh_lookup": "Live web results",
    }.get(mode, "Live web results")
    lines = [f"{label} for `{query}`:"]
    browser_used = False
    for note in list(notes or [])[:3]:
        title = str(note.get("result_title") or note.get("origin_domain") or "Source").strip()
        domain = str(note.get("origin_domain") or "").strip()
        snippet = " ".join(str(note.get("summary") or "").split()).strip()
        url = str(note.get("result_url") or "").strip()
        line = f"- {title}"
        if domain and domain.lower() not in title.lower():
            line += f" ({domain})"
        if snippet:
            line += f": {snippet[:220]}"
        if url:
            line += f" [{url}]"
        lines.append(line)
        browser_used = browser_used or bool(note.get("used_browser"))
    if browser_used:
        lines.append("Browser rendering was used for at least one source when plain fetch was too thin.")
    return "\n".join(lines)


def first_live_quote(notes: list[dict[str, Any]]) -> LiveQuoteResult | None:
    for note in list(notes or []):
        payload = note.get("live_quote")
        if not isinstance(payload, dict):
            continue
        ok, _reason = validate_live_quote_payload(payload)
        if not ok:
            continue
        try:
            return LiveQuoteResult.from_payload(payload)
        except Exception:
            continue
    return None


def render_weather_response(*, query: str, notes: list[dict[str, Any]]) -> str:
    location = re.sub(
        r"\b(?:what\s+is\s+(?:the\s+)?|how\s+is\s+(?:the\s+)?|weather\s+(?:like\s+)?(?:in|for|at)\s+|"
        r"weather\s+in\s+|now\??|right\s+now\??|today\??|current(?:ly)?)\b",
        "",
        query,
        flags=re.IGNORECASE,
    )
    location = re.sub(r"\bforecast\b", " ", location, flags=re.IGNORECASE)
    location = " ".join(location.split()).strip(" ?.,!") or "your location"
    primary = dict(next((note for note in list(notes or []) if isinstance(note, dict)), {}))
    summary = " ".join(str(primary.get("summary") or "").split()).strip()
    url = str(primary.get("result_url") or "").strip()
    domain = str(primary.get("origin_domain") or "").strip()
    if summary:
        if location.lower() in summary.lower():
            line = summary
        else:
            line = f"Weather in {location}: {summary}"
    else:
        line = f"I searched for weather in {location} but couldn't extract conditions from the results."
    if url:
        line += f" Source: [{domain or 'source'}]({url})."
    return line


def render_news_response(*, query: str, notes: list[dict[str, Any]]) -> str:
    topic = re.sub(
        r"^\s*(?:what's|what is|whats)\s+the\s+latest\s+on\s+|^\s*latest\s+(?:news\s+on|news\s+about|on|about)\s+",
        "",
        query,
        flags=re.IGNORECASE,
    ).strip(" ?.,!") or query.strip()
    lines = [f"Latest coverage on {topic}:"]
    for note in list(notes or [])[:3]:
        summary = " ".join(str(note.get("summary") or "").split()).strip()
        fallback_title = str(note.get("result_title") or "").strip()
        url = str(note.get("result_url") or "").strip()
        domain = str(note.get("origin_domain") or "").strip()
        parts = [part.strip() for part in summary.split("|") if part.strip()]
        source = parts[0] if len(parts) >= 1 else domain
        published = parts[1] if len(parts) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1]) else ""
        headline = parts[2] if len(parts) >= 3 else fallback_title or summary
        lead_parts = [item for item in (published, source) if item]
        lead = " | ".join(lead_parts)
        line = f"- {headline}"
        if lead:
            line = f"- {lead}: {headline}"
        if url:
            line += f" [{url}]"
        lines.append(line)
    return "\n".join(lines)


def unresolved_price_lookup_response(*, query: str, notes: list[dict[str, Any]], mode: str) -> str:
    if mode != "fresh_lookup":
        return ""
    lowered = " ".join(str(query or "").strip().lower().split())
    if not any(
        marker in lowered
        for marker in (
            "price",
            "cost",
            "worth",
            "value",
            "quote",
            "rate",
            "market cap",
            "trading at",
            "how much",
        )
    ):
        return ""
    if first_live_quote(notes) is not None:
        return ""
    if notes_include_grounded_price_signal(notes):
        return ""
    subject = extract_price_lookup_subject(query)
    if not subject:
        return ""
    return (
        f"I couldn't map `{subject}` to a known traded asset or commodity quote. "
        "If you mean a stock, token, ETF, or product, give me the exact ticker or full name."
    )


def notes_include_grounded_price_signal(notes: list[dict[str, Any]]) -> bool:
    finance_domains = (
        "finance.yahoo.com",
        "coingecko.com",
        "marketwatch.com",
        "bloomberg.com",
        "tradingview.com",
        "investing.com",
    )
    for note in list(notes or []):
        if not isinstance(note, dict):
            continue
        text = " ".join(
            str(part).strip()
            for part in (
                note.get("summary"),
                note.get("result_title"),
                note.get("origin_domain"),
            )
            if str(part).strip()
        )
        lowered = text.lower()
        domain = str(note.get("origin_domain") or "").strip().lower()
        if any(finance_domain in domain for finance_domain in finance_domains) and re.search(r"\d", text):
            return True
        if re.search(r"[$€£¥]\s?\d", text):
            return True
        if re.search(r"\b\d[\d,]*(?:\.\d+)?\s*(?:usd|eur|gbp|jpy|btc|eth)\b", lowered):
            return True
        if any(marker in lowered for marker in ("price", "quote", "market cap", "session change", "24h change")) and re.search(r"\d", text):
            return True
    return False


def extract_price_lookup_subject(query: str) -> str:
    clean = re.sub(r"[\?\!\.,]+", " ", str(query or "")).strip()
    clean = re.sub(
        r"\b(?:what\s+is|what's|whats|tell\s+me|show\s+me|price|cost|worth|value|quote|rate|market\s+cap|"
        r"trading\s+at|how\s+much|current|latest|today|now|right\s+now|for|of|the)\b",
        " ",
        clean,
        flags=re.IGNORECASE,
    )
    return " ".join(clean.split()).strip()


def live_info_failure_text(*, query: str, mode: str) -> str:
    if mode == "weather":
        return f'I tried the live web lane for "{query}", but no current weather results came back.'
    if mode == "news":
        return f'I tried the live web lane for "{query}", but no current news results came back.'
    return f'I checked the live web lane for "{query}", but I could not ground a current answer confidently.'
