from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

from core import policy_engine
from core.source_credibility import evaluate_source_domain
from retrieval.web_adapter import _domain_from_url
from tools.browser.browser_render import browser_render
from tools.web.ddg_instant import best_text_blob, ddg_instant_answer
from tools.web.http_fetch import http_fetch_text
from tools.web.searxng_client import SearchResult, SearXNGClient


@dataclass(frozen=True)
class WebHit:
    title: str
    url: str
    snippet: str = ""
    engine: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class PageEvidence:
    url: str
    final_url: str | None
    status: str
    title: str = ""
    text: str = ""
    html_len: int = 0
    used_browser: bool = False
    screenshot_path: str | None = None


@dataclass(frozen=True)
class ResearchResult:
    query: str
    provider: str
    hits: list[WebHit]
    pages: list[PageEvidence]
    notes: list[str]
    ts_utc: float


def _provider_order() -> list[str]:
    env_raw = str(os.getenv("WEB_SEARCH_PROVIDER_ORDER", "")).strip()
    allowed = set(policy_engine.allowed_web_engines())
    if env_raw:
        return [item.strip().lower() for item in env_raw.split(",") if item.strip() and item.strip().lower() in allowed]
    return [item for item in policy_engine.web_provider_order() if item in allowed]


def _should_try_browser() -> bool:
    env_value = str(os.getenv("ALLOW_BROWSER_FALLBACK", "")).lower()
    if env_value:
        return env_value in {"1", "true", "yes"}
    return policy_engine.allow_browser_fallback()


def _text_too_short(text: str) -> bool:
    return len((text or "").strip()) < 600


def _needs_browser(fetch_status: str, text: str) -> bool:
    return fetch_status in {"captcha", "login_wall"} or _text_too_short(text)


def _looks_like_weather_query(query: str) -> bool:
    lowered = str(query or "").lower()
    return any(token in lowered for token in ("weather", "forecast", "temperature", "rain", "snow", "wind", "humidity"))


def _looks_like_news_query(query: str) -> bool:
    lowered = str(query or "").lower()
    return any(token in lowered for token in ("latest news", "breaking news", "headlines", "news on", "news about", "what happened today"))


def _extract_weather_location(query: str) -> str:
    clean = re.sub(r"[\?\!\.,]+", " ", str(query or "")).strip()
    clean = re.sub(r"\s+", " ", clean)
    lowered = clean.lower()
    for pattern in (
        r"\b(?:weather|forecast|temperature|rain|snow|wind|humidity)\s+(?:in|for|at)\s+(.+)$",
        r"\b(?:what is|what's|tell me|show me)\s+the\s+(?:weather|forecast)\s+(?:in|for|at)\s+(.+)$",
        r"\b(?:in|for|at)\s+(.+)$",
    ):
        match = re.search(pattern, lowered)
        if match:
            location = match.group(1).strip()
            break
    else:
        location = lowered
        for token in ("weather", "forecast", "temperature", "rain", "snow", "wind", "humidity"):
            location = location.replace(token, " ")

    tokens = [item for item in location.split() if item]
    trailing_noise = {
        "today",
        "tomorrow",
        "tonight",
        "now",
        "currently",
        "current",
        "forecast",
        "please",
        "right",
        "this",
        "week",
        "weekend",
    }
    while tokens and tokens[-1] in trailing_noise:
        tokens.pop()
    candidate = " ".join(tokens).strip()
    return candidate or "current location"


def _extract_news_topic(query: str) -> str:
    clean = re.sub(r"\s+", " ", str(query or "").strip())
    lowered = clean.lower()
    for prefix in (
        "latest news on ",
        "latest news about ",
        "breaking news on ",
        "breaking news about ",
        "news on ",
        "news about ",
        "headlines on ",
        "headlines about ",
    ):
        if lowered.startswith(prefix):
            return clean[len(prefix):].strip() or clean
    return clean


