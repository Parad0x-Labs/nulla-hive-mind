from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NULLA_HOME = Path(os.environ.get("NULLA_HOME", PROJECT_ROOT / ".nulla_local")).resolve()
DATA_DIR = (NULLA_HOME / "data").resolve()
CONFIG_HOME_DIR = (NULLA_HOME / "config").resolve()
DOCS_DIR = (PROJECT_ROOT / "docs").resolve()
PROJECT_CONFIG_DIR = (PROJECT_ROOT / "config").resolve()
WORKSPACE_DIR = (PROJECT_ROOT / "workspace").resolve()
_NULLA_HOME_OVERRIDE: Path | None = None


def configure_runtime_home(path: str | Path | None) -> None:
    global _NULLA_HOME_OVERRIDE
    _NULLA_HOME_OVERRIDE = None if path is None else Path(path).expanduser().resolve()


def active_nulla_home() -> Path:
    return (_NULLA_HOME_OVERRIDE or Path(os.environ.get("NULLA_HOME", NULLA_HOME))).resolve()


def active_data_dir() -> Path:
    return (active_nulla_home() / "data").resolve()


def active_config_home_dir() -> Path:
    return (active_nulla_home() / "config").resolve()


def active_workspace_dir() -> Path:
    return WORKSPACE_DIR.resolve()


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
