from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_openclaw_launcher_respects_runtime_home_override() -> None:
    script = (PROJECT_ROOT / "OpenClaw_NULLA.sh").read_text(encoding="utf-8")

    assert 'export NULLA_HOME="${NULLA_HOME:-' in script


def test_openclaw_launcher_maps_profile_home_to_state_dir() -> None:
    script = (PROJECT_ROOT / "OpenClaw_NULLA.sh").read_text(encoding="utf-8")

    assert 'export OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-${OPENCLAW_HOME_OVERRIDE}}"' in script


def test_openclaw_launcher_uses_noninteractive_ollama_fallback() -> None:
    script = (PROJECT_ROOT / "OpenClaw_NULLA.sh").read_text(encoding="utf-8")

    assert 'ollama launch openclaw --yes --model "${MODEL_TAG}"' in script
