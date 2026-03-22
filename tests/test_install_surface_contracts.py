from __future__ import annotations

import ast
import re
from pathlib import Path

from setuptools import find_packages

REPO_ROOT = Path(__file__).resolve().parent.parent


def _setuptools_include_patterns() -> list[str]:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r"\[tool\.setuptools\.packages\.find\]\s+include = \[(.*?)\]", pyproject, re.S)
    assert match is not None, "setuptools package discovery include list missing from pyproject.toml"
    return ast.literal_eval(f"[{match.group(1)}]")


def test_packaged_runtime_includes_adapter_and_tool_roots() -> None:
    discovered = find_packages(where=str(REPO_ROOT), include=_setuptools_include_patterns())
    packaged_roots = {package.split(".")[0] for package in discovered}

    assert "adapters" in packaged_roots
    assert "tools" in packaged_roots


def test_container_and_docs_share_api_healthz_contract() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    install_doc = (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    control_plane_doc = (REPO_ROOT / "docs" / "CONTROL_PLANE.md").read_text(encoding="utf-8")
    api_server = (REPO_ROOT / "apps" / "nulla_api_server.py").read_text(encoding="utf-8")

    assert "http://localhost:11435/healthz" in dockerfile
    assert "http://127.0.0.1:11435/healthz" in install_doc
    assert "GET /healthz" in control_plane_doc
    assert '"/healthz"' in api_server
