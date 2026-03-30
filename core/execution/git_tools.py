from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .artifacts import build_command_artifact, truncate_text


def git_status_workspace(arguments: dict[str, Any], *, workspace_root: Path) -> dict[str, Any]:
    target = _git_target(arguments, workspace_root=workspace_root)
    result = _run_git(["status", "--short", "--branch"], cwd=target)
    if result["status"] == "not_git_repo":
        return {
            "ok": False,
            "status": "not_git_repo",
            "response_text": f"`{target}` is not inside a git repository.",
            "details": {"cwd": str(target), "stdout": "", "stderr": ""},
        }
    stdout = truncate_text(result["stdout"], limit=2400)
    stderr = truncate_text(result["stderr"], limit=1600)
    rendered = stdout or "Git working tree is clean."
    return {
        "ok": True,
        "status": "executed",
        "response_text": f"Git status for `{target}`:\n{rendered}",
        "details": {
            "cwd": str(target),
            "stdout": stdout,
            "stderr": stderr,
            "returncode": int(result["returncode"]),
            "artifacts": [
                build_command_artifact(
                    command="git status --short --branch",
                    cwd=str(target),
                    returncode=int(result["returncode"]),
                    stdout=stdout,
                    stderr=stderr,
                    status="executed",
                    artifact_type="git_status",
                )
            ],
        },
    }


def git_diff_workspace(arguments: dict[str, Any], *, workspace_root: Path) -> dict[str, Any]:
    target = _git_target(arguments, workspace_root=workspace_root)
    command = ["diff"]
    if bool(arguments.get("cached", False)):
        command.append("--cached")
    path = str(arguments.get("path") or "").strip()
    if path:
        command.extend(["--", path])
    result = _run_git(command, cwd=target)
    if result["status"] == "not_git_repo":
        return {
            "ok": False,
            "status": "not_git_repo",
            "response_text": f"`{target}` is not inside a git repository.",
            "details": {"cwd": str(target), "stdout": "", "stderr": ""},
        }
    stdout = truncate_text(result["stdout"], limit=3200)
    stderr = truncate_text(result["stderr"], limit=1600)
    rendered = stdout or "No unstaged git diff is present."
    return {
        "ok": True,
        "status": "executed",
        "response_text": f"Git diff for `{target}`:\n{rendered}",
        "details": {
            "cwd": str(target),
            "stdout": stdout,
            "stderr": stderr,
            "returncode": int(result["returncode"]),
            "artifacts": [
                build_command_artifact(
                    command="git " + " ".join(command),
                    cwd=str(target),
                    returncode=int(result["returncode"]),
                    stdout=stdout,
                    stderr=stderr,
                    status="executed",
                    artifact_type="git_diff",
                )
            ],
        },
    }


