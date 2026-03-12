from __future__ import annotations

import html
import re
import urllib.request
from html.parser import HTMLParser

from core import policy_engine


_CAPTCHA_RE = re.compile(r"(captcha|are you human|robot check)", re.IGNORECASE)
_LOGIN_RE = re.compile(r"(sign in|log in|password)", re.IGNORECASE)


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = re.sub(r"\s+", " ", data or "").strip()
        if text:
            self.parts.append(text)


def strip_html(raw_html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(raw_html or "")
    return " ".join(parser.parts).strip()


def http_fetch_text(url: str, *, timeout_s: float = 15.0, max_bytes: int = 2_000_000) -> dict[str, str]:
    max_bytes = max(1024, min(int(max_bytes), policy_engine.max_fetch_bytes()))
    request = urllib.request.Request(url, headers={"User-Agent": "NULLA-FETCH/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        final_url = str(response.geturl() or url)
        raw = response.read(max_bytes)
    html_text = raw.decode("utf-8", errors="ignore")
    lowered = html_text.lower()
    if _CAPTCHA_RE.search(lowered):
        return {"status": "captcha", "text": "", "html": html_text, "final_url": final_url}
    if _LOGIN_RE.search(lowered):
        return {"status": "login_wall", "text": "", "html": html_text, "final_url": final_url}
    return {"status": "ok", "text": html.unescape(strip_html(html_text)), "html": html_text, "final_url": final_url}
