from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_install_script_autodetects_supported_python() -> None:
    script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")

    assert "resolve_python_bin()" in script
    assert "uv python find" in script
    assert "python3.11" in script


def test_install_script_rebuilds_unsupported_venv() -> None:
    script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")

    assert "Existing virtual environment uses unsupported Python. Rebuilding..." in script
    assert 'rm -rf "${VENV_DIR}"' in script


def test_install_script_hardens_openclaw_launcher_bootstrap() -> None:
    script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")

    assert 'curl -sf --max-time 2 http://127.0.0.1:11435/healthz' in script
    assert 'launch openclaw --yes --model "\\${MODEL_TAG}"' in script
    assert 'launch openclaw --yes --config --model "${model_tag}"' in script
    assert 'openclaw gateway run --force' in script
    assert '${HOME}/.openclaw-default' in script
    assert 'Skipping Ollama OpenClaw auto-config for isolated home' in script
    assert 'disown "\\${api_pid}"' in script
    assert 'disown "\\${openclaw_pid}"' in script
    assert 'open "${PROJECT_ROOT}/OpenClaw_NULLA.command"' in script
    assert 'Handing off launch to Terminal.app...' in script


def test_install_script_surfaces_machine_probe_command() -> None:
    script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")

    assert 'Probe:   ${PROJECT_ROOT}/Probe_NULLA_Stack.sh' in script


def test_public_hive_auth_helper_is_tracked() -> None:
    helper = PROJECT_ROOT / "ops" / "ensure_public_hive_auth.py"

    assert helper.exists()
    content = helper.read_text(encoding="utf-8")
    assert 'default=""' in content
    assert "from core.public_hive_bridge import ensure_public_hive_auth" in content
