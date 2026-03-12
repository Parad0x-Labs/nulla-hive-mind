from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import policy_engine
from core.execution_gate import ExecutionGate
from sandbox.sandbox_runner import SandboxRunner


_EXECUTION_REQUEST_MARKERS = (
    "run ",
    "execute ",
    "command",
    "shell",
    "terminal",
    "repo",
    "repository",
    "project",
    "workspace",
    "read file",
    "open file",
    "search code",
    "find in files",
    "edit file",
    "change file",
    "patch file",
    "write file",
    "replace in file",
    "pytest",
    "rg ",
    "grep ",
)
_TOOL_INTENTS = {
    "workspace.list_files",
    "workspace.search_text",
    "workspace.read_file",
    "workspace.write_file",
    "workspace.replace_in_file",
    "sandbox.run_command",
}


@dataclass
class RuntimeExecutionResult:
    handled: bool
    ok: bool
    status: str
    response_text: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def runtime_execution_tool_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    if policy_engine.get("filesystem.allow_read_workspace", True):
        specs.extend(
            [
                {
                    "intent": "workspace.list_files",
                    "description": "List files or directories inside the active workspace.",
                    "read_only": True,
                    "arguments": {
                        "path": "string optional",
                        "glob": "string optional",
                        "limit": "integer optional",
                    },
                },
                {
                    "intent": "workspace.search_text",
                    "description": "Search text inside workspace files and return file/line matches.",
                    "read_only": True,
                    "arguments": {
                        "query": "string",
                        "path": "string optional",
                        "glob": "string optional",
                        "limit": "integer optional",
                    },
                },
                {
                    "intent": "workspace.read_file",
                    "description": "Read a workspace file with line numbers.",
                    "read_only": True,
                    "arguments": {
                        "path": "string",
                        "start_line": "integer optional",
                        "max_lines": "integer optional",
                    },
                },
            ]
        )
    if policy_engine.get("filesystem.allow_write_workspace", False):
        specs.extend(
            [
                {
                    "intent": "workspace.write_file",
                    "description": "Write full text content to a workspace file.",
                    "read_only": False,
                    "arguments": {
                        "path": "string",
                        "content": "string",
                    },
                },
                {
                    "intent": "workspace.replace_in_file",
                    "description": "Replace text inside a workspace file.",
                    "read_only": False,
                    "arguments": {
                        "path": "string",
                        "old_text": "string",
                        "new_text": "string",
                        "replace_all": "boolean optional",
                    },
                },
            ]
        )
    if policy_engine.get("execution.allow_sandbox_execution", False):
        specs.append(
            {
                "intent": "sandbox.run_command",
                "description": "Run one bounded shell command inside the active workspace with network blocked.",
                "read_only": False,
                "arguments": {
                    "command": "string",
                    "cwd": "string optional",
                },
            }
        )
    return specs


def looks_like_execution_request(user_text: str, *, task_class: str) -> bool:
    if task_class in {"debugging", "dependency_resolution", "config", "file_inspection", "shell_guidance"}:
        return True
    lowered = str(user_text or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in _EXECUTION_REQUEST_MARKERS)


def execute_runtime_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    source_context: dict[str, Any] | None = None,
) -> RuntimeExecutionResult | None:
    if intent not in _TOOL_INTENTS:
        return None
    workspace_root = _workspace_root(source_context)
    try:
        if intent == "workspace.list_files":
            return _list_files(arguments, workspace_root=workspace_root)
        if intent == "workspace.search_text":
            return _search_text(arguments, workspace_root=workspace_root)
        if intent == "workspace.read_file":
            return _read_file(arguments, workspace_root=workspace_root)
        if intent == "workspace.write_file":
            return _write_file(arguments, workspace_root=workspace_root)
        if intent == "workspace.replace_in_file":
            return _replace_in_file(arguments, workspace_root=workspace_root)
        if intent == "sandbox.run_command":
            return _run_command(arguments, workspace_root=workspace_root)
    except Exception as exc:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="error",
            response_text=f"Execution tool `{intent}` failed: {exc}",
            details={"error": str(exc)},
        )
    return RuntimeExecutionResult(
        handled=True,
        ok=False,
        status="unsupported",
        response_text=f"I won't fake it: `{intent}` is not supported by the runtime execution layer.",
    )


