from __future__ import annotations

import unittest
from unittest import mock

from retrieval.web_adapter import WebAdapter
from storage.db import get_connection
from storage.migrations import run_migrations
from tools.web.web_research import PageEvidence, ResearchResult, WebHit


def _research_result(*, query: str, hits: list[WebHit], pages: list[PageEvidence] | None = None) -> ResearchResult:
    return ResearchResult(
        query=query,
        provider="duckduckgo_html",
        hits=hits,
        pages=list(pages or []),
        notes=[],
        ts_utc=0.0,
    )


class WebAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM web_notes")
            conn.commit()
        finally:
            conn.close()

    def test_planned_search_query_prefers_official_docs_and_root_repo(self) -> None:
        def fake_research(query: str, *, limit: int = 3):
            if "site:core.telegram.org" in query:
                return _research_result(
                    query=query,
                    hits=[
                        WebHit(
                            title="Telegram Bot API",
                            url="https://core.telegram.org/bots/api",
                            snippet="Canonical auth and update delivery docs.",
                        )
                    ],
                    pages=[
                        PageEvidence(
                            url="https://core.telegram.org/bots/api",
                            final_url="https://core.telegram.org/bots/api",
                            status="ok",
                            title="Telegram Bot API",
                            text="Bot API authentication, updates, and webhook limits.",
                            html_len=1500,
                            used_browser=False,
                            screenshot_path=None,
                        )
                    ],
                )
            if "site:github.com" in query:
                return _research_result(
                    query=query,
                    hits=[
                        WebHit(
                            title="tg-bot issue thread",
                            url="https://github.com/acme/tg-bot/issues/12",
                            snippet="Issue discussion.",
                        ),
                        WebHit(
                            title="acme/tg-bot",
                            url="https://github.com/acme/tg-bot",
                            snippet="Maintained Telegram bot example repo.",
                        ),
                    ],
                    pages=[
                        PageEvidence(
                            url="https://github.com/acme/tg-bot",
                            final_url="https://github.com/acme/tg-bot",
                            status="ok",
                            title="acme/tg-bot",
                            text="README with setup and deployment examples.",
                            html_len=1200,
                            used_browser=False,
                            screenshot_path=None,
                        )
                    ],
                )
            return _research_result(query=query, hits=[])

        with mock.patch.object(WebAdapter, "research_query", side_effect=fake_research):
            notes = WebAdapter.planned_search_query(
                "build telegram bot with docs and github",
                limit=3,
                task_class="system_design",
                topic_kind="integration",
            )

        self.assertTrue(notes)
        self.assertEqual(notes[0]["origin_domain"], "core.telegram.org")
        self.assertTrue(any(note.get("source_profile_id") == "messaging_platform_docs" for note in notes))
        self.assertTrue(any(note.get("source_profile_id") == "reputable_repos" for note in notes))
        self.assertTrue(any(note.get("github_repo_root") == "https://github.com/acme/tg-bot" for note in notes))
        self.assertFalse(any("/issues/" in str(note.get("result_url") or "") for note in notes))
        self.assertGreaterEqual(float(notes[0]["confidence"]), 0.45)

    def test_search_query_ignores_missing_local_task_foreign_key(self) -> None:
        def fake_research(query: str, *, limit: int = 3):
            return _research_result(
                query=query,
                hits=[
                    WebHit(
                        title="Telegram Bot API",
                        url="https://core.telegram.org/bots/api",
                        snippet="Canonical docs.",
                    )
                ],
                pages=[
                    PageEvidence(
                        url="https://core.telegram.org/bots/api",
                        final_url="https://core.telegram.org/bots/api",
                        status="ok",
                        title="Telegram Bot API",
                        text="Bot API docs with auth and webhook guidance.",
                        html_len=1200,
                        used_browser=False,
                        screenshot_path=None,
                    )
                ],
            )

        with mock.patch.object(WebAdapter, "research_query", side_effect=fake_research):
            notes = WebAdapter.search_query(
                "telegram bot api docs",
                task_id="research:7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                limit=1,
            )

        self.assertEqual(len(notes), 1)
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT used_in_task_id FROM web_notes ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertIsNone(row["used_in_task_id"])

    def test_search_query_uses_page_final_url_for_domain_and_result(self) -> None:
        def fake_research(query: str, *, limit: int = 3):
            return _research_result(
                query=query,
                hits=[
                    WebHit(
                        title="OpenAI to acquire Promptfoo",
                        url="https://news.google.com/rss/articles/demo",
                        snippet="OpenAI | 2026-03-09 | OpenAI to acquire Promptfoo",
                    )
                ],
                pages=[
                    PageEvidence(
                        url="https://news.google.com/rss/articles/demo",
                        final_url="https://openai.com/index/openai-to-acquire-promptfoo/",
                        status="ok",
                        title="OpenAI to acquire Promptfoo",
                        text="OpenAI announced it will acquire Promptfoo.",
                        html_len=1600,
                        used_browser=False,
                        screenshot_path=None,
                    )
                ],
            )

        with mock.patch.object(WebAdapter, "research_query", side_effect=fake_research):
            notes = WebAdapter.search_query(
                "latest news on OpenAI",
                limit=1,
            )

        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["origin_domain"], "openai.com")
        self.assertEqual(notes[0]["result_url"], "https://openai.com/index/openai-to-acquire-promptfoo/")

    def test_search_query_prefers_snippet_when_page_text_is_navigation_noise(self) -> None:
        def fake_research(query: str, *, limit: int = 3):
            return _research_result(
                query=query,
                hits=[
                    WebHit(
                        title="BBC Weather - London",
                        url="https://www.bbc.com/weather/2643743",
                        snippet="Cloudy with light rain, around 11C, with breezy afternoon conditions.",
                    )
                ],
                pages=[
                    PageEvidence(
                        url="https://www.bbc.com/weather/2643743",
                        final_url="https://www.bbc.com/weather/2643743",
                        status="ok",
                        title="BBC Weather - London",
                        text=(
                            "BBC Weather Homepage Accessibility links Skip to content Accessibility Help "
                            "BBC Account Notifications Home News Sport Weather iPlayer Sounds Bitesize"
                        ),
                        html_len=1800,
                        used_browser=False,
                        screenshot_path=None,
                    )
                ],
            )

        with mock.patch.object(WebAdapter, "research_query", side_effect=fake_research):
            notes = WebAdapter.search_query(
                "what is the weather in London today?",
                limit=1,
            )

        self.assertEqual(len(notes), 1)
        self.assertIn("cloudy with light rain", notes[0]["summary"].lower())
        self.assertNotIn("accessibility links", notes[0]["summary"].lower())

    def test_planned_search_query_for_telegram_drops_cross_platform_docs(self) -> None:
        def fake_research(query: str, *, limit: int = 3):
            if "site:core.telegram.org" in query:
                return _research_result(
                    query=query,
                    hits=[
                        WebHit(
                            title="Telegram Bot API",
                            url="https://core.telegram.org/bots/api",
                            snippet="Official Telegram Bot API updates.",
                        ),
                        WebHit(
                            title="Discord Change Log",
                            url="https://docs.discord.com/developers/change-log",
                            snippet="Discord platform updates.",
                        ),
                    ],
                    pages=[
                        PageEvidence(
                            url="https://core.telegram.org/bots/api",
                            final_url="https://core.telegram.org/bots/api",
                            status="ok",
                            title="Telegram Bot API",
                            text="Official Telegram Bot API updates.",
                            html_len=1200,
                            used_browser=False,
                            screenshot_path=None,
                        )
                    ],
                )
            return _research_result(query=query, hits=[])

        with mock.patch.object(WebAdapter, "research_query", side_effect=fake_research):
            notes = WebAdapter.planned_search_query(
                "latest telegram bot api updates",
                limit=3,
                task_class="research",
                topic_kind="integration",
                topic_hints=["telegram"],
            )

        self.assertTrue(notes)
        self.assertTrue(all(note["origin_domain"] == "core.telegram.org" for note in notes))


if __name__ == "__main__":
    unittest.main()
