from __future__ import annotations

import difflib
import fnmatch
import re
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
    "write files",
    "replace in file",
    "folder",
    "directory",
    "mkdir",
    "start coding",
    "initial files",
    "bootstrap",
    "pytest",
    "rg ",
    "grep ",
)
_TOOL_INTENTS = {
    "workspace.list_files",
    "workspace.search_text",
    "workspace.read_file",
    "workspace.ensure_directory",
    "workspace.write_file",
    "workspace.replace_in_file",
    "sandbox.run_command",
}
_FILE_LINE_RE = re.compile(r"(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+):(?P<line>\d+)")


@dataclass
class RuntimeExecutionResult:
    handled: bool
    ok: bool
    status: str
    response_text: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _tool_observation(
    *,
    intent: str,
    tool_surface: str,
    ok: bool,
    status: str,
    **payload: Any,
) -> dict[str, Any]:
    observation = {
        "schema": "tool_observation_v1",
        "intent": str(intent or "").strip(),
        "tool_surface": str(tool_surface or "").strip(),
        "ok": bool(ok),
        "status": str(status or "").strip(),
    }
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        observation[str(key)] = value
    return observation


def _diff_preview(*, before: str, after: str, path: str, limit: int = 1600) -> str:
    diff_lines = list(
        difflib.unified_diff(
            str(before or "").splitlines(),
            str(after or "").splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=2,
        )
    )
    if not diff_lines:
        return ""
    preview = "\n".join(diff_lines[:40]).strip()
    if len(preview) <= limit:
        return preview
    return preview[: max(1, limit - 3)].rstrip() + "..."


def _extract_failure_summary(*, command: str, stdout: str, stderr: str, returncode: int) -> str:
    if int(returncode or 0) == 0:
        return ""
    combined = "\n".join(part for part in (str(stderr or "").strip(), str(stdout or "").strip()) if part).splitlines()
    hot_markers = ("FAILED", "FAIL", "ERROR", "AssertionError", "Traceback", "Exception", "E   ")
    for raw_line in combined:
        line = " ".join(str(raw_line or "").split()).strip()
        if not line:
            continue
        if any(marker in line for marker in hot_markers):
            return line[:260]
    for raw_line in combined:
        line = " ".join(str(raw_line or "").split()).strip()
        if line:
            return line[:260]
    return f"`{command}` exited with code {int(returncode or 0)}"


def runtime_execution_capability_ledger() -> list[dict[str, Any]]:
    read_enabled = bool(policy_engine.get("filesystem.allow_read_workspace", True))
    write_enabled = bool(policy_engine.get("filesystem.allow_write_workspace", False))
    sandbox_enabled = bool(policy_engine.get("execution.allow_sandbox_execution", False))
    return [
        {
            "capability_id": "workspace.read",
            "surface": "workspace",
            "claim": "list files, search text, and read files in the active workspace",
            "supported": read_enabled,
            "unsupported_reason": "Workspace read actions are disabled on this runtime.",
            "intents": [
                "workspace.list_files",
                "workspace.search_text",
                "workspace.read_file",
            ],
            "public_tag": "workspace.read",
        },
        {
            "capability_id": "workspace.write",
            "surface": "workspace",
            "claim": "create directories, write files, and replace text in the active workspace",
            "supported": write_enabled,
            "unsupported_reason": "Workspace write actions are disabled on this runtime.",
            "intents": [
                "workspace.ensure_directory",
                "workspace.write_file",
                "workspace.replace_in_file",
            ],
            "public_tag": "workspace.write",
        },
        {
            "capability_id": "sandbox.command",
            "surface": "sandbox",
            "claim": "run bounded local commands in the active workspace with network blocked",
            "supported": sandbox_enabled,
            "unsupported_reason": "Sandbox command execution is disabled on this runtime.",
            "intents": ["sandbox.run_command"],
            "public_tag": "sandbox.command",
        },
    ]


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
                    "intent": "workspace.ensure_directory",
                    "description": "Create a directory inside the active workspace.",
                    "read_only": False,
                    "arguments": {
                        "path": "string",
                    },
                },
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


