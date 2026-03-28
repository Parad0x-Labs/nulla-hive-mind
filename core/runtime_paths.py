from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NULLA_HOME = (PROJECT_ROOT / ".nulla_local").resolve()
DATA_DIR = (NULLA_HOME / "data").resolve()
CONFIG_HOME_DIR = (NULLA_HOME / "config").resolve()
DOCS_DIR = (PROJECT_ROOT / "docs").resolve()
PROJECT_CONFIG_DIR = (PROJECT_ROOT / "config").resolve()
WORKSPACE_DIR = (PROJECT_ROOT / "workspace").resolve()
_NULLA_HOME_OVERRIDE: Path | None = None


def configure_runtime_home(path: str | Path | None) -> None:
    global _NULLA_HOME_OVERRIDE
    _NULLA_HOME_OVERRIDE = None if path is None else Path(path).expanduser().resolve()


def discover_installed_runtime_home(
    *,
    project_root: str | Path | None = None,
) -> Path | None:
    root = (Path(project_root).expanduser().resolve() if project_root is not None else PROJECT_ROOT.resolve())
    receipt_path = root / "install_receipt.json"
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    candidate = str(payload.get("runtime_home") or "").strip()
    if not candidate:
        return None
    try:
        return Path(candidate).expanduser().resolve()
    except Exception:
        return None


def active_nulla_home(env: Mapping[str, str] | None = None) -> Path:
    if env is None:
        if _NULLA_HOME_OVERRIDE is not None:
            return _NULLA_HOME_OVERRIDE.resolve()
        env_map = os.environ
        env_home = str(env_map.get("NULLA_HOME") or "").strip()
        if env_home:
            return Path(env_home).expanduser().resolve()
        installed_home = discover_installed_runtime_home()
        if installed_home is not None:
            return installed_home
        return NULLA_HOME.resolve()

    env_map = env
    explicit_env_home = "NULLA_HOME" in env_map
    env_home = str(env_map.get("NULLA_HOME") or "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    if _NULLA_HOME_OVERRIDE is not None and not explicit_env_home:
        return _NULLA_HOME_OVERRIDE.resolve()
    installed_home = discover_installed_runtime_home()
    if installed_home is not None:
        return installed_home
    return NULLA_HOME.resolve()


def active_data_dir() -> Path:
    return (active_nulla_home() / "data").resolve()


def active_config_home_dir() -> Path:
    return (active_nulla_home() / "config").resolve()


def active_workspace_dir() -> Path:
    return WORKSPACE_DIR.resolve()


def resolve_workspace_root(explicit: str | Path | None = None) -> Path:
    candidate = str(explicit or "").strip()
    if candidate:
        return Path(candidate).expanduser().resolve()
    override = str(
        os.environ.get("NULLA_WORKSPACE_ROOT")
        or os.environ.get("NULLA_PROJECT_ROOT")
        or ""
    ).strip()
    if override:
        return Path(override).expanduser().resolve()
    try:
        return Path.cwd().resolve()
    except FileNotFoundError:
        return PROJECT_ROOT.resolve()


def ensure_runtime_dirs() -> None:
    for path in (active_nulla_home(), active_data_dir(), active_config_home_dir(), DOCS_DIR, active_workspace_dir()):
        path.mkdir(parents=True, exist_ok=True)


def data_path(*parts: str) -> Path:
    ensure_runtime_dirs()
    return active_data_dir().joinpath(*parts).resolve()


def config_path(*parts: str) -> Path:
    ensure_runtime_dirs()
    candidate = active_config_home_dir().joinpath(*parts)
    if candidate.exists():
        return candidate.resolve()
    return PROJECT_CONFIG_DIR.joinpath(*parts).resolve()


def project_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts).resolve()


def docs_path(*parts: str) -> Path:
    ensure_runtime_dirs()
    return DOCS_DIR.joinpath(*parts).resolve()


def workspace_path(*parts: str) -> Path:
    ensure_runtime_dirs()
    return active_workspace_dir().joinpath(*parts).resolve()
