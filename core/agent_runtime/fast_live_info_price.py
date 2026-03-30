from __future__ import annotations

import re
from typing import Any

from core.agent_runtime.fast_live_info_rendering import first_live_quote

_PRICE_REQUEST_MARKERS = (
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
_PRICE_ASSET_ALIASES = (
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "solana",
    "sol",
    "cardano",
    "ada",
    "polkadot",
    "dot",
    "dogecoin",
    "doge",
    "ripple",
    "xrp",
    "litecoin",
    "ltc",
    "avalanche",
    "avax",
    "chainlink",
    "link",
    "polygon",
    "matic",
    "binance",
    "bnb",
    "brent crude",
    "brent",
    "wti crude",
    "wti",
    "gold",
    "silver",
    "xau",
    "xag",
)
_PRICE_FOLLOWUP_PREFIX_RE = re.compile(
    r"^(?:no[\s,]+)?(?:i\s+mean|meant|what\s+about|how\s+about|btw|by\s+the\s+way)\b[\s,:-]*",
    re.IGNORECASE,
)


def recover_price_lookup_query(
    user_input: str,
    *,
    source_context: dict[str, Any] | None,
) -> str:
    lowered = " ".join(str(user_input or "").strip().lower().split())
    if not lowered:
        return ""
    explicit_subject = _extract_price_asset_alias(lowered)
    has_price_marker = any(marker in lowered for marker in _PRICE_REQUEST_MARKERS)
    looks_like_short_subject_followup = bool(explicit_subject) and bool(_PRICE_FOLLOWUP_PREFIX_RE.match(lowered))
    if explicit_subject and (has_price_marker or looks_like_short_subject_followup):
        return f"{explicit_subject} price now"
    if has_price_marker:
        recent_subject = _recent_price_subject(source_context)
        if recent_subject:
            return f"{recent_subject} price now"
    return ""


def looks_like_grounded_price_lookup(query: str) -> bool:
    lowered = " ".join(str(query or "").strip().lower().split())
    if not lowered:
        return False
    if not any(marker in lowered for marker in _PRICE_REQUEST_MARKERS):
        return False
    return bool(_extract_price_asset_alias(lowered) or extract_price_lookup_subject(query))


def _extract_price_asset_alias(text: str) -> str:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return ""
    for alias in sorted(_PRICE_ASSET_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            return alias
    return ""


def _recent_price_subject(source_context: dict[str, Any] | None) -> str:
    history = [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]
    for message in reversed(history[-10:]):
        content = " ".join(str(message.get("content") or "").split()).strip().lower()
        if not content:
            continue
        subject = _extract_price_asset_alias(content)
        if not subject:
            continue
        if any(marker in content for marker in _PRICE_REQUEST_MARKERS) or "source:" in content or "$" in content:
            return subject
    return ""


def unresolved_price_lookup_response(*, query: str, notes: list[dict[str, Any]], mode: str) -> str:
    if mode != "fresh_lookup":
        return ""
    lowered = " ".join(str(query or "").strip().lower().split())
    if not any(marker in lowered for marker in _PRICE_REQUEST_MARKERS):
        return ""
    if first_live_quote(notes) is not None:
        return ""
    if notes_include_grounded_price_signal(notes):
        return ""
    if _extract_price_asset_alias(lowered):
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
