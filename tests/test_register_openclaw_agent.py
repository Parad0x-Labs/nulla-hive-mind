from __future__ import annotations

from installer import register_openclaw_agent as roa


def test_build_nulla_provider_honors_api_url_override(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OPENCLAW_API_URL", "http://127.0.0.1:21435")

    provider = roa._build_nulla_provider("ollama/qwen2.5:7b")

    assert provider["baseUrl"] == "http://127.0.0.1:21435"
    assert provider["models"][0]["id"] == "nulla"


def test_build_ollama_provider_uses_raw_ollama_host(monkeypatch) -> None:
    monkeypatch.delenv("NULLA_RAW_OLLAMA_API_URL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")

    provider = roa._build_ollama_provider("ollama/qwen2.5:7b")

    assert provider["baseUrl"] == "http://127.0.0.1:11434"
    assert provider["models"][0]["id"] == "qwen2.5:7b"


def test_gateway_port_honors_override(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OPENCLAW_GATEWAY_PORT", "28790")
    monkeypatch.setenv("NULLA_OLLAMA_MODEL", "qwen2.5:7b")

    cfg = roa._base_openclaw_config("/tmp/workspace")

    assert cfg["gateway"]["port"] == 28790
    assert cfg["models"]["providers"]["ollama"]["models"][0]["id"] == "qwen2.5:7b"


def test_ensure_ollama_provider_repairs_broken_nulla_alias(monkeypatch) -> None:
    monkeypatch.setenv("NULLA_OPENCLAW_API_URL", "http://127.0.0.1:21435")
    monkeypatch.setenv("NULLA_RAW_OLLAMA_API_URL", "http://127.0.0.1:11434")
    cfg = {
        "models": {
            "providers": {
                "ollama": roa._build_nulla_provider("ollama/qwen2.5:7b"),
            }
        }
    }

    roa._ensure_ollama_provider(cfg, "ollama/qwen2.5:7b")

    provider = cfg["models"]["providers"]["ollama"]
    assert provider["baseUrl"] == "http://127.0.0.1:11434"
    assert provider["models"][0]["id"] == "qwen2.5:7b"