def _workspace_root(source_context: dict[str, Any] | None) -> Path:
    raw = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    candidate = Path(raw).resolve() if raw else Path.cwd().resolve()
    if candidate.exists():
        return candidate
    return Path.cwd().resolve()


def _resolve_workspace_path(raw_path: str | None, *, workspace_root: Path) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        return workspace_root
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (workspace_root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if candidate != workspace_root and workspace_root not in candidate.parents:
        raise ValueError("Path escapes the active workspace.")
    return candidate


def _relative_path(path: Path, *, workspace_root: Path) -> str:
    try:
        return str(path.relative_to(workspace_root)) or "."
    except Exception:
        return str(path)


def _truncate(text: str, *, limit: int = 1800) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n...[truncated]"


def _iter_workspace_files(
    target: Path,
    *,
    workspace_root: Path,
    glob_pattern: str,
    limit: int,
) -> list[Path]:
    if target.is_file():
        return [target]
    matches: list[Path] = []
    for path in sorted(target.rglob("*")):
        if len(matches) >= limit:
            break
        if not path.is_file():
            continue
        relative = _relative_path(path, workspace_root=workspace_root)
        if any(part.startswith(".") for part in Path(relative).parts):
            continue
        if glob_pattern not in {"", "*", "**", "**/*"} and not fnmatch.fnmatch(relative, glob_pattern) and not fnmatch.fnmatch(path.name, glob_pattern):
            continue
        matches.append(path)
    return matches


def _is_probably_text(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:4096]
    except Exception:
        return False
    return b"\x00" not in sample


def _list_files(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    limit = max(1, min(int(arguments.get("limit") or 50), 200))
    glob_pattern = str(arguments.get("glob") or "**/*").strip() or "**/*"
    if target.is_file():
        rows = [target]
    else:
        rows = _iter_workspace_files(target, workspace_root=workspace_root, glob_pattern=glob_pattern, limit=limit)
    if not rows:
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="no_results",
            response_text=f"No files matched inside `{_relative_path(target, workspace_root=workspace_root)}`.",
            details={"path": _relative_path(target, workspace_root=workspace_root)},
        )
    lines = [f"Workspace files under `{_relative_path(target, workspace_root=workspace_root)}`:"]
    for path in rows[:limit]:
        lines.append(f"- {_relative_path(path, workspace_root=workspace_root)}")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(lines),
        details={"path": _relative_path(target, workspace_root=workspace_root), "count": len(rows[:limit])},
    )


def _search_text(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="workspace.search_text needs a non-empty `query`.",
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    limit = max(1, min(int(arguments.get("limit") or 20), 100))
    glob_pattern = str(arguments.get("glob") or "**/*").strip() or "**/*"
    matches: list[str] = []
    lowered = query.lower()
    for path in _iter_workspace_files(target, workspace_root=workspace_root, glob_pattern=glob_pattern, limit=500):
        if not _is_probably_text(path):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for index, line in enumerate(lines, start=1):
            if lowered not in line.lower():
                continue
            relative = _relative_path(path, workspace_root=workspace_root)
            matches.append(f"- {relative}:{index} {line.strip()[:220]}")
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    if not matches:
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="no_results",
            response_text=f'No text matches for "{query}" were found in the workspace.',
            details={"query": query},
        )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=f'Search matches for "{query}":\n' + "\n".join(matches),
        details={"query": query, "match_count": len(matches)},
    )


