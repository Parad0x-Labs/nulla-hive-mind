from __future__ import annotations

import unittest
from unittest import mock

from tools.web.web_research import PageEvidence, WebHit, web_research


class WebResearchRuntimeTests(unittest.TestCase):
    def test_ddg_instant_empty_falls_through_to_duckduckgo_html(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["ddg_instant", "duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research.ddg_instant_answer",
            return_value={},
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            return_value=[
                WebHit(
                    title="Telegram Bot API",
                    url="https://core.telegram.org/bots/api",
                    snippet="HTTP-based interface for building Telegram bots.",
                    engine="duckduckgo_html",
                )
            ],
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            return_value={"status": "ok", "text": "Useful docs text " * 60, "html": "<html></html>"},
        ), mock.patch(
            "tools.web.web_research._should_try_browser",
            return_value=False,
        ):
            result = web_research("Telegram Bot API docs", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "duckduckgo_html")
        self.assertIn("ddg_instant_empty", result.notes)
        self.assertTrue(result.hits)
        self.assertEqual(result.hits[0].url, "https://core.telegram.org/bots/api")

    def test_browser_disabled_keeps_http_text_without_claiming_browser_use(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            return_value=[
                WebHit(
                    title="Telegram Bot API",
                    url="https://core.telegram.org/bots/api",
                    snippet="Canonical Telegram docs.",
                    engine="duckduckgo_html",
                )
            ],
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            return_value={"status": "ok", "text": "short docs text", "html": "<html>short</html>"},
        ), mock.patch(
            "tools.web.web_research._should_try_browser",
            return_value=True,
        ), mock.patch(
            "tools.web.web_research.browser_render",
            return_value={"status": "disabled", "final_url": "https://core.telegram.org/bots/api"},
        ):
            result = web_research("Telegram Bot API docs", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "duckduckgo_html")
        self.assertTrue(result.pages)
        self.assertEqual(result.pages[0].status, "empty")
        self.assertEqual(result.pages[0].text, "short docs text")
        self.assertFalse(result.pages[0].used_browser)

    def test_weather_query_uses_specialized_live_fallback_when_search_providers_fail(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["ddg_instant", "duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research.ddg_instant_answer",
            return_value={},
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            side_effect=RuntimeError("duckduckgo_anomaly_challenge"),
        ), mock.patch(
            "tools.web.web_research._specialized_live_research",
            return_value=(
                "wttr_in",
                [
                    WebHit(
                        title="wttr.in weather for London",
                        url="https://wttr.in/London",
                        snippet="London: Rain, 12 C.",
                        engine="wttr_in",
                    )
                ],
                [
                    PageEvidence(
                        url="https://wttr.in/London",
                        final_url="https://wttr.in/London",
                        status="ok",
                        title="wttr.in weather for London",
                        text="London: Rain, 12 C.",
                        html_len=128,
                        used_browser=False,
                        screenshot_path=None,
                    )
                ],
                ["live_weather_fallback:wttr_in"],
            ),
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            side_effect=AssertionError("prebuilt weather page should skip refetch"),
        ):
            result = web_research("what is the weather in London today?", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "wttr_in")
        self.assertIn("live_weather_fallback:wttr_in", result.notes)
        self.assertEqual(result.pages[0].text, "London: Rain, 12 C.")

    def test_news_query_uses_specialized_live_fallback_and_preserves_final_url(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["ddg_instant", "duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research.ddg_instant_answer",
            return_value={},
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            side_effect=RuntimeError("duckduckgo_anomaly_challenge"),
        ), mock.patch(
            "tools.web.web_research._specialized_live_research",
            return_value=(
                "google_news_rss",
                [
                    WebHit(
                        title="OpenAI to acquire Promptfoo",
                        url="https://news.google.com/rss/articles/demo",
                        snippet="OpenAI | 2026-03-09 | OpenAI to acquire Promptfoo",
                        engine="google_news_rss",
                    )
                ],
                [],
                ["live_news_fallback:google_news_rss"],
            ),
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            return_value={
                "status": "ok",
                "text": "OpenAI announced it will acquire Promptfoo.",
                "html": "<html>OpenAI announced it will acquire Promptfoo.</html>",
                "final_url": "https://openai.com/index/openai-to-acquire-promptfoo/",
            },
        ), mock.patch(
            "tools.web.web_research._should_try_browser",
            return_value=False,
        ):
            result = web_research("latest news on OpenAI", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "google_news_rss")
        self.assertIn("live_news_fallback:google_news_rss", result.notes)
        self.assertEqual(result.pages[0].final_url, "https://openai.com/index/openai-to-acquire-promptfoo/")


if __name__ == "__main__":
    unittest.main()
