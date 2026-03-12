from __future__ import annotations

from installer.write_install_receipt import build_receipt


def test_install_receipt_includes_enabled_web_stack_defaults() -> None:
    receipt = build_receipt(
        project_root="/tmp/nulla",
        runtime_home="/tmp/nulla-home",
        model_tag="qwen2.5:7b",
        openclaw_enabled=True,
        openclaw_config_path="/tmp/openclaw.json",
        openclaw_agent_dir="/tmp/agent",
        ollama_binary="ollama",
    )

    web_stack = receipt["web_stack"]
    assert web_stack["provider_order"] == ["searxng", "ddg_instant", "duckduckgo_html"]
    assert web_stack["searxng_url"] == "http://127.0.0.1:8080"
    assert web_stack["playwright_enabled"] is True
    assert web_stack["browser_engine"] == "chromium"
