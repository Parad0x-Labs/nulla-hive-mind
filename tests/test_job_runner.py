from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.job_runner import JobRunner
from sandbox.resource_limits import ExecutionPolicy


class JobRunnerTests(unittest.TestCase):
    def test_network_command_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(ExecutionPolicy(workspace_root=Path(tmpdir)))
            with self.assertRaises(ValueError):
                runner.run(["curl", "https://example.com"])

    def test_workspace_escape_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(ExecutionPolicy(workspace_root=Path(tmpdir)))
            escape_dir = "C:\\" if sys.platform == "win32" else "/"
            with self.assertRaises(ValueError):
                runner.run(["python3", "-c", "print('ok')"], cwd=escape_dir)

    @unittest.skipIf(sys.platform == "win32", "PosixPath cannot be instantiated on Windows")
    def test_os_enforced_network_isolation_requires_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="os_enforced",
                )
            )
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "darwin"), patch(
                "sandbox.job_runner.shutil.which", return_value=None
            ):
                with self.assertRaises(ValueError):
                    runner.run(["python3", "-c", "print('safe')"])

    def test_linux_unshare_prefix_applied_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            def _which(cmd: str) -> str | None:
                return "/usr/bin/unshare" if cmd == "unshare" else None
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "linux"), patch(
                "sandbox.job_runner.shutil.which", side_effect=_which
            ):
                argv = runner._with_network_isolation(["python3", "-c", "print('x')"])
                self.assertEqual(argv[:3], ["/usr/bin/unshare", "-n", "--"])

    def test_linux_bwrap_prefix_preferred_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            def _which(cmd: str) -> str | None:
                if cmd == "bwrap":
                    return "/usr/bin/bwrap"
                if cmd == "unshare":
                    return "/usr/bin/unshare"
                return None
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "linux"), patch(
                "sandbox.job_runner.shutil.which", side_effect=_which
            ):
                argv = runner._with_network_isolation(["python3", "-c", "print('x')"])
                self.assertEqual(argv[:3], ["/usr/bin/bwrap", "--unshare-net", "--"])


if __name__ == "__main__":
    unittest.main()