def git_summary_workspace(arguments: dict[str, Any], *, workspace_root: Path) -> dict[str, Any]:
    target = _git_target(arguments, workspace_root=workspace_root)
    probe = _run_git(["rev-parse", "--show-toplevel"], cwd=target)
    if probe["status"] == "not_git_repo":
        return {
            "ok": False,
            "status": "not_git_repo",
            "response_text": f"`{target}` is not inside a git repository.",
            "details": {"cwd": str(target), "stdout": "", "stderr": ""},
        }

    branch = (_run_git(["branch", "--show-current"], cwd=target).get("stdout") or "").strip()
    commit = (_run_git(["rev-parse", "--short=12", "HEAD"], cwd=target).get("stdout") or "").strip()
    dirty = bool((_run_git(["status", "--short"], cwd=target).get("stdout") or "").strip())
    local_branches = _git_stdout_lines(_run_git(["for-each-ref", "refs/heads", "--format=%(refname:short)"], cwd=target))
    remote_branch_rows = _git_stdout_lines(
        _run_git(["for-each-ref", "refs/remotes", "--format=%(refname:short)\t%(symref)"], cwd=target)
    )
    remote_branches = _git_remote_branches(remote_branch_rows)
    (today_date, today_start, tomorrow_start), (yesterday_date, yesterday_start, today_cutoff), timezone_label = _local_commit_day_windows()
    today_commit_count = _git_rev_list_count(
        target,
        "--all",
        f"--since={today_start.isoformat()}",
        f"--before={tomorrow_start.isoformat()}",
    )
    yesterday_commit_count = _git_rev_list_count(
        target,
        "--all",
        f"--since={yesterday_start.isoformat()}",
        f"--before={today_cutoff.isoformat()}",
    )
    total_branch_count = len(local_branches) + len(remote_branches)
    summary_head = (
        f"Git summary: branch {branch or '(detached HEAD)'} @ {commit or 'unknown'}; "
        f"{total_branch_count} visible branches ({len(local_branches)} local, {len(remote_branches)} remote tracking); "
        f"{today_commit_count} commits on {today_date}; {yesterday_commit_count} commits on {yesterday_date}; "
        f"dirty {'yes' if dirty else 'no'}"
    )
    response_text = "\n".join(
        [
            summary_head,
            f"Repo: `{target}`",
            f"- current branch: {branch or '(detached HEAD)'}",
            f"- head commit: {commit or 'unknown'}",
            f"- dirty: {'yes' if dirty else 'no'}",
            f"- local branches: {len(local_branches)}",
            f"- remote tracking branches: {len(remote_branches)}",
            f"- total visible branches: {total_branch_count}",
            f"- commits on {today_date}: {today_commit_count}",
            f"- commits on {yesterday_date}: {yesterday_commit_count}",
            "- commit count scope: repo-wide unique commits across visible local and remote refs",
            f"- commit day boundary timezone: {timezone_label}",
        ]
    )
    return {
        "ok": True,
        "status": "executed",
        "response_text": response_text,
        "details": {
            "cwd": str(target),
            "branch": branch,
            "commit": commit,
            "dirty": dirty,
            "local_branch_count": len(local_branches),
            "remote_branch_count": len(remote_branches),
            "total_branch_count": total_branch_count,
            "local_branches": local_branches,
            "remote_branches": remote_branches,
            "today_date": today_date,
            "today_commit_count": today_commit_count,
            "yesterday_date": yesterday_date,
            "yesterday_commit_count": yesterday_commit_count,
            "commit_count_scope": "all_visible_refs_unique",
            "timezone_label": timezone_label,
            "artifacts": [
                build_command_artifact(
                    command="git summary",
                    cwd=str(target),
                    returncode=0,
                    stdout=response_text,
                    stderr="",
                    status="executed",
                    artifact_type="git_summary",
                )
            ],
        },
    }


def _git_target(arguments: dict[str, Any], *, workspace_root: Path) -> Path:
    raw = str(arguments.get("cwd") or "").strip()
    if not raw:
        return workspace_root
    candidate = Path(raw)
    return (workspace_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()


def _run_git(argv: list[str], *, cwd: Path) -> dict[str, Any]:
    probe = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return {"status": "not_git_repo", "returncode": probe.returncode, "stdout": "", "stderr": probe.stderr}
    result = subprocess.run(["git", "-C", str(cwd), *argv], capture_output=True, text=True)
    return {
        "status": "executed",
        "returncode": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
    }


def _git_stdout_lines(result: dict[str, Any]) -> list[str]:
    return [str(line).strip() for line in str(result.get("stdout") or "").splitlines() if str(line).strip()]


def _git_remote_branches(rows: list[str]) -> list[str]:
    branches: list[str] = []
    for row in rows:
        name, _, symref = str(row or "").partition("\t")
        clean_name = name.strip()
        if not clean_name or symref.strip():
            continue
        branches.append(clean_name)
    return branches


def _local_commit_day_windows(
    now: datetime | None = None,
) -> tuple[tuple[str, datetime, datetime], tuple[str, datetime, datetime], str]:
    current = (now or datetime.now().astimezone()).astimezone()
    today_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    yesterday_start = today_start - timedelta(days=1)
    timezone_label = str(current.tzname() or current.tzinfo or "local time")
    return (
        (today_start.date().isoformat(), today_start, tomorrow_start),
        (yesterday_start.date().isoformat(), yesterday_start, today_start),
        timezone_label,
    )


def _git_rev_list_count(cwd: Path, *args: str) -> int:
    result = _run_git(["rev-list", "--count", *args], cwd=cwd)
    try:
        return max(0, int(str(result.get("stdout") or "0").strip() or "0"))
    except Exception:
        return 0
