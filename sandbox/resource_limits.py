from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ExecutionPolicy:
    workspace_root: Path
    writable_roots: tuple[Path, ...] = field(default_factory=tuple)
    max_seconds: int = 120
    max_output_kb: int = 256
    max_memory_mb: int = 512
    allow_network_egress: bool = False
    network_isolation_mode: str = "auto"  # auto | os_enforced | heuristic_only
    backend: str = "subprocess"


def normalize_policy(policy: ExecutionPolicy) -> ExecutionPolicy:
    writable_roots = tuple(Path(path).resolve() for path in (policy.writable_roots or (policy.workspace_root,)))
    return ExecutionPolicy(
        workspace_root=policy.workspace_root.resolve(),
        writable_roots=writable_roots,
        max_seconds=int(policy.max_seconds),
        max_output_kb=int(policy.max_output_kb),
        max_memory_mb=int(policy.max_memory_mb),
        allow_network_egress=bool(policy.allow_network_egress),
        network_isolation_mode=str(policy.network_isolation_mode or "auto"),
        backend=policy.backend,
    )


def path_within_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in roots)
