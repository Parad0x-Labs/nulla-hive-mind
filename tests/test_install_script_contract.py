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

    assert "--install-profile <profile>" in script
    assert 'validate_selected_install_profile() {' in script
    assert '"${SCRIPT_DIR}/validate_install_profile.py"' in script
    assert 'export NULLA_INSTALL_PROFILE="\\${NULLA_INSTALL_PROFILE:-${install_profile}}"' in script
    assert 'Recommended profile: ${recommended_install_profile}' in script
    assert 'wait_for_http_ready() {' in script
    assert 'curl -sf --max-time 2 "\\${url}" >/dev/null 2>&1' in script
    assert 'cd "${PROJECT_ROOT}"' in script
    assert 'export NULLA_HOME="\\${NULLA_HOME:-${runtime_home}}"' in script
    assert 'export NULLA_OPENCLAW_API_URL="\\${NULLA_OPENCLAW_API_URL:-http://127.0.0.1:\\${NULLA_OPENCLAW_API_PORT}}"' in script
    assert 'nohup "\\${VENV_PY}" -m apps.nulla_api_server --port "\\${NULLA_OPENCLAW_API_PORT}"' in script
    assert 'wait_for_http_ready "\\${NULLA_OPENCLAW_API_URL}/healthz" 30 "\\${api_pid}" 3' in script
    assert 'launch openclaw --yes --model "\\${MODEL_TAG}"' in script
    assert 'launch openclaw --yes --config --model "${model_tag}"' in script
    assert 'openclaw gateway run --force' in script
    assert '${HOME}/.openclaw-default' in script
    assert 'Skipping Ollama OpenClaw auto-config for isolated home' in script
    assert 'disown "\\${api_pid}"' in script
    assert 'disown "\\${openclaw_pid}"' in script
    assert 'open "${PROJECT_ROOT}/OpenClaw_NULLA.command"' in script
    assert 'Handing off launch to Terminal.app...' in script


def test_install_wrappers_forward_install_profile_and_extra_args() -> None:
    install_and_run = (PROJECT_ROOT / "Install_And_Run_NULLA.sh").read_text(encoding="utf-8")
    install_and_run_bat = (PROJECT_ROOT / "Install_And_Run_NULLA.bat").read_text(encoding="utf-8")
    install_bat = (PROJECT_ROOT / "Install_NULLA.bat").read_text(encoding="utf-8")
    install_bat_script = (PROJECT_ROOT / "installer" / "install_nulla.bat").read_text(encoding="utf-8")

    assert '--start "$@"' in install_and_run
    assert "%*" in install_and_run_bat
    assert "%*" in install_bat
    assert "requested_profile=r'%NULLA_INSTALL_PROFILE%'" in install_bat_script
    assert '"%SCRIPT_DIR%validate_install_profile.py"' in install_bat_script


def test_windows_launchers_use_module_entrypoint_for_api_server() -> None:
    install_bat_script = (PROJECT_ROOT / "installer" / "install_nulla.bat").read_text(encoding="utf-8")

    assert '"%VENV_DIR%\\Scripts\\python.exe" -m apps.nulla_api_server' in install_bat_script


def test_install_script_surfaces_machine_probe_command() -> None:
    script = (PROJECT_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")

    assert 'Probe:   ${PROJECT_ROOT}/Probe_NULLA_Stack.sh' in script


def test_public_hive_auth_helper_is_tracked() -> None:
    helper = PROJECT_ROOT / "ops" / "ensure_public_hive_auth.py"

    assert helper.exists()
    content = helper.read_text(encoding="utf-8")
    assert 'default=""' in content
    assert "from core.public_hive_bridge import ensure_public_hive_auth" in content
