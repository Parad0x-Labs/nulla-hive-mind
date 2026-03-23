from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp


@dataclass(frozen=True)
class ShardAssignment:
    index: int
    targets: tuple[str, ...]
    estimated_weight: int


def discover_test_targets(
    *,
    repo_root: Path,
    paths: Sequence[str] | None = None,
) -> tuple[str, ...]:
    raw_paths = [str(item).strip() for item in list(paths or []) if str(item).strip()]
    if not raw_paths:
        return tuple(_discover_from_dir(repo_root / "tests", repo_root=repo_root))

    discovered: list[str] = []
    seen: set[str] = set()
    for raw in raw_paths:
        resolved = (repo_root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if resolved.is_file():
            rel = resolved.relative_to(repo_root.resolve()).as_posix()
            if rel not in seen:
                seen.add(rel)
                discovered.append(rel)
            continue
        if resolved.is_dir():
            for item in _discover_from_dir(resolved, repo_root=repo_root):
                if item not in seen:
                    seen.add(item)
                    discovered.append(item)
            continue
        raise FileNotFoundError(f"pytest shard target does not exist: {raw}")
    return tuple(discovered)


def partition_targets(
    targets: Sequence[str],
    *,
    repo_root: Path,
    workers: int,
) -> tuple[ShardAssignment, ...]:
    normalized_workers = max(1, int(workers))
    ordered_targets = [str(item) for item in targets if str(item).strip()]
    if not ordered_targets:
        return tuple()
    shard_lists: list[list[str]] = [[] for _ in range(min(normalized_workers, len(ordered_targets)))]
    shard_weights = [0 for _ in shard_lists]
    weighted_targets = sorted(
        ordered_targets,
        key=lambda target: (_target_weight(repo_root / target), target),
        reverse=True,
    )
    for target in weighted_targets:
        idx = min(range(len(shard_lists)), key=lambda item: (shard_weights[item], item))
        shard_lists[idx].append(target)
        shard_weights[idx] += _target_weight(repo_root / target)
    return tuple(
        ShardAssignment(
            index=index + 1,
            targets=tuple(items),
            estimated_weight=shard_weights[index],
        )
        for index, items in enumerate(shard_lists)
        if items
    )


def build_shard_command(
    assignment: ShardAssignment,
    *,
    pytest_args: Sequence[str] = (),
) -> tuple[str, ...]:
    return ("pytest", "-q", *assignment.targets, *tuple(str(item) for item in pytest_args))


def run_shards(
    assignments: Sequence[ShardAssignment],
    *,
    repo_root: Path,
    pytest_args: Sequence[str] = (),
    shard_label: str = "full",
    dry_run: bool = False,
    launcher: Callable[..., subprocess.Popen[str]] | None = None,
) -> int:
    if not assignments:
        print("No shard assignments generated.", flush=True)
        return 0

    run_root = Path(mkdtemp(prefix=f"nulla-pytest-shards-{shard_label}-"))
    logs_dir = run_root / "logs"
    runtime_root = run_root / "runtime"
    logs_dir.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    print(f"Shard run root: {run_root}", flush=True)

    popen = launcher or subprocess.Popen
    processes: list[tuple[ShardAssignment, subprocess.Popen[str], Path]] = []
    try:
        for assignment in assignments:
            command = build_shard_command(assignment, pytest_args=pytest_args)
            runtime_home = runtime_root / f"shard-{assignment.index}"
            runtime_home.mkdir(parents=True, exist_ok=True)
            log_path = logs_dir / f"shard-{assignment.index}.log"
            rendered = " ".join(command)
            print(
                f"==> shard {assignment.index}/{len(assignments)} "
                f"({len(assignment.targets)} files, weight={assignment.estimated_weight})\n"
                f"$ {rendered}",
                flush=True,
            )
            if dry_run:
                continue
            handle = log_path.open("w", encoding="utf-8")
            env = os.environ.copy()
            env["NULLA_HOME"] = str(runtime_home)
            env["NULLA_TEST_SHARD"] = str(assignment.index)
            env.setdefault("PYTHONUNBUFFERED", "1")
            try:
                process = popen(
                    command,
                    cwd=str(repo_root),
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            finally:
                handle.close()
            processes.append((assignment, process, log_path))

        if dry_run:
            return 0

        failed = False
        for assignment, process, log_path in processes:
            rc = int(process.wait())
            if rc != 0:
                failed = True
                print(f"\n!! shard {assignment.index} failed (exit {rc})", flush=True)
                print(log_path.read_text(encoding="utf-8", errors="replace"), flush=True)
            else:
                print(f"ok shard {assignment.index}", flush=True)
        return 1 if failed else 0
    finally:
        for _, process, _ in processes:
            if process.poll() is None:
                with contextlib.suppress(Exception):
                    process.terminate()
        if dry_run:
            shutil.rmtree(run_root, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run NULLA pytest files in isolated parallel shards.")
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional file or directory targets. Defaults to the full tests tree.",
    )
    parser.add_argument("--workers", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="Extra argument forwarded to each shard pytest invocation.",
    )
    parser.add_argument("--label", default="full", help="Label for runtime-home isolation folders.")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    targets = discover_test_targets(repo_root=repo_root, paths=tuple(args.paths))
    assignments = partition_targets(targets, repo_root=repo_root, workers=max(1, int(args.workers)))
    return run_shards(
        assignments,
        repo_root=repo_root,
        pytest_args=tuple(str(item) for item in args.pytest_arg),
        shard_label=str(args.label or "full"),
        dry_run=bool(args.dry_run),
    )


def _discover_from_dir(path: Path, *, repo_root: Path) -> list[str]:
    if not path.exists():
        return []
    items = [
        entry.relative_to(repo_root.resolve()).as_posix()
        for entry in sorted(path.rglob("test_*.py"))
        if entry.is_file()
    ]
    return items


def _target_weight(path: Path) -> int:
    try:
        return max(1, int(path.stat().st_size))
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