def _read_file(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    if not target.exists() or not target.is_file():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` does not exist.",
        )
    if not _is_probably_text(target):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="binary_file",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` does not look like readable text.",
        )
    start_line = max(1, int(arguments.get("start_line") or 1))
    max_lines = max(1, min(int(arguments.get("max_lines") or 160), 400))
    content = target.read_text(encoding="utf-8", errors="replace").splitlines()
    chunk = content[start_line - 1 : start_line - 1 + max_lines]
    if not chunk:
        return RuntimeExecutionResult(
            handled=True,
            ok=True,
            status="empty_slice",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` has no lines in that range.",
        )
    numbered = [f"{start_line + offset}: {line}" for offset, line in enumerate(chunk)]
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}`:\n" + "\n".join(numbered),
        details={
            "path": _relative_path(target, workspace_root=workspace_root),
            "start_line": start_line,
            "line_count": len(chunk),
        },
    )


def _write_file(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    content = str(arguments.get("content") or "")
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_text(content, encoding="utf-8")
    line_count = len(content.splitlines()) or (1 if content else 0)
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=(
            f"{'Updated' if existed else 'Created'} file `{_relative_path(target, workspace_root=workspace_root)}` "
            f"with {line_count} lines."
        ),
        details={"path": _relative_path(target, workspace_root=workspace_root), "line_count": line_count},
    )


def _replace_in_file(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    if not target.exists() or not target.is_file():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` does not exist.",
        )
    old_text = str(arguments.get("old_text") or "")
    new_text = str(arguments.get("new_text") or "")
    replace_all = bool(arguments.get("replace_all", False))
    if not old_text:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="workspace.replace_in_file needs non-empty `old_text`.",
        )
    content = target.read_text(encoding="utf-8", errors="replace")
    occurrences = content.count(old_text)
    if occurrences <= 0:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="no_match",
            response_text=f"`old_text` was not found in `{_relative_path(target, workspace_root=workspace_root)}`.",
        )
    if replace_all:
        updated = content.replace(old_text, new_text)
        replaced = occurrences
    else:
        updated = content.replace(old_text, new_text, 1)
        replaced = 1
    target.write_text(updated, encoding="utf-8")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=(
            f"Applied {replaced} replacement{'s' if replaced != 1 else ''} in "
            f"`{_relative_path(target, workspace_root=workspace_root)}`."
        ),
        details={
            "path": _relative_path(target, workspace_root=workspace_root),
            "replacements": replaced,
        },
    )


def _run_command(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    command = str(arguments.get("command") or arguments.get("cmd") or "").strip()
    if not command:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="sandbox.run_command needs a non-empty `command`.",
        )
    raw_cwd = arguments.get("cwd")
    cwd = _resolve_workspace_path(raw_cwd, workspace_root=workspace_root) if raw_cwd else workspace_root
    if cwd.is_file():
        cwd = cwd.parent
    runner = SandboxRunner(ExecutionGate(), str(workspace_root))
    result = runner.run_command(command, cwd=str(cwd))
    status = str(result.get("status") or "")
    if status and status != "executed":
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status=status,
            response_text=str(result.get("error") or f"Command could not run: {status}"),
            details=dict(result),
        )
    stdout = _truncate(str(result.get("stdout") or ""), limit=2400)
    stderr = _truncate(str(result.get("stderr") or ""), limit=1600)
    lines = [
        f"Command executed in `{_relative_path(cwd, workspace_root=workspace_root)}`:",
        f"$ {command}",
        f"- Exit code: {int(result.get('returncode', 0) or 0)}",
    ]
    if stdout:
        lines.append(f"- Stdout:\n{stdout}")
    if stderr:
        lines.append(f"- Stderr:\n{stderr}")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(lines),
        details={
            "command": command,
            "cwd": _relative_path(cwd, workspace_root=workspace_root),
            "returncode": int(result.get("returncode", 0) or 0),
            "success": bool(result.get("success", False)),
            "stdout": stdout,
            "stderr": stderr,
        },
    )