def extract_observation_followup_hints(observation: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(observation or {})
    intent = str(payload.get("intent") or "").strip()
    if not intent:
        return {}
    if intent == "workspace.search_text":
        matches = [dict(item) for item in list(payload.get("matches") or []) if isinstance(item, dict)]
        primary = dict((matches[:1] or [{}])[0] or {})
        return {
            "intent": intent,
            "match_count": int(payload.get("match_count") or len(matches)),
            "paths": [str(item.get("path") or "").strip() for item in matches if str(item.get("path") or "").strip()],
            "primary_path": str(primary.get("path") or "").strip(),
            "primary_line": int(primary.get("line") or 0) if str(primary.get("line") or "").strip() else 0,
            "primary_snippet": str(primary.get("snippet") or "").strip(),
        }
    if intent == "workspace.read_file":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "start_line": int(payload.get("start_line") or 1),
            "line_count": int(payload.get("line_count") or 0),
        }
    if intent == "workspace.ensure_directory":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "action": str(payload.get("action") or "").strip(),
            "already_present": bool(payload.get("already_present", False)),
        }
    if intent == "workspace.write_file":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "line_count": int(payload.get("line_count") or 0),
            "action": str(payload.get("action") or "").strip(),
        }
    if intent == "workspace.replace_in_file":
        return {
            "intent": intent,
            "path": str(payload.get("path") or "").strip(),
            "replacements": int(payload.get("replacements") or 0),
        }
    if intent == "sandbox.run_command":
        stdout = str(payload.get("stdout") or "").strip()
        stderr = str(payload.get("stderr") or "").strip()
        combined = "\n".join(part for part in (stderr, stdout) if part).strip()
        file_match = _FILE_LINE_RE.search(combined)
        path = str(file_match.group("path") or "").strip() if file_match else ""
        line_number = int(file_match.group("line") or 0) if file_match else 0
        diagnostic_query = ""
        for line in [item.strip() for item in combined.splitlines() if item.strip()]:
            lowered = line.lower()
            if any(token in lowered for token in ("error", "failed", "exception", "traceback", "assert")):
                diagnostic_query = line[:160]
                break
        return {
            "intent": intent,
            "command": str(payload.get("command") or "").strip(),
            "cwd": str(payload.get("cwd") or "").strip(),
            "returncode": int(payload.get("returncode") or 0),
            "success": bool(payload.get("success", False)),
            "error_path": path,
            "error_line": line_number,
            "diagnostic_query": diagnostic_query,
        }
    if intent == "web.search":
        results = [dict(item) for item in list(payload.get("results") or []) if isinstance(item, dict)]
        primary = dict((results[:1] or [{}])[0] or {})
        return {
            "intent": intent,
            "result_count": int(payload.get("result_count") or len(results)),
            "primary_url": str(primary.get("url") or "").strip(),
            "primary_domain": str(primary.get("origin_domain") or primary.get("domain") or "").strip(),
            "domains": [
                str(item.get("origin_domain") or item.get("domain") or "").strip()
                for item in results
                if str(item.get("origin_domain") or item.get("domain") or "").strip()
            ],
        }
    if intent in {"web.fetch", "web.research"}:
        return {
            "intent": intent,
            "url": str(payload.get("url") or payload.get("final_url") or "").strip(),
            "query": str(payload.get("query") or "").strip(),
            "status": str(payload.get("status") or "").strip(),
            "hit_count": int(payload.get("hit_count") or 0),
            "evidence_strength": str(payload.get("evidence_strength") or "").strip(),
        }
    return {"intent": intent}


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
        if intent == "workspace.ensure_directory":
            return _ensure_directory(arguments, workspace_root=workspace_root)
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
            details={
                "error": str(exc),
                "observation": _tool_observation(
                    intent=intent,
                    tool_surface="runtime_execution",
                    ok=False,
                    status="error",
                    error=str(exc),
                ),
            },
        )
    return RuntimeExecutionResult(
        handled=True,
        ok=False,
        status="unsupported",
        response_text=f"I won't fake it: `{intent}` is not supported by the runtime execution layer.",
        details={
            "observation": _tool_observation(
                intent=intent,
                tool_surface="runtime_execution",
                ok=False,
                status="unsupported",
            ),
        },
    )


