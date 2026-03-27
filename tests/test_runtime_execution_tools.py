from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from core.runtime_execution_tools import (
    _trusted_local_network_mode,
    execute_runtime_tool,
    extract_observation_followup_hints,
)

_UNSHARE_AVAILABLE = os.system("unshare -r true >/dev/null 2>&1") == 0


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
            self.assertEqual(listed.details["observation"]["tool_surface"], "workspace")
            self.assertIn("notes.txt", listed.details["observation"]["paths"])

            read = execute_runtime_tool(
                "workspace.read_file",
                {"path": "notes.txt", "start_line": 2, "max_lines": 1},
                source_context={"workspace": tmpdir},
            )
            assert read is not None
            self.assertTrue(read.ok)
            self.assertIn("2: beta", read.response_text)
            self.assertEqual(read.details["observation"]["intent"], "workspace.read_file")
            self.assertEqual(read.details["observation"]["lines"][0]["line_number"], 2)
            self.assertEqual(read.details["observation"]["lines"][0]["text"], "beta")

    def test_workspace_read_file_verbatim_returns_exact_content_and_hints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")

            read = execute_runtime_tool(
                "workspace.read_file",
                {"path": "notes.txt", "start_line": 1, "max_lines": 10, "verbatim": True},
                source_context={"workspace": tmpdir},
            )
            assert read is not None
            self.assertTrue(read.ok)
            self.assertEqual(read.response_text, "alpha\nbeta")

            hints = extract_observation_followup_hints(read.details["observation"])
            self.assertTrue(hints["verbatim"])
            self.assertEqual(hints["content"], "alpha\nbeta")
            self.assertEqual(hints["lines"][1]["text"], "beta")

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
            self.assertEqual(written.details["observation"]["action"], "created")

            replaced = execute_runtime_tool(
                "workspace.replace_in_file",
                {"path": "docs/plan.md", "old_text": "draft", "new_text": "done"},
                source_context={"workspace": tmpdir},
            )
            assert replaced is not None
            self.assertTrue(replaced.ok)
            self.assertIn("Applied 1 replacement", replaced.response_text)
            self.assertEqual(replaced.details["observation"]["replacements"], 1)

            searched = execute_runtime_tool(
                "workspace.search_text",
                {"query": "status: done", "path": "docs"},
                source_context={"workspace": tmpdir},
            )
            assert searched is not None
            self.assertTrue(searched.ok)
            self.assertIn("docs/plan.md:1", searched.response_text)
            self.assertEqual(searched.details["observation"]["matches"][0]["path"], "docs/plan.md")
            self.assertEqual(searched.details["observation"]["matches"][0]["line"], 1)

    def test_machine_list_directory_lists_safe_desktop_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)):
            home = Path(tmpdir)
            desktop = home / "Desktop"
            desktop.mkdir(parents=True, exist_ok=True)
            (desktop / "Projects").mkdir()
            (desktop / "Ideas").mkdir()
            (desktop / "todo.txt").write_text("one\n", encoding="utf-8")
            (desktop / ".hidden").mkdir()

            listed = execute_runtime_tool(
                "machine.list_directory",
                {"path": "~/Desktop", "directories_only": True, "limit": 20},
                source_context={"workspace": tmpdir},
            )
            assert listed is not None
            self.assertTrue(listed.ok)
            self.assertIn("Projects/", listed.response_text)
            self.assertIn("Ideas/", listed.response_text)
            self.assertNotIn("todo.txt", listed.response_text)
            self.assertNotIn(".hidden", listed.response_text)

            hints = extract_observation_followup_hints(listed.details["observation"])
            self.assertEqual(hints["path"], "~/Desktop")
            self.assertTrue(hints["directories_only"])
            self.assertEqual(hints["entry_count"], 2)

    def test_machine_list_directory_blocks_paths_outside_safe_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)):
            result = execute_runtime_tool(
                "machine.list_directory",
                {"path": "~/Secrets"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "not_allowed")
            self.assertIn("safe local directories", result.response_text.lower())

    def test_machine_inspect_specs_returns_grounded_machine_summary(self) -> None:
        with mock.patch(
            "core.runtime_execution_tools.probe_machine",
            return_value=SimpleNamespace(
                cpu_cores=10,
                ram_gb=24.0,
                gpu_name="Apple Silicon",
                vram_gb=24.0,
                accelerator="mps",
            ),
        ), mock.patch(
            "core.runtime_execution_tools.select_qwen_tier",
            return_value=SimpleNamespace(tier_name="mid", ollama_tag="qwen2.5:14b"),
        ), mock.patch(
            "core.runtime_execution_tools._machine_os_details",
            return_value=("macOS", "15.4"),
        ), mock.patch(
            "core.runtime_execution_tools._machine_chip_name",
            return_value="Apple M4",
        ):
            result = execute_runtime_tool(
                "machine.inspect_specs",
                {},
                source_context={},
            )
            assert result is not None
            self.assertTrue(result.ok)
            self.assertIn("Apple M4", result.response_text)
            self.assertIn("24.0 GiB", result.response_text)
            hints = extract_observation_followup_hints(result.details["observation"])
            self.assertEqual(hints["chip_name"], "Apple M4")
            self.assertEqual(hints["recommended_model"], "qwen2.5:14b")

    def test_workspace_ensure_directory_creates_requested_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            created = execute_runtime_tool(
                "workspace.ensure_directory",
                {"path": "tools/helpers"},
                source_context={"workspace": tmpdir},
            )
            assert created is not None
            self.assertTrue(created.ok)
            self.assertIn("Created directory `tools/helpers`", created.response_text)
            self.assertEqual(created.details["observation"]["intent"], "workspace.ensure_directory")
            self.assertFalse(created.details["observation"]["already_present"])
            self.assertTrue((Path(tmpdir) / "tools" / "helpers").is_dir())

    def test_workspace_bootstrap_honors_nonexistent_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "fresh-project"

            created = execute_runtime_tool(
                "workspace.ensure_directory",
                {"path": "src/api"},
                source_context={"workspace": str(project_root)},
            )
            assert created is not None
            self.assertTrue(created.ok)
            self.assertTrue((project_root / "src" / "api").is_dir())

            written = execute_runtime_tool(
                "workspace.write_file",
                {"path": "src/api/main.py", "content": "print('ok')\n"},
                source_context={"workspace": str(project_root)},
            )
            assert written is not None
            self.assertTrue(written.ok)
            self.assertTrue((project_root / "src" / "api" / "main.py").is_file())
            self.assertIn("Created file `src/api/main.py`", written.response_text)

    def test_workspace_write_followup_hints_preserve_path_and_line_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            written = execute_runtime_tool(
                "workspace.write_file",
                {"path": "generated/app.py", "content": "print('ok')\n"},
                source_context={"workspace": tmpdir},
            )
            assert written is not None
            hints = extract_observation_followup_hints(written.details["observation"])

            self.assertEqual(hints["intent"], "workspace.write_file")
            self.assertEqual(hints["path"], "generated/app.py")
            self.assertEqual(hints["action"], "created")
            self.assertEqual(hints["line_count"], 1)

    def test_workspace_write_and_replace_store_diff_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            created = execute_runtime_tool(
                "workspace.write_file",
                {"path": "app.py", "content": "print('hello')\n"},
                source_context={"workspace": tmpdir},
            )
            assert created is not None
            created_artifact = created.details["artifacts"][0]
            self.assertEqual(created_artifact["artifact_type"], "file_diff")
            self.assertEqual(created_artifact["path"], "app.py")
            self.assertIn("+++ b/app.py", created_artifact["diff_preview"])

            replaced = execute_runtime_tool(
                "workspace.replace_in_file",
                {"path": "app.py", "old_text": "hello", "new_text": "goodbye"},
                source_context={"workspace": tmpdir},
            )
            assert replaced is not None
            replaced_artifact = replaced.details["artifacts"][0]
            self.assertEqual(replaced_artifact["artifact_type"], "file_diff")
            self.assertEqual(replaced_artifact["action"], "replaced")
            self.assertIn("-print('hello')", replaced_artifact["diff_preview"])
            self.assertIn("+print('goodbye')", replaced_artifact["diff_preview"])

    @pytest.mark.skipif(not _UNSHARE_AVAILABLE, reason="unshare not available (CI / non-Linux)")
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
            self.assertEqual(result.details["observation"]["tool_surface"], "sandbox")
            self.assertEqual(result.details["observation"]["command"], "pwd")
            self.assertEqual(result.details["observation"]["cwd"], ".")
            self.assertEqual(result.details["artifacts"][0]["artifact_type"], "command_output")

    @pytest.mark.skipif(not _UNSHARE_AVAILABLE, reason="unshare not available (CI / non-Linux)")
    def test_sandbox_run_command_preserves_failure_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "fail.py").write_text(
                "import sys\nprint('FAILED test_example', file=sys.stderr)\nsys.exit(1)\n",
                encoding="utf-8",
            )
            result = execute_runtime_tool(
                "sandbox.run_command",
                {"command": "python3 fail.py"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.handled)
            artifacts = list(result.details["artifacts"])
            self.assertEqual(artifacts[0]["artifact_type"], "command_output")
            self.assertEqual(artifacts[1]["artifact_type"], "failure")
            self.assertIn("FAILED test_example", artifacts[1]["summary"])
            self.assertIn("FAILED test_example", result.details["observation"]["failure_summary"])

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
            self.assertEqual(result.details["observation"]["tool_surface"], "sandbox")
            self.assertEqual(result.details["observation"]["status"], "blocked_by_policy")

    def test_trusted_local_network_mode_only_applies_to_internal_compileall_verification(self) -> None:
        self.assertEqual(
            _trusted_local_network_mode(
                "python3 -m compileall -q generated/telegram-bot/src",
                arguments={"_trusted_local_only": True},
            ),
            "heuristic_only",
        )
        self.assertIsNone(
            _trusted_local_network_mode(
                "python3 app.py",
                arguments={"_trusted_local_only": True},
            )
        )
        self.assertIsNone(
            _trusted_local_network_mode(
                "python3 -m compileall -q generated/telegram-bot/src",
                arguments={},
            )
        )


if __name__ == "__main__":
    unittest.main()
