from __future__ import annotations

import shlex
from pathlib import PurePath


_NETWORK_BINARIES = {
    "curl",
    "wget",
    "nc",
    "ncat",
    "netcat",
    "telnet",
    "ssh",
    "scp",
    "rsync",
    "socat",
    "ftp",
    "tftp",
    "http",
    "https",
}

_INTERPRETER_BINARIES = {"python", "python3", "node", "nodejs", "php", "ruby", "perl"}
_PACKAGE_MANAGER_BINARIES = {"npm", "npx", "pnpm", "yarn", "pip", "pip3", "poetry", "cargo", "brew", "git"}
_NETWORK_KEYWORDS = {
    "http://",
    "https://",
    "socket",
    "urllib",
    "requests",
    "http.client",
    "aiohttp",
    "websocket",
    "fetch(",
    "axios",
    "curl",
    "wget",
}
_PACKAGE_MANAGER_NETWORK_SUBCOMMANDS = {
    "npm": {"install", "add", "update", "upgrade", "audit", "publish", "login", "whoami", "view"},
    "npx": {"*"},
    "pnpm": {"install", "add", "update", "up", "audit", "publish", "login", "whoami", "view", "dlx"},
    "yarn": {"install", "add", "upgrade", "up", "audit", "publish", "login", "whoami", "info", "dlx"},
    "pip": {"install", "download", "wheel", "index"},
    "pip3": {"install", "download", "wheel", "index"},
    "poetry": {"install", "add", "update", "publish", "source"},
    "cargo": {"install", "update", "fetch", "publish", "search"},
    "brew": {"install", "update", "upgrade", "tap", "untap", "search", "info"},
    "git": {"clone", "fetch", "pull", "push", "ls-remote", "remote", "submodule"},
}


def _env_unwrapped(argv: list[str]) -> list[str]:
    if not argv:
        return []
    if _basename(argv[0]) != "env":
        return list(argv)
    index = 1
    while index < len(argv):
        token = str(argv[index] or "")
        if "=" in token and not token.startswith("-"):
            index += 1
            continue
        break
    return list(argv[index:])


def _basename(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        return ""
    return PurePath(value).name.lower()


def command_uses_network(argv: list[str]) -> bool:
    if not argv:
        return False
    normalized_argv = _env_unwrapped(argv)
    if not normalized_argv:
        return False
    first = _basename(normalized_argv[0])
    if first in _NETWORK_BINARIES:
        return True
    if first in _PACKAGE_MANAGER_BINARIES and _package_manager_uses_network(normalized_argv):
        return True
    if first in _INTERPRETER_BINARIES:
        joined = " ".join(str(item or "") for item in normalized_argv[1:]).lower()
        if any(keyword in joined for keyword in _NETWORK_KEYWORDS):
            return True
        if any(keyword in joined for keyword in ("pip install", "npm install", "pnpm install", "yarn add", "cargo install", "git clone")):
            return True
    joined = " ".join(normalized_argv)
    return "http://" in joined or "https://" in joined


def parse_command(cmd: str) -> list[str]:
    return shlex.split(cmd, posix=True)


def _package_manager_uses_network(argv: list[str]) -> bool:
    if not argv:
        return False
    base = _basename(argv[0])
    if base not in _PACKAGE_MANAGER_BINARIES:
        return False
    subcommands = [str(item or "").strip().lower() for item in argv[1:] if str(item or "").strip()]
    if not subcommands:
        return False
    if base == "git" and subcommands[:1] == ["remote"]:
        return any(token in {"add", "set-url"} for token in subcommands[1:2])
    wanted = _PACKAGE_MANAGER_NETWORK_SUBCOMMANDS.get(base) or set()
    if "*" in wanted:
        return True
    return any(token in wanted for token in subcommands[:2])