def _workspace_root(source_context: dict[str, Any] | None) -> Path:
    raw = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_workspace_path(raw_path: str | None, *, workspace_root: Path) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        return workspace_root
    candidate = Path(raw)
    candidate = (workspace_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
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
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.list_files",
                    tool_surface="workspace",
                    ok=True,
                    status="no_results",
                    path=_relative_path(target, workspace_root=workspace_root),
                    paths=[],
                    count=0,
                ),
            },
        )
    relative_rows = [_relative_path(path, workspace_root=workspace_root) for path in rows[:limit]]
    lines = [f"Workspace files under `{_relative_path(target, workspace_root=workspace_root)}`:"]
    for relative in relative_rows:
        lines.append(f"- {relative}")
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text="\n".join(lines),
        details={
            "path": _relative_path(target, workspace_root=workspace_root),
            "count": len(relative_rows),
            "paths": relative_rows,
            "observation": _tool_observation(
                intent="workspace.list_files",
                tool_surface="workspace",
                ok=True,
                status="executed",
                path=_relative_path(target, workspace_root=workspace_root),
                count=len(relative_rows),
                paths=relative_rows,
            ),
        },
    )


def _search_text(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    query = str(arguments.get("query") or "").strip()
    if not query:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="invalid_arguments",
            response_text="workspace.search_text needs a non-empty `query`.",
            details={
                "observation": _tool_observation(
                    intent="workspace.search_text",
                    tool_surface="workspace",
                    ok=False,
                    status="invalid_arguments",
                ),
            },
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    limit = max(1, min(int(arguments.get("limit") or 20), 100))
    glob_pattern = str(arguments.get("glob") or "**/*").strip() or "**/*"
    matches: list[str] = []
    match_rows: list[dict[str, Any]] = []
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
            snippet = line.strip()[:220]
            matches.append(f"- {relative}:{index} {snippet}")
            match_rows.append({"path": relative, "line": index, "snippet": snippet})
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
            details={
                "query": query,
                "matches": [],
                "observation": _tool_observation(
                    intent="workspace.search_text",
                    tool_surface="workspace",
                    ok=True,
                    status="no_results",
                    query=query,
                    matches=[],
                    match_count=0,
                ),
            },
        )
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=f'Search matches for "{query}":\n' + "\n".join(matches),
        details={
            "query": query,
            "match_count": len(match_rows),
            "matches": match_rows,
            "observation": _tool_observation(
                intent="workspace.search_text",
                tool_surface="workspace",
                ok=True,
                status="executed",
                query=query,
                match_count=len(match_rows),
                matches=match_rows,
            ),
        },
    )


