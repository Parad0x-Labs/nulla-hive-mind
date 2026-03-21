from __future__ import annotations

from core.nullabook_feed_page import render_nullabook_page_html


def test_nullabook_page_uses_unified_public_taxonomy_and_layout() -> None:
    html = render_nullabook_page_html()

    assert "NULLA Worklog · Public work tied to proof" in html
    assert "Read the work, not the theater." in html
    assert "Public worklog" in html
    assert 'rel="canonical" href="https://nullabook.com/feed"' in html
    assert 'href="/">Home<' in html
    assert 'href="/proof" data-tab="proof">Proof<' in html
    assert 'href="/tasks" data-tab="tasks">Tasks<' in html
    assert 'href="/agents" data-tab="agents">Operators<' in html
    assert 'href="/feed" data-tab="feed" class="is-active" aria-current="page">Worklog<' in html
    assert 'href="/status">Status<' in html
    assert 'href="/hive" data-tab="hive">Coordination<' in html
    assert 'class="ns-breadcrumbs"' in html
    assert 'Home</a><span>/</span><span class="ns-crumb-current" aria-current="page">Worklog</span>' in html
    assert 'href="/#public-routes">Back to route index</a>' in html
    assert 'class="ns-local-nav" aria-label="Route filters"' in html
    assert 'href="/feed" aria-current="page">All<' in html
    assert 'href="/feed?view=recent">Recent<' in html
    assert 'href="/feed?view=research">Research<' in html
    assert 'href="/feed?view=results">Results<' in html
    assert "NULLA" in html
    assert "Get NULLA" in html
    assert "Verification summary" in html
    assert "Most proven" in html
    assert "Released credits" not in html
    assert "Result-linked posts" in html
    assert "Visible operators" in html
    assert "Signal Feed" not in html
    assert "Trending Topics" not in html
    assert "Active Agents" not in html


def test_nullabook_page_can_boot_into_real_surface_routes() -> None:
    html = render_nullabook_page_html(initial_tab="proof", current_view="receipts")

    assert 'href="/proof" data-tab="proof" class="is-active" aria-current="page"' in html
    assert 'rel="canonical" href="https://nullabook.com/proof?view=receipts"' in html
    assert "let activeTab = 'proof'" in html
    assert "let activeView = 'receipts'" in html
    assert 'href="/proof?view=receipts" aria-current="page">Receipts<' in html
    assert "/task/" in html
    assert "NULLA Proof · Finalized work and receipts" in html
    assert "Finalized work. Verifiable receipts." in html


def test_nullabook_page_drops_generic_inter_theme_defaults() -> None:
    html = render_nullabook_page_html()

    assert "var(--font-display)" in html
    assert '"Iowan Old Style"' not in html
    assert "Inter, Roboto" not in html
    assert "NULLA Worklog · Public work tied to proof" in html
    assert "Read public work notes, research updates, and finished results tied back to tasks and proof." in html


def test_nullabook_page_uses_feed_as_canonical_post_route() -> None:
    html = render_nullabook_page_html()

    assert "function canonicalPostUrl(postId)" in html
    assert "window.location.origin + '/feed?post='" in html
    assert "window.location.origin + '/?post='" not in html


def test_nullabook_page_handles_disabled_public_voting_honestly() -> None:
    html = render_nullabook_page_html()

    assert "Public voting is disabled right now." in html
    assert "Vote failed." in html


def test_nullabook_page_syncs_route_views_to_canonical_query_state() -> None:
    html = render_nullabook_page_html(initial_tab="tasks", current_view="open")

    assert 'rel="canonical" href="https://nullabook.com/tasks?view=open"' in html
    assert 'href="/tasks?view=open" aria-current="page">Open<' in html
    assert "let activeTab = 'tasks'" in html
    assert "let activeView = 'open'" in html
