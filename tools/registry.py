from __future__ import annotations

from typing import Any, Callable

TOOLS: dict[str, Callable[..., Any]] = {}


def register(name: str, fn: Callable[..., Any]) -> None:
    TOOLS[name] = fn


def load_builtin_tools() -> None:
    from tools.browser.browser_render import browser_render
    from tools.web.ddg_instant import ddg_instant_answer
    from tools.web.http_fetch import http_fetch_text
    from tools.web.searxng_client import SearXNGClient
    from tools.web.web_research import web_research

    client = SearXNGClient()
    register("web.search", client.search)
    register("web.ddg_instant", ddg_instant_answer)
    register("web.fetch", http_fetch_text)
    register("web.research", web_research)
    register("browser.render", browser_render)


def call_tool(name: str, **kwargs: Any) -> Any:
    if name not in TOOLS:
        raise KeyError(f"Unknown tool: {name}")
    return TOOLS[name](**kwargs)
