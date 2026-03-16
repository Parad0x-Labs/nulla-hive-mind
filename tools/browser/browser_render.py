from __future__ import annotations

import os
import re
from typing import Any

from core import policy_engine

_CAPTCHA_RE = re.compile(
    r"(captcha|are you human|robot check|bots use duckduckgo too|anomaly-modal|select all squares containing)",
    re.IGNORECASE,
)
_LOGIN_RE = re.compile(r"(sign in|log in|password)", re.IGNORECASE)


def _classify_rendered_content(html_text: str, text: str) -> str:
    lowered = f"{html_text}\n{text}".lower()
    if _CAPTCHA_RE.search(lowered):
        return "captcha"
    if _LOGIN_RE.search(lowered):
        return "login_wall"
    return "ok"


def browser_render(
    url: str,
    *,
    engine: str | None = None,
    timeout_ms: int = 20_000,
    max_scroll: int = 2,
    screenshot_path: str | None = None,
) -> dict[str, Any]:
    """Render JS-heavy pages with Playwright when explicitly enabled."""

    env_enabled = str(os.getenv("PLAYWRIGHT_ENABLED", "")).lower()
    enabled = env_enabled in {"1", "true", "yes"} if env_enabled else policy_engine.playwright_enabled()
    if not enabled:
        return {"status": "disabled_by_policy", "final_url": url}

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return {"status": "missing_dependency", "final_url": url}

    selected_engine = engine or os.getenv("BROWSER_ENGINE") or policy_engine.browser_engine()
    with sync_playwright() as playwright:
        browser = getattr(playwright, selected_engine).launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(timeout_ms)
        page.goto(url, wait_until="domcontentloaded")

        for _ in range(max(0, int(max_scroll))):
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(600)

        html_text = page.content()
        text = page.inner_text("body") if page.locator("body").count() else ""
        final_url = page.url
        rendered_status = _classify_rendered_content(html_text, text)
        if rendered_status == "captcha":
            browser.close()
            return {"status": "captcha", "final_url": final_url}
        if rendered_status == "login_wall":
            browser.close()
            return {"status": "login_wall", "final_url": final_url}

        links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href).slice(0, 200)") or []
        if screenshot_path:
            page.screenshot(path=screenshot_path, full_page=True)
        title = page.title()
        browser.close()
        return {
            "status": "ok",
            "final_url": final_url,
            "title": title,
            "text": text[:200000],
            "html": html_text[:2000000],
            "links": links,
            "screenshot_path": screenshot_path or None,
        }
