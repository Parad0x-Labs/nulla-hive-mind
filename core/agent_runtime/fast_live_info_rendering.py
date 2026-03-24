from __future__ import annotations

import re
from typing import Any

from core.live_quote_contract import LiveQuoteResult, validate_live_quote_payload


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