def _compact_pub_date(raw_value: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        return text


def _prebuilt_page_for_hit(pages: list[PageEvidence], hit: WebHit) -> PageEvidence | None:
    for page in list(pages or []):
        if page.url == hit.url or page.final_url == hit.url:
            return page
    return None


def _specialized_live_research(
    query: str,
    *,
    max_hits: int,
    fetch_timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    if _looks_like_weather_query(query):
        return _weather_fallback(query, timeout_s=fetch_timeout_s)
    if _looks_like_news_query(query):
        return _news_rss_fallback(query, max_hits=max_hits, timeout_s=fetch_timeout_s)
    return None


def _weather_fallback(
    query: str,
    *,
    timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    location = _extract_weather_location(query)
    page_url = "https://wttr.in/" + urllib.parse.quote(location)
    api_url = page_url + "?format=j1"
    request = urllib.request.Request(api_url, headers={"User-Agent": "NULLA-WEATHER/1.0"})
    with urllib.request.urlopen(request, timeout=min(max(timeout_s, 3.0), 12.0)) as response:
        payload = json.loads(response.read(300000).decode("utf-8", errors="ignore"))

    current_items = list(payload.get("current_condition") or [])
    nearest_items = list(payload.get("nearest_area") or [])
    if not current_items:
        return None

    current = current_items[0] or {}
    area = nearest_items[0] if nearest_items else {}
    area_name = _first_nested_value(area.get("areaName")) or location
    country_name = _first_nested_value(area.get("country"))
    observed = str(current.get("localObsDateTime") or current.get("observation_time") or "").strip()
    weather_desc = _first_nested_value(current.get("weatherDesc")) or "Conditions unavailable"
    temp_c = str(current.get("temp_C") or "?").strip()
    feels_c = str(current.get("FeelsLikeC") or "?").strip()
    humidity = str(current.get("humidity") or "?").strip()
    wind_kmph = str(current.get("windspeedKmph") or "?").strip()
    place = area_name if not country_name or country_name.lower() == area_name.lower() else f"{area_name}, {country_name}"
    summary = (
        f"{place}: {weather_desc}, {temp_c} C (feels like {feels_c} C), "
        f"humidity {humidity}%, wind {wind_kmph} km/h."
    )
    if observed:
        summary += f" Observed {observed}."

    hit = WebHit(
        title=f"wttr.in weather for {place}",
        url=page_url,
        snippet=summary,
        engine="wttr_in",
        score=None,
    )
    page = PageEvidence(
        url=page_url,
        final_url=page_url,
        status="ok",
        title=hit.title,
        text=summary,
        html_len=len(json.dumps(payload)),
        used_browser=False,
        screenshot_path=None,
    )
    return ("wttr_in", [hit], [page], ["live_weather_fallback:wttr_in"])


def _news_rss_fallback(
    query: str,
    *,
    max_hits: int,
    timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    topic = _extract_news_topic(query)
    if not topic:
        return None
    rss_url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(topic)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    request = urllib.request.Request(rss_url, headers={"User-Agent": "NULLA-NEWS/1.0"})
    with urllib.request.urlopen(request, timeout=min(max(timeout_s, 3.0), 12.0)) as response:
        xml_text = response.read(500000).decode("utf-8", errors="ignore")

    root = ET.fromstring(xml_text)
    hits: list[WebHit] = []
    seen_urls: set[str] = set()
    for item in root.findall(".//item"):
        title = str(item.findtext("title") or "").strip()
        link = str(item.findtext("link") or "").strip()
        source_el = item.find("source")
        source_name = str(source_el.text or "").strip() if source_el is not None and source_el.text else ""
        source_url = str(source_el.get("url") or "").strip() if source_el is not None else ""
        final_url = _resolve_redirect_url(link, timeout_s=timeout_s) or source_url or link
        if not final_url or final_url in seen_urls:
            continue
        verdict = evaluate_source_domain(_domain_from_url(final_url or source_url))
        if verdict.blocked:
            continue
        seen_urls.add(final_url)
        pub_date = _compact_pub_date(str(item.findtext("pubDate") or ""))
        summary_parts = [source_name, pub_date, title]
        summary = " | ".join(part for part in summary_parts if part)
        hits.append(
            WebHit(
                title=title or source_name or "News result",
                url=final_url,
                snippet=summary[:280],
                engine="google_news_rss",
                score=None,
            )
        )
        if len(hits) >= max(1, int(max_hits)):
            break
    if not hits:
        return None
    return ("google_news_rss", hits, [], ["live_news_fallback:google_news_rss"])


def _resolve_redirect_url(url: str, *, timeout_s: float) -> str:
    target = str(url or "").strip()
    if not target:
        return ""
    request = urllib.request.Request(target, headers={"User-Agent": "NULLA-NEWS/1.0"})
    with urllib.request.urlopen(request, timeout=min(max(timeout_s, 3.0), 12.0)) as response:
        return str(response.geturl() or target).strip()


def _first_nested_value(items: Any) -> str:
    for item in list(items or []):
        if isinstance(item, dict):
            value = str(item.get("value") or "").strip()
            if value:
                return value
        else:
            value = str(item or "").strip()
            if value:
                return value
    return ""


def web_research(
    query: str,
    *,
    language: str = "en",
    safesearch: int = 1,
    max_hits: int = 8,
    max_pages: int = 3,
    fetch_timeout_s: float = 15.0,
    browser_engine: str | None = None,
    evidence_screenshot_dir: str | None = None,
) -> ResearchResult:
    notes: list[str] = []
    hits: list[WebHit] = []
    pages: list[PageEvidence] = []
    provider_used = "none"

    for provider in _provider_order():
        if provider == "searxng":
            try:
                client = SearXNGClient()
                results: list[SearchResult] = client.search(
                    query,
                    language=language,
                    safesearch=safesearch,
                    max_results=max_hits,
                )
                hits = [WebHit(r.title, r.url, r.snippet, r.engine, r.score) for r in results if r.url]
                if hits:
                    provider_used = "searxng"
                    break
            except Exception as exc:
                notes.append(f"searxng_failed:{type(exc).__name__}")
                continue

        if provider in {"ddg", "ddg_instant"}:
            try:
                payload = ddg_instant_answer(query, timeout_s=10.0)
                blob = best_text_blob(payload) or ""
                url = str(payload.get("AbstractURL") or "").strip()
                title = str(payload.get("Heading") or "DuckDuckGo Instant Answer").strip()
                if not url:
                    url = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(query)
                if not blob and url.startswith("https://duckduckgo.com/?q="):
                    notes.append("ddg_instant_empty")
                    continue
                hits = [WebHit(title=title, url=url, snippet=blob, engine="ddg_instant", score=None)]
                provider_used = "ddg_instant"
                break
            except Exception as exc:
                notes.append(f"ddg_failed:{type(exc).__name__}")
                continue

        if provider == "duckduckgo_html":
            try:
                hits = _duckduckgo_html_hits(query, max_hits=max_hits)
                if hits:
                    provider_used = "duckduckgo_html"
                    break
            except Exception as exc:
                notes.append(f"duckduckgo_html_failed:{type(exc).__name__}")
                continue

    if not hits:
        try:
            specialized = _specialized_live_research(
                query,
                max_hits=max_hits,
                fetch_timeout_s=fetch_timeout_s,
            )
        except Exception as exc:
            notes.append(f"specialized_live_failed:{type(exc).__name__}")
            specialized = None
        if specialized is None:
            notes.append("no_search_hits")
            return ResearchResult(query=query, provider=provider_used, hits=[], pages=[], notes=notes, ts_utc=time.time())
        provider_used, hits, pages, extra_notes = specialized
        notes.extend(extra_notes)

    for hit in hits[: max(1, int(max_pages))]:
        if _prebuilt_page_for_hit(pages, hit) is not None:
            continue
        try:
            fetched = http_fetch_text(hit.url, timeout_s=fetch_timeout_s)
            status = str(fetched.get("status") or "fetch_error")
            text = str(fetched.get("text") or "")
            html_text = str(fetched.get("html") or "")
            final_url = str(fetched.get("final_url") or hit.url)

            if _should_try_browser() and _needs_browser(status, text):
                screenshot_path = None
                if evidence_screenshot_dir:
                    os.makedirs(evidence_screenshot_dir, exist_ok=True)
                    screenshot_path = os.path.join(
                        evidence_screenshot_dir,
                        f"shot_{abs(hash(hit.url)) % 10_000_000}.png",
                    )
                rendered = browser_render(
                    hit.url,
                    engine=(browser_engine or os.getenv("BROWSER_ENGINE") or policy_engine.browser_engine()),
                    screenshot_path=screenshot_path,
                )
                rendered_status = str(rendered.get("status") or "fetch_error")
                if rendered_status == "ok":
                    pages.append(
                        PageEvidence(
                            url=hit.url,
                            final_url=str(rendered.get("final_url") or final_url),
                            status="ok",
                            title=str(rendered.get("title") or hit.title),
                            text=str(rendered.get("text") or "")[:200000],
                            html_len=len(str(rendered.get("html") or "")),
                            used_browser=True,
                            screenshot_path=rendered.get("screenshot_path"),
                        )
                    )
                else:
                    fallback_status = status
                    if fallback_status == "ok" and _text_too_short(text):
                        fallback_status = "empty"
                    pages.append(
                        PageEvidence(
                            url=hit.url,
                            final_url=str(rendered.get("final_url") or final_url),
                            status=fallback_status,
                            title=hit.title,
                            text=text[:200000],
                            html_len=len(html_text),
                            used_browser=False,
                            screenshot_path=None,
                        )
                    )
                continue

            pages.append(
                PageEvidence(
                    url=hit.url,
                    final_url=final_url,
                    status="empty" if status == "ok" and _text_too_short(text) else status,
                    title=hit.title,
                    text=text[:200000],
                    html_len=len(html_text),
                    used_browser=False,
                    screenshot_path=None,
                )
            )
        except Exception as exc:
            pages.append(
                PageEvidence(
                    url=hit.url,
                    final_url=None,
                    status=f"fetch_error:{type(exc).__name__}",
                    title=hit.title,
                    text="",
                    html_len=0,
                    used_browser=False,
                    screenshot_path=None,
                )
            )

    return ResearchResult(
        query=query,
        provider=provider_used,
        hits=hits[: max(1, int(max_hits))],
        pages=pages,
        notes=notes,
        ts_utc=time.time(),
    )


def to_jsonable(result: ResearchResult) -> dict[str, Any]:
    return {
        "query": result.query,
        "provider": result.provider,
        "hits": [asdict(item) for item in result.hits],
        "pages": [asdict(item) for item in result.pages],
        "notes": list(result.notes),
        "ts_utc": result.ts_utc,
    }


def _duckduckgo_html_hits(query: str, *, max_hits: int) -> list[WebHit]:
    from html import unescape

    text = (query or "").strip()
    if not text:
        return []
    request = urllib.request.Request(
        "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(text),
        headers={"User-Agent": "Mozilla/5.0 NULLA-XSEARCH/1.0"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        html_text = response.read().decode("utf-8", errors="ignore")
    if "Unfortunately, bots use DuckDuckGo too." in html_text or "anomaly-modal" in html_text:
        raise RuntimeError("duckduckgo_anomaly_challenge")
    snippet_matches = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL)
    link_matches = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', html_text, re.IGNORECASE)
    title_matches = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL)
    hits: list[WebHit] = []
    for raw_title, raw_snippet, raw_link in zip(title_matches, snippet_matches, link_matches):
        resolved_url = _resolve_duckduckgo_result_url(raw_link)
        if not resolved_url:
            continue
        title = re.sub(r"<[^>]+>", "", raw_title).strip()
        snippet = re.sub(r"<[^>]+>", "", raw_snippet).strip()
        hits.append(
            WebHit(
                title=unescape(title),
                url=resolved_url,
                snippet=unescape(snippet),
                engine="duckduckgo_html",
                score=None,
            )
        )
        if len(hits) >= max(1, int(max_hits)):
            break
    return hits


def _resolve_duckduckgo_result_url(raw_href: str) -> str:
    from html import unescape
    from urllib.parse import parse_qs, urlparse

    href = unescape(raw_href or "").strip()
    if not href:
        return ""
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    if query.get("uddg"):
        return urllib.parse.unquote(query["uddg"][0])
    return href
