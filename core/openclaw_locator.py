from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

NULLA_AGENT_ID = "nulla"


@dataclass(frozen=True)
class OpenClawPaths:
    home: Path
    config_path: Path
    workspace_dir: Path
    agent_dir: Path
    agent_runtime_dir: Path
    compat_bridge_dir: Path
    source: str
    discovered_existing: bool


def discover_openclaw_paths(
    *,
    explicit_home: str | Path | None = None,
    explicit_config_path: str | Path | None = None,
    create_default: bool = False,
) -> OpenClawPaths:
    direct_config = _clean_path(explicit_config_path) or _clean_path(os.environ.get("OPENCLAW_CONFIG_PATH"))
    if direct_config is not None:
        home = direct_config.parent
        return _build_paths(
            home=home,
            config_path=direct_config,
            source="explicit_config" if explicit_config_path else "env_config",
            discovered_existing=direct_config.is_file() or home.exists(),
        )

    direct_home = _clean_path(explicit_home) or _clean_path(os.environ.get("OPENCLAW_HOME"))
    if direct_home is not None:
        return _build_paths(
            home=direct_home,
            source="explicit_home" if explicit_home else "env_home",
            discovered_existing=direct_home.exists() or (direct_home / "openclaw.json").is_file(),
        )

    ranked: list[tuple[int, int, Path, str]] = []
    for idx, (home, source) in enumerate(_candidate_homes()):
        ranked.append((_score_home(home), -idx, home, source))
    ranked.sort(reverse=True)
    if ranked and ranked[0][0] > 0:
        _, _, home, source = ranked[0]
        return _build_paths(home=home, source=source, discovered_existing=True)

    default_home = _default_openclaw_home()
    return _build_paths(
        home=default_home,
        source="default_home",
        discovered_existing=default_home.exists() if create_default else False,
    )


def load_openclaw_config(paths: OpenClawPaths | None = None) -> dict:
    paths = paths or discover_openclaw_paths(create_default=False)
    if not paths.config_path.is_file():
        return {}
    try:
        return json.loads(paths.config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_gateway_token(paths: OpenClawPaths | None = None) -> str:
    cfg = load_openclaw_config(paths)
    return str(cfg.get("gateway", {}).get("auth", {}).get("token", "") or "").strip()


def load_registered_agent_name(
    agent_id: str = NULLA_AGENT_ID,
    *,
    paths: OpenClawPaths | None = None,
) -> str:
    cfg = load_openclaw_config(paths)
    for entry in cfg.get("agents", {}).get("list", []) or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id", "")).lower() != str(agent_id).lower():
            continue
        identity = entry.get("identity") or {}
        return str(entry.get("name") or identity.get("name") or "").strip()
    return ""


def _default_openclaw_home() -> Path:
    home = Path.home()
    return home / ".openclaw"


def _candidate_homes() -> list[tuple[Path, str]]:
    home = Path.home()
    candidates: list[tuple[Path, str]] = [
        (_default_openclaw_home(), "dot_home"),
        (home / ".config" / "openclaw", "xdg_config_lower"),
        (home / ".config" / "OpenClaw", "xdg_config_title"),
        (home / ".local" / "share" / "openclaw", "xdg_share_lower"),
        (home / ".local" / "share" / "OpenClaw", "xdg_share_title"),
        (home / "Library" / "Application Support" / "OpenClaw", "macos_app_support"),
    ]
    for env_name, source in (
        ("APPDATA", "windows_appdata"),
        ("LOCALAPPDATA", "windows_localappdata"),
    ):
        env_path = _clean_path(os.environ.get(env_name))
        if env_path is not None:
            candidates.append((env_path / "OpenClaw", source))
            candidates.append((env_path / "openclaw", f"{source}_lower"))
    return _dedupe_candidates(candidates)


def _dedupe_candidates(items: list[tuple[Path, str]]) -> list[tuple[Path, str]]:
    seen: set[str] = set()
    result: list[tuple[Path, str]] = []
    for path, source in items:
        key = str(path.expanduser())
        if key in seen:
            continue
        seen.add(key)
        result.append((path, source))
    return result


def _score_home(home: Path) -> int:
    score = 0
    config_path = home / "openclaw.json"
    if config_path.is_file():
        score += 100
    if (home / "agents").is_dir():
        score += 30
    if (home / "agents" / "main" / "agent").is_dir():
        score += 20
    if (home / "workspace").is_dir():
        score += 10
    if home.exists():
        score += 5
    return score


def _clean_path(value: str | Path | None) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _build_paths(
    *,
    home: Path,
    source: str,
    discovered_existing: bool,
    config_path: Path | None = None,
) -> OpenClawPaths:
    resolved_home = home.expanduser()
    resolved_config = (config_path or (resolved_home / "openclaw.json")).expanduser()
    return OpenClawPaths(
        home=resolved_home,
        config_path=resolved_config,
        workspace_dir=resolved_home / "workspace",
        agent_dir=resolved_home / "agents" / NULLA_AGENT_ID,
        agent_runtime_dir=resolved_home / "agents" / NULLA_AGENT_ID / "agent",
        compat_bridge_dir=resolved_home / "agents" / "main" / "agent" / NULLA_AGENT_ID,
        source=source,
        discovered_existing=discovered_existing,
    )
