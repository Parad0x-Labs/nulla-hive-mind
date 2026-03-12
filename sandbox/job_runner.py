from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from sandbox.container_adapter import ExecutionResult
from sandbox.network_guard import command_uses_network
from sandbox.resource_limits import ExecutionPolicy, normalize_policy, path_within_roots


def _truncate(text: str, limit_kb: int) -> str:
    raw = text or ""
    encoded = raw.encode("utf-8")
    if len(encoded) <= limit_kb * 1024:
        return raw
    return encoded[-(limit_kb * 1024) :].decode("utf-8", errors="replace")


class JobRunner:
    def __init__(self, policy: ExecutionPolicy):
        self.policy = normalize_policy(policy)

    def run(self, argv: list[str], *, cwd: str | Path | None = None) -> ExecutionResult:
        if not argv:
            raise ValueError("No command provided.")
        if cwd is None:
            cwd = self.policy.workspace_root
        cwd_path = Path(cwd).resolve()
        allowed_roots = (self.policy.workspace_root,) + tuple(self.policy.writable_roots)
        if not path_within_roots(cwd_path, allowed_roots):
            raise ValueError("Execution cwd escapes allowed workspace roots.")
        if command_uses_network(argv) and not self.policy.allow_network_egress:
            raise ValueError("Network egress is disabled by execution policy.")
        argv = self._with_network_isolation(argv)

        env = os.environ.copy()
        env["NULLA_EXECUTION_BACKEND"] = self.policy.backend
        env["NO_PROXY"] = "*"
        env["no_proxy"] = "*"

        completed = subprocess.run(
            argv,
            cwd=str(cwd_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=self.policy.max_seconds,
            shell=False,
            env=env,
        )
        return ExecutionResult(
            returncode=int(completed.returncode),
            stdout=_truncate(completed.stdout, self.policy.max_output_kb),
            stderr=_truncate(completed.stderr, self.policy.max_output_kb),
        )

    def _with_network_isolation(self, argv: list[str]) -> list[str]:
        if self.policy.allow_network_egress:
            return argv
        mode = (self.policy.network_isolation_mode or "auto").strip().lower()
        if mode not in {"auto", "os_enforced", "heuristic_only"}:
            mode = "auto"
        isolated = self._kernel_network_isolation_prefix(argv)
        if isolated is not None:
            return isolated
        if mode == "os_enforced":
            raise ValueError(
                "OS-level network isolation is required but unavailable (expected one of: bwrap, unshare, firejail)."
            )
        return argv

    def _kernel_network_isolation_prefix(self, argv: list[str]) -> list[str] | None:
        # Prefer hardened Linux isolation backends when present.
        isolated = self._linux_bwrap_prefix(argv)
        if isolated is not None:
            return isolated
        isolated = self._linux_unshare_prefix(argv)
        if isolated is not None:
            return isolated
        return self._linux_firejail_prefix(argv)

    def _linux_bwrap_prefix(self, argv: list[str]) -> list[str] | None:
        if os.name != "posix":
            return None
        if not sys.platform.startswith("linux"):
            return None
        bwrap = shutil.which("bwrap")
        if not bwrap:
            return None
        return [bwrap, "--unshare-net", "--", *list(argv)]

    def _linux_unshare_prefix(self, argv: list[str]) -> list[str] | None:
        if os.name != "posix":
            return None
        if not sys.platform.startswith("linux"):
            return None
        unshare = shutil.which("unshare")
        if not unshare:
            return None
        return [unshare, "-n", "--"] + list(argv)

    def _linux_firejail_prefix(self, argv: list[str]) -> list[str] | None:
        if os.name != "posix":
            return None
        if not sys.platform.startswith("linux"):
            return None
        firejail = shutil.which("firejail")
        if not firejail:
            return None
        return [firejail, "--net=none", "--quiet", "--"] + list(argv)
