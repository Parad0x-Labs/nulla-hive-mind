from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.runtime_execution_tools import execute_runtime_tool


class RuntimeExecutionToolsTests(unittest.TestCase):
    def test_workspace_list_files_and_read_file_are_grounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
            (workspace / ".hidden.txt").write_text("secret\n", encoding="utf-8")

            listed = execute_runtime_tool(
                "workspace.list_files",
                {"path": ".", "limit": 20},
                source_context={"workspace": tmpdir},
            )
            assert listed is not None
            self.assertTrue(listed.handled)
            self.assertTrue(listed.ok)
            self.assertIn("notes.txt", listed.response_text)
            self.assertNotIn(".hidden.txt", listed.response_text)

            read = execute_runtime_tool(
                "workspace.read_file",
                {"path": "notes.txt", "start_line": 2, "max_lines": 1},
                source_context={"workspace": tmpdir},
            )
            assert read is not None
            self.assertTrue(read.ok)
            self.assertIn("2: beta", read.response_text)

    def test_workspace_write_replace_and_search_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            written = execute_runtime_tool(
                "workspace.write_file",
                {"path": "docs/plan.md", "content": "status: draft\nnext: loop\n"},
                source_context={"workspace": tmpdir},
            )
            assert written is not None
            self.assertTrue(written.ok)
            self.assertIn("Created file `docs/plan.md`", written.response_text)

            replaced = execute_runtime_tool(
                "workspace.replace_in_file",
                {"path": "docs/plan.md", "old_text": "draft", "new_text": "done"},
                source_context={"workspace": tmpdir},
            )
            assert replaced is not None
            self.assertTrue(replaced.ok)
            self.assertIn("Applied 1 replacement", replaced.response_text)

            searched = execute_runtime_tool(
                "workspace.search_text",
                {"query": "status: done", "path": "docs"},
                source_context={"workspace": tmpdir},
            )
            assert searched is not None
            self.assertTrue(searched.ok)
            self.assertIn("docs/plan.md:1", searched.response_text)

    def test_sandbox_run_command_executes_local_bounded_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_runtime_tool(
                "sandbox.run_command",
                {"command": "pwd"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.handled)
            self.assertTrue(result.ok)
            self.assertIn("Command executed in `.`", result.response_text)
            self.assertIn(tmpdir, result.response_text)

    def test_sandbox_run_command_blocks_network_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "core.execution_gate.load_preferences",
            return_value=SimpleNamespace(autonomy_mode="hands_off"),
        ):
            result = execute_runtime_tool(
                "sandbox.run_command",
                {"command": "git pull"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.handled)
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "blocked_by_policy")
            self.assertIn("Network egress is disabled", result.response_text)


if __name__ == "__main__":
    unittest.main()
