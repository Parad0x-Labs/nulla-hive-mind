from __future__ import annotations

import re
from typing import Any

from core.agent_runtime.fast_live_info_rendering import first_live_quote


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
