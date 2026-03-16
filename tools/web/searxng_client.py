from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from core import policy_engine

DEFAULT_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: str | None = None
    score: float | None = None


class SearXNGClient:
    """Call a SearXNG instance using the JSON search API."""

    def __init__(self, base_url: str | None = None, timeout_s: float | None = None) -> None:
        configured_url = str(os.getenv("SEARXNG_URL") or policy_engine.searxng_url()).strip() or "http://127.0.0.1:8080"
        self.base_url = (base_url or configured_url).rstrip("/")
        env_timeout = os.getenv("SEARXNG_TIMEOUT")
        configured_timeout = policy_engine.searxng_timeout_seconds()
        raw_timeout: float | str = timeout_s if timeout_s is not None else (env_timeout if env_timeout else configured_timeout)
        self.timeout_s = float(raw_timeout)

    def search(
        self,
        query: str,
        *,
        language: str = "en",
        safesearch: int = 1,
        max_results: int = 10,
    ) -> list[SearchResult]:
        text = (query or "").strip()
        if not text:
            return []

        params = urllib.parse.urlencode(
            {
                "q": text,
                "format": "json",
                "language": language,
                "safesearch": str(max(0, int(safesearch))),
            }
        )
        request = urllib.request.Request(
            f"{self.base_url}/search?{params}",
            headers={"User-Agent": "NULLA-XSEARCH/1.0"},
        )
        start = time.time()
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            body = response.read().decode("utf-8")
        _ = time.time() - start
        payload = json.loads(body)
        items = list(payload.get("results") or [])[: max(1, int(max_results))]
        results: list[SearchResult] = []
        for item in items:
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("content") or "").strip()
            if not url:
                continue
            score_raw = item.get("score")
            try:
                score = float(score_raw) if score_raw is not None else None
            except (TypeError, ValueError):
                score = None
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    engine=str(item.get("engine") or "").strip() or None,
                    score=score,
                )
            )
        return results
