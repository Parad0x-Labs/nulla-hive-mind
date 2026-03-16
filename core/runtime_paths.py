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


def ensure_runtime_dirs() -> None:
    for path in (NULLA_HOME, DATA_DIR, CONFIG_HOME_DIR, DOCS_DIR, WORKSPACE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def data_path(*parts: str) -> Path:
    ensure_runtime_dirs()
    return DATA_DIR.joinpath(*parts).resolve()


def config_path(*parts: str) -> Path:
    ensure_runtime_dirs()
    candidate = CONFIG_HOME_DIR.joinpath(*parts)
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
    return WORKSPACE_DIR.joinpath(*parts).resolve()
