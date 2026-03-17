"""Fallback web search using HTML scraping of public search engines.

Named google_html for provider-order compatibility. Tries Brave Search
first, then Yahoo Search as fallback.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(raw: str) -> str:
    return unescape(_TAG_RE.sub("", raw)).strip()


def google_html_search(
    query: str, *, max_results: int = 8, timeout_s: float = 10.0,
) -> list[dict[str, str]]:
    """Search using available HTML search engines.

    Tries Brave Search, then falls back to Yahoo Search.
    """
    text = (query or "").strip()
    if not text:
        return []

    for search_fn in (_brave_search, _yahoo_search):
        try:
            results = search_fn(text, max_results=max_results, timeout_s=timeout_s)
            if results:
                return results
        except Exception:
            continue

    return []


def _brave_search(query: str, *, max_results: int, timeout_s: float) -> list[dict[str, str]]:
    params = urllib.parse.urlencode({"q": query})
    html = _fetch(
        f"https://search.brave.com/search?{params}", timeout_s=timeout_s,
    )
    blocks = html.split('class="snippet  svelte')[1:]
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    for block in blocks:
        chunk = block[:4000]
        href_m = re.search(r'href="(https?://[^"]+)"', chunk)
        if not href_m:
            continue
        url = href_m.group(1).strip()
        domain = _domain(url)
        if not domain or url in seen or domain in _BRAVE_SKIP:
            continue
        seen.add(url)

        title_m = re.search(r'snippet-title[^>]*>(.*?)</(?:span|a)', chunk, re.DOTALL)
        title = _clean(title_m.group(1)) if title_m else ""
        if not title:
            continue

        long = [t.strip() for t in re.findall(r">([^<]{40,})<", chunk) if t.strip() != title]
        snippet = _clean(long[0])[:300] if long else ""
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


_BRAVE_SKIP = frozenset({
    "search.brave.com", "brave.com", "youtube.com", "www.youtube.com",
})


def _yahoo_search(query: str, *, max_results: int, timeout_s: float) -> list[dict[str, str]]:
    params = urllib.parse.urlencode({"p": query})
    html = _fetch(
        f"https://search.yahoo.com/search?{params}", timeout_s=timeout_s,
    )

    title_re = re.compile(
        r'class="[^"]*compTitle[^"]*"[^>]*>.*?'
        r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    desc_re = re.compile(
        r'class="[^"]*compText[^"]*"[^>]*>(.*?)</(?:div|p|span)>',
        re.DOTALL,
    )

    desc_list = [_clean(d) for d in desc_re.findall(html) if len(_clean(d)) > 15]
    matches = title_re.findall(html)

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    desc_idx = 0

    for raw_url, raw_title in matches:
        url = _resolve_yahoo_url(raw_url)
        if not url:
            continue
        domain = _domain(url)
        if not domain or url in seen or domain in _YAHOO_SKIP:
            continue
        seen.add(url)

        title = _clean_yahoo_title(_clean(raw_title))
        if not title or len(title) < 3:
            continue
        if title.lower() in ("ad", "advertisement", "sponsored"):
            continue

        snippet = ""
        if desc_idx < len(desc_list):
            snippet = desc_list[desc_idx]
            desc_idx += 1

        results.append({"title": title, "url": url, "snippet": snippet[:300]})
        if len(results) >= max_results:
            break
    return results


_YAHOO_SKIP = frozenset({
    "search.yahoo.com", "yahoo.com", "help.yahoo.com",
    "bing.com", "www.bing.com",
    "youtube.com", "www.youtube.com",
})


def _clean_yahoo_title(title: str) -> str:
    """Strip the inline domain prefix Yahoo sometimes prepends to titles."""
    m = re.match(r'^[A-Za-z0-9.\-]+(?:https?://[^\s]+\s*›?\s*[^\s]*\s*)', title)
    if m:
        return title[m.end():].strip()
    m = re.match(r'^https?://[^\s]+\s*›?\s*[^\s]*\s*', title)
    if m:
        return title[m.end():].strip()
    return title


def _resolve_yahoo_url(raw: str) -> str:
    """Yahoo wraps result URLs in a redirect; extract the real target."""
    m = re.search(r"RU=([^/]+)", raw)
    if m:
        return urllib.parse.unquote(m.group(1))
    if raw.startswith("http") and "yahoo.com" not in raw and "bing.com" not in raw:
        return raw
    return ""


def _fetch(url: str, *, timeout_s: float, max_retries: int = 1) -> str:
    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code == 429 and attempt < max_retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                time.sleep(1.0)
                continue
            raise
    raise last_exc


def _domain(url: str) -> str:
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""
