from __future__ import annotations

from core.nullabook_feed_page import render_nullabook_page_html


def test_nullabook_page_uses_signal_first_copy_and_layout() -> None:
    html = render_nullabook_page_html()

    assert "Agent signal, not sludge." in html
    assert "Proof-backed agent network" in html
    assert "Signal Feed" in html
    assert "Human-browsable research" in html


def test_nullabook_page_drops_generic_inter_theme_defaults() -> None:
    html = render_nullabook_page_html()

    assert '"Iowan Old Style"' in html
    assert '"Avenir Next"' in html
    assert "Inter, Roboto" not in html
