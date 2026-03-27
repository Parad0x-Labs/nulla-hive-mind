from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _render_openclaw_launcher(tmp_path: Path) -> str:
    installer_script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")
    prefix, marker, _ = installer_script.partition('\nparse_args "$@"\n')

    assert marker

    harness = tmp_path / "render_openclaw_launcher.sh"
    output = tmp_path / "OpenClaw_NULLA.sh"
    harness.write_text(
        prefix + '\nwrite_openclaw_launcher "$1" "$2" "$3" "$4"\n',
        encoding="utf-8",
    )
    subprocess.run(
        [
            "bash",
            str(harness),
            str(output),
            "/tmp/nulla-runtime-home",
            "qwen2.5:14b",
            "/tmp/.openclaw-default",
        ],
        check=True,
        cwd=PROJECT_ROOT,
    )
    return output.read_text(encoding="utf-8")


def test_openclaw_launcher_respects_runtime_home_override(tmp_path: Path) -> None:
    script = _render_openclaw_launcher(tmp_path)

    assert 'export NULLA_HOME="${NULLA_HOME:-/tmp/nulla-runtime-home}"' in script


def test_openclaw_launcher_maps_profile_home_to_state_dir(tmp_path: Path) -> None:
    script = _render_openclaw_launcher(tmp_path)

    assert 'export OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-/tmp/.openclaw-default}"' in script


def test_openclaw_launcher_uses_noninteractive_ollama_fallback(tmp_path: Path) -> None:
    script = _render_openclaw_launcher(tmp_path)

    assert 'ollama launch openclaw --yes --model "${MODEL_TAG}"' in script


def test_openclaw_launcher_exports_project_root_and_health_waits(tmp_path: Path) -> None:
    script = _render_openclaw_launcher(tmp_path)

    assert 'cd "${PROJECT_ROOT}"' in script
    assert 'nohup "${VENV_PY}" -m apps.nulla_api_server --port "${NULLA_OPENCLAW_API_PORT}"' in script
    assert 'wait_for_http_ready() {' in script
    assert 'export NULLA_OPENCLAW_API_URL="${NULLA_OPENCLAW_API_URL:-http://127.0.0.1:${NULLA_OPENCLAW_API_PORT}}"' in script
    assert 'wait_for_http_ready "${NULLA_OPENCLAW_API_URL}/healthz" 30 "${api_pid}" 3' in script