def _read_file(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    if not target.exists() or not target.is_file():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` does not exist.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.read_file",
                    tool_surface="workspace",
                    ok=False,
                    status="not_found",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
        )
    if not _is_probably_text(target):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="binary_file",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` does not look like readable text.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.read_file",
                    tool_surface="workspace",
                    ok=False,
                    status="binary_file",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
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
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "start_line": start_line,
                "line_count": 0,
                "lines": [],
                "observation": _tool_observation(
                    intent="workspace.read_file",
                    tool_surface="workspace",
                    ok=True,
                    status="empty_slice",
                    path=_relative_path(target, workspace_root=workspace_root),
                    start_line=start_line,
                    line_count=0,
                    lines=[],
                ),
            },
        )
    numbered = [f"{start_line + offset}: {line}" for offset, line in enumerate(chunk)]
    line_rows = [
        {"line_number": start_line + offset, "text": line}
        for offset, line in enumerate(chunk)
    ]
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}`:\n" + "\n".join(numbered),
        details={
            "path": _relative_path(target, workspace_root=workspace_root),
            "start_line": start_line,
            "line_count": len(chunk),
            "lines": line_rows,
            "observation": _tool_observation(
                intent="workspace.read_file",
                tool_surface="workspace",
                ok=True,
                status="executed",
                path=_relative_path(target, workspace_root=workspace_root),
                start_line=start_line,
                line_count=len(chunk),
                lines=line_rows,
            ),
        },
    )


def _ensure_directory(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="workspace.ensure_directory",
                    tool_surface="workspace",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    relative_path = _relative_path(target, workspace_root=workspace_root)
    already_present = target.exists()
    target.mkdir(parents=True, exist_ok=True)
    status = "already_exists" if already_present else "executed"
    action = "confirmed" if already_present else "created"
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status=status,
        response_text=(
            f"Directory `{relative_path}` already existed."
            if already_present
            else f"Created directory `{relative_path}`."
        ),
        details={
            "path": relative_path,
            "action": action,
            "already_present": already_present,
            "observation": _tool_observation(
                intent="workspace.ensure_directory",
                tool_surface="workspace",
                ok=True,
                status=status,
                path=relative_path,
                action=action,
                already_present=already_present,
            ),
        },
    )


def _write_file(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="workspace.write_file",
                    tool_surface="workspace",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    content = str(arguments.get("content") or "")
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    previous = target.read_text(encoding="utf-8", errors="replace") if existed else ""
    target.write_text(content, encoding="utf-8")
    line_count = len(content.splitlines()) or (1 if content else 0)
    relative_path = _relative_path(target, workspace_root=workspace_root)
    diff_artifact = {
        "artifact_type": "file_diff",
        "path": relative_path,
        "action": "updated" if existed else "created",
        "line_count": line_count,
        "diff_preview": _diff_preview(before=previous, after=content, path=relative_path),
    }
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=(
            f"{'Updated' if existed else 'Created'} file `{relative_path}` "
            f"with {line_count} lines."
        ),
        details={
            "path": relative_path,
            "line_count": line_count,
            "action": "updated" if existed else "created",
            "artifacts": [diff_artifact],
            "observation": _tool_observation(
                intent="workspace.write_file",
                tool_surface="workspace",
                ok=True,
                status="executed",
                path=relative_path,
                line_count=line_count,
                action="updated" if existed else "created",
                diff_preview=str(diff_artifact.get("diff_preview") or ""),
            ),
        },
    )


def _replace_in_file(arguments: dict[str, Any], *, workspace_root: Path) -> RuntimeExecutionResult:
    if not policy_engine.get("filesystem.allow_write_workspace", False):
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="disabled",
            response_text="Workspace writes are disabled by policy.",
            details={
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="disabled",
                ),
            },
        )
    target = _resolve_workspace_path(arguments.get("path"), workspace_root=workspace_root)
    if not target.exists() or not target.is_file():
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="not_found",
            response_text=f"File `{_relative_path(target, workspace_root=workspace_root)}` does not exist.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="not_found",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
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
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="invalid_arguments",
                    path=_relative_path(target, workspace_root=workspace_root),
                ),
            },
        )
    content = target.read_text(encoding="utf-8", errors="replace")
    occurrences = content.count(old_text)
    if occurrences <= 0:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="no_match",
            response_text=f"`old_text` was not found in `{_relative_path(target, workspace_root=workspace_root)}`.",
            details={
                "path": _relative_path(target, workspace_root=workspace_root),
                "replacements": 0,
                "observation": _tool_observation(
                    intent="workspace.replace_in_file",
                    tool_surface="workspace",
                    ok=False,
                    status="no_match",
                    path=_relative_path(target, workspace_root=workspace_root),
                    replacements=0,
                ),
            },
        )
    if replace_all:
        updated = content.replace(old_text, new_text)
        replaced = occurrences
    else:
        updated = content.replace(old_text, new_text, 1)
        replaced = 1
    target.write_text(updated, encoding="utf-8")
    relative_path = _relative_path(target, workspace_root=workspace_root)
    diff_artifact = {
        "artifact_type": "file_diff",
        "path": relative_path,
        "action": "replaced",
        "replacements": replaced,
        "old_text_preview": _truncate(old_text, limit=180),
        "new_text_preview": _truncate(new_text, limit=180),
        "diff_preview": _diff_preview(before=content, after=updated, path=relative_path),
    }
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="executed",
        response_text=(
            f"Applied {replaced} replacement{'s' if replaced != 1 else ''} in "
            f"`{relative_path}`."
        ),
        details={
            "path": relative_path,
            "replacements": replaced,
            "artifacts": [diff_artifact],
            "observation": _tool_observation(
                intent="workspace.replace_in_file",
                tool_surface="workspace",
                ok=True,
                status="executed",
                path=relative_path,
                replacements=replaced,
                diff_preview=str(diff_artifact.get("diff_preview") or ""),
            ),
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
            details={
                "observation": _tool_observation(
                    intent="sandbox.run_command",
                    tool_surface="sandbox",
                    ok=False,
                    status="invalid_arguments",
                ),
            },
        )
    raw_cwd = arguments.get("cwd")
    cwd = _resolve_workspace_path(raw_cwd, workspace_root=workspace_root) if raw_cwd else workspace_root
    if cwd.is_file():
        cwd = cwd.parent
    runner = SandboxRunner(ExecutionGate(), str(workspace_root))
    result = runner.run_command(command, cwd=str(cwd))
    relative_cwd = _relative_path(cwd, workspace_root=workspace_root)
    status = str(result.get("status") or "")
    if status and status != "executed":
        stdout = _truncate(str(result.get("stdout") or ""), limit=2400)
        stderr = _truncate(str(result.get("stderr") or ""), limit=1600)
        command_artifact = {
            "artifact_type": "command_output",
            "command": command,
            "cwd": relative_cwd,
            "returncode": int(result.get("returncode", 0) or 0),
            "stdout": stdout,
            "stderr": stderr,
            "status": status,
        }
        failure_summary = _extract_failure_summary(
            command=command,
            stdout=stdout,
            stderr=stderr,
            returncode=int(result.get("returncode", 0) or 0),
        )
        failure_artifacts = []
        if failure_summary:
            failure_artifacts.append(
                {
                    "artifact_type": "failure",
                    "command": command,
                    "cwd": relative_cwd,
                    "returncode": int(result.get("returncode", 0) or 0),
                    "summary": failure_summary,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            )
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status=status,
            response_text=str(result.get("error") or f"Command could not run: {status}"),
            details={
                **dict(result),
                "artifacts": [command_artifact, *failure_artifacts],
                "observation": _tool_observation(
                    intent="sandbox.run_command",
                    tool_surface="sandbox",
                    ok=False,
                    status=status,
                    command=command,
                    cwd=relative_cwd,
                    returncode=int(result.get("returncode", 0) or 0),
                    stdout=stdout,
                    stderr=stderr,
                    error=str(result.get("error") or ""),
                    failure_summary=failure_summary,
                ),
            },
        )
    stdout = _truncate(str(result.get("stdout") or ""), limit=2400)
    stderr = _truncate(str(result.get("stderr") or ""), limit=1600)
    command_artifact = {
        "artifact_type": "command_output",
        "command": command,
        "cwd": relative_cwd,
        "returncode": int(result.get("returncode", 0) or 0),
        "stdout": stdout,
        "stderr": stderr,
        "status": "executed",
    }
    failure_summary = _extract_failure_summary(
        command=command,
        stdout=stdout,
        stderr=stderr,
        returncode=int(result.get("returncode", 0) or 0),
    )
    failure_artifacts = []
    if failure_summary:
        failure_artifacts.append(
            {
                "artifact_type": "failure",
                "command": command,
                "cwd": relative_cwd,
                "returncode": int(result.get("returncode", 0) or 0),
                "summary": failure_summary,
                "stdout": stdout,
                "stderr": stderr,
            }
        )
    lines = [
        f"Command executed in `{relative_cwd}`:",
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
            "cwd": relative_cwd,
            "returncode": int(result.get("returncode", 0) or 0),
            "success": bool(result.get("success", False)),
            "stdout": stdout,
            "stderr": stderr,
            "artifacts": [command_artifact, *failure_artifacts],
            "observation": _tool_observation(
                intent="sandbox.run_command",
                tool_surface="sandbox",
                ok=True,
                status="executed",
                command=command,
                cwd=relative_cwd,
                returncode=int(result.get("returncode", 0) or 0),
                success=bool(result.get("success", False)),
                stdout=stdout,
                stderr=stderr,
                failure_summary=failure_summary,
            ),
        },
    )
