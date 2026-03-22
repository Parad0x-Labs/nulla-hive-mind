from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _setuptools_include_patterns() -> list[str]:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"\[tool\.setuptools\.packages\.find\]\s+include = \[(.*?)\]", pyproject, re.S)
    assert match is not None, "setuptools package discovery include list missing from pyproject.toml"
    return ast.literal_eval(f"[{match.group(1)}]")


def test_pyproject_package_discovery_lists_runtime_package_roots() -> None:
    include = set(_setuptools_include_patterns())
    model_registry = (REPO_ROOT / "core" / "model_registry.py").read_text(encoding="utf-8")
    tool_executor = (REPO_ROOT / "core" / "tool_intent_executor.py").read_text(encoding="utf-8")
    channel_actions = (REPO_ROOT / "core" / "channel_actions.py").read_text(encoding="utf-8")
    onboarding = (REPO_ROOT / "core" / "onboarding.py").read_text(encoding="utf-8")

    assert "adapters*" in include
    assert "tools*" in include
    assert "relay*" in include
    assert "installer*" in include
    assert (REPO_ROOT / "adapters" / "__init__.py").exists()
    assert (REPO_ROOT / "tools" / "__init__.py").exists()
    assert (REPO_ROOT / "relay" / "__init__.py").exists()
    assert (REPO_ROOT / "relay" / "bridge_workers" / "__init__.py").exists()
    assert (REPO_ROOT / "installer" / "__init__.py").exists()
    assert "from adapters." in model_registry
    assert "from tools.registry" in tool_executor
    assert "from relay." in channel_actions
    assert "from installer.register_openclaw_agent import register" in onboarding


def test_pyproject_runtime_extra_covers_installer_runtime_surface() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    for marker in (
        "runtime = [",
        '"openai>=1.0"',
        '"anthropic>=0.18"',
        '"sentence-transformers>=2.2"',
        '"torch>=2.5"',
        '"transformers>=4.48"',
        '"playwright>=1.52,<2.0"',
        '"zstandard>=0.22.0"',
        '"xxhash>=3.4.0"',
    ):
        assert marker in pyproject


def test_container_and_docs_share_api_healthz_contract() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    install_doc = (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    control_plane_doc = (REPO_ROOT / "docs" / "CONTROL_PLANE.md").read_text(encoding="utf-8")
    api_server = (REPO_ROOT / "apps" / "nulla_api_server.py").read_text(encoding="utf-8")
    api_service = (REPO_ROOT / "core" / "web" / "api" / "service.py").read_text(encoding="utf-8")

    assert "http://localhost:11435/healthz" in dockerfile
    assert "http://127.0.0.1:11435/healthz" in install_doc
    assert "GET /healthz" in control_plane_doc
    assert "create_api_app" in api_server
    assert '"/healthz"' in api_service
    assert '"/v1/healthz"' in api_service


def test_installers_use_module_entrypoints_and_runtime_extra_without_pythonpath_hacks() -> None:
    sh_installer = (REPO_ROOT / "installer" / "install_nulla.sh").read_text(encoding="utf-8")
    bat_installer = (REPO_ROOT / "installer" / "install_nulla.bat").read_text(encoding="utf-8")

    assert 'pip install "${PROJECT_ROOT}[runtime]"' in sh_installer
    assert 'pip install "%PROJECT_ROOT%[runtime]"' in bat_installer
    assert "-m storage.migrations" in sh_installer
    assert "-m storage.migrations" in bat_installer
    assert "-m ops.ensure_public_hive_auth" in sh_installer
    assert "-m ops.ensure_public_hive_auth" in bat_installer
    assert "PYTHONPATH" not in sh_installer
    assert "PYTHONPATH" not in bat_installer
    assert "ops/ensure_public_hive_auth.py" not in sh_installer
    assert "ops\\ensure_public_hive_auth.py" not in bat_installer
    assert (REPO_ROOT / "ops" / "ensure_public_hive_auth.py").exists()
