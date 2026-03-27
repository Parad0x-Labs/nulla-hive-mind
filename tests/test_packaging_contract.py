from __future__ import annotations

from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_extra_is_declared() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    extras = pyproject["project"]["optional-dependencies"]
    assert "runtime" in extras
    assert "playwright>=1.52" in extras["runtime"]


def test_watch_entrypoint_is_declared() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    scripts = pyproject["project"]["scripts"]
    assert scripts["nulla-watch"] == "ops.run_brain_hive_watch_from_config:main"


def test_package_find_includes_runtime_support_roots() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    include = pyproject["tool"]["setuptools"]["packages"]["find"]["include"]
    assert "adapters*" in include
    assert "relay*" in include
    assert "tools*" in include


def test_relay_runtime_root_is_a_real_package() -> None:
    assert (REPO_ROOT / "relay" / "__init__.py").is_file()
    assert (REPO_ROOT / "relay" / "bridge_workers" / "__init__.py").is_file()
