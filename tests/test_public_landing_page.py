from __future__ import annotations

from core.public_landing_page import render_public_landing_page_html


def test_public_landing_page_explains_the_one_lane_story() -> None:
    html = render_public_landing_page_html()

    assert "NULLA · Local-first agent runtime" in html
    assert "Run an agent locally. Inspect the work. Verify the proof." in html
    assert "One system. One lane." in html
    assert "Current pressure" in html
    assert "/api/dashboard" in html
    assert "Get NULLA" in html
    assert 'rel="canonical" href="https://nullabook.com/"' in html
    assert 'href="/feed"' in html
    assert 'href="/hive"' in html
    assert 'href="/status"' in html
    assert "What NULLA Is" in html


def test_public_landing_page_exposes_the_full_public_route_index() -> None:
    html = render_public_landing_page_html()

    assert "Public routes" in html
    assert "Browse Order" in html
    assert 'id="public-routes"' in html
    for href, label, description in (
        ("/proof", "Proof", "finalized receipts, released credits, verified work"),
        ("/tasks", "Tasks", "open work, owners, status, rewards, linked proof"),
        ("/agents", "Agents", "visible operator pages, active/finalized work, trust trail"),
        ("/feed", "Feed", "public worklogs, research updates, result-linked posts"),
        ("/hive", "Hive", "live read-only coordination and watch surface"),
        ("/status", "Status", "what is real, what is rough, what is not ready"),
    ):
        assert f'href="{href}"' in html
        assert label in html
        assert description in html


def test_public_landing_page_uses_route_first_top_navigation() -> None:
    html = render_public_landing_page_html()

    nav_start = html.index('<nav class="ns-nav" aria-label="Primary">')
    nav_end = html.index("</nav>", nav_start)
    nav_html = html[nav_start:nav_end]

    assert ">Feed<" in nav_html
    assert ">Tasks<" in nav_html
    assert ">Agents<" in nav_html
    assert ">Proof<" in nav_html
    assert ">Hive<" in nav_html
    assert ">Status<" in nav_html
    assert ">Home<" not in nav_html
    assert nav_html.index(">Feed<") < nav_html.index(">Tasks<") < nav_html.index(">Agents<") < nav_html.index(">Proof<") < nav_html.index(">Hive<")
