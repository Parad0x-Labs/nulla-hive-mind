from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.execution.validation_tools import runtime_validation_command, validation_command
from core.orchestration import build_task_envelope
from core.runtime_execution_tools import execute_runtime_tool
from core.tool_intent_executor import plan_tool_workflow


class RuntimeExecutionOperatorPhase1Tests(unittest.TestCase):
    def test_workspace_list_tree_and_symbol_search_are_grounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "src").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "app.py").write_text(
                "class Runner:\n    pass\n\n\ndef launch():\n    return Runner()\n",
                encoding="utf-8",
            )

            tree = execute_runtime_tool(
                "workspace.list_tree",
                {"path": ".", "limit": 20},
                source_context={"workspace": tmpdir},
            )
            assert tree is not None
            self.assertTrue(tree.ok)
            self.assertIn("src/", tree.response_text)
            self.assertIn("src/app.py", tree.response_text)
            self.assertEqual(tree.details["observation"]["intent"], "workspace.list_tree")

            symbol = execute_runtime_tool(
                "workspace.symbol_search",
                {"symbol": "launch", "path": "src"},
                source_context={"workspace": tmpdir},
            )
            assert symbol is not None
            self.assertTrue(symbol.ok)
            self.assertIn("[function_definition]", symbol.response_text)
            self.assertEqual(symbol.details["observation"]["matches"][0]["path"], "src/app.py")

    def test_workspace_apply_unified_diff_and_rollback_use_tracked_mutation_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1 +1 @@",
                    "-print('hello')",
                    "+print('goodbye')",
                    "",
                ]
            )

            applied = execute_runtime_tool(
                "workspace.apply_unified_diff",
                {"patch": patch_text},
                source_context={"workspace": tmpdir, "session_id": "session-1"},
            )
            assert applied is not None
            self.assertTrue(applied.ok)
            self.assertIn("Applied unified diff", applied.response_text)
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "print('goodbye')\n")

            rolled_back = execute_runtime_tool(
                "workspace.rollback_last_change",
                {},
                source_context={"workspace": tmpdir, "session_id": "session-1"},
            )
            assert rolled_back is not None
            self.assertTrue(rolled_back.ok)
            self.assertIn("restored `app.py`", rolled_back.response_text)
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "print('hello')\n")

    def test_workspace_apply_unified_diff_has_python_fallback_when_shell_patchers_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1 +1 @@",
                    "-print('hello')",
                    "+print('goodbye')",
                    "",
                ]
            )

            with mock.patch("core.execution.workspace_tools.shutil.which", return_value=None):
                applied = execute_runtime_tool(
                    "workspace.apply_unified_diff",
                    {"patch": patch_text},
                    source_context={"workspace": tmpdir, "session_id": "session-fallback"},
                )

            assert applied is not None
            self.assertTrue(applied.ok)
            self.assertEqual(applied.details["observation"]["engine"], "python_fallback")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "print('goodbye')\n")

    def test_workspace_git_status_and_diff_report_real_repo_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "tests@example.test"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=workspace, check=True, capture_output=True)
            (workspace / "tracked.py").write_text("print('hello')\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.py"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=workspace, check=True, capture_output=True)
            (workspace / "tracked.py").write_text("print('goodbye')\n", encoding="utf-8")

            status = execute_runtime_tool(
                "workspace.git_status",
                {},
                source_context={"workspace": tmpdir},
            )
            assert status is not None
            self.assertTrue(status.ok)
            self.assertIn("tracked.py", status.response_text)

            diff = execute_runtime_tool(
                "workspace.git_diff",
                {},
                source_context={"workspace": tmpdir},
            )
            assert diff is not None
            self.assertTrue(diff.ok)
            self.assertIn("-print('hello')", diff.response_text)
            self.assertIn("+print('goodbye')", diff.response_text)

    def test_workspace_run_tests_executes_validation_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "test_sample.py").write_text(
                "def test_truth():\n    assert 2 + 2 == 4\n",
                encoding="utf-8",
            )
            result = execute_runtime_tool(
                "workspace.run_tests",
                {"command": "python3 -m pytest -q test_sample.py"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.details["observation"]["intent"], "workspace.run_tests")
            self.assertEqual(result.details["observation"]["returncode"], 0)
            self.assertIn(sys.executable, result.details["observation"]["command"])

    def test_workspace_run_tests_bypasses_linux_isolation_backends_for_trusted_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "test_sample.py").write_text(
                "def test_truth():\n    assert 2 + 2 == 4\n",
                encoding="utf-8",
            )
            with mock.patch("sandbox.job_runner.os.name", "posix"), mock.patch(
                "sandbox.job_runner.sys.platform", "linux"
            ), mock.patch("sandbox.job_runner.shutil.which", return_value="/usr/bin/unshare"):
                result = execute_runtime_tool(
                    "workspace.run_tests",
                    {"command": "python3 -m pytest -q test_sample.py"},
                    source_context={"workspace": tmpdir},
                )
            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.details["observation"]["returncode"], 0)

    def test_validation_command_defaults_are_honest(self) -> None:
        with mock.patch("core.execution.validation_tools.shutil.which") as which:
            which.side_effect = lambda name: "/usr/bin/" + name if name in {"pytest", "ruff"} else None
            self.assertEqual(validation_command("workspace.run_tests", {}), "pytest -q")
            self.assertEqual(validation_command("workspace.run_lint", {}), "ruff check .")
            self.assertEqual(validation_command("workspace.run_formatter", {}), "ruff format --check .")
            self.assertEqual(validation_command("workspace.run_formatter", {"apply": True}), "ruff format .")

    def test_runtime_validation_command_uses_current_interpreter_for_pytest_and_ruff(self) -> None:
        self.assertEqual(
            runtime_validation_command("python3 -m pytest -q test_sample.py"),
            f"{sys.executable} -m pytest -q test_sample.py",
        )
        self.assertEqual(
            runtime_validation_command("ruff check ."),
            f"{sys.executable} -m ruff check .",
        )

    def test_runtime_tool_can_execute_coder_task_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1,2 +1,2 @@",
                    " def answer():",
                    "-    return 41",
                    "+    return 42",
                    "",
                ]
            )
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-runtime",
                goal="Patch the answer and validate it",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {"intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}},
                        {"intent": "workspace.run_tests", "arguments": {"command": "python3 -m pytest -q test_app.py"}},
                    ],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )

            result = execute_runtime_tool(
                "orchestration.execute_envelope",
                {"task_envelope": envelope.to_dict()},
                source_context={"workspace": tmpdir, "session_id": "session-envelope"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.details["observation"]["intent"], "orchestration.execute_envelope")
            self.assertEqual(result.details["observation"]["task_role"], "coder")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_runtime_tool_executes_queen_envelope_with_dependency_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1,2 +1,2 @@",
                    " def answer():",
                    "-    return 41",
                    "+    return 42",
                    "",
                ]
            )
            coder = build_task_envelope(
                role="coder",
                task_id="coder-child",
                parent_task_id="queen-parent",
                goal="Patch first",
                latency_budget="deep",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {"intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}},
                        {"intent": "workspace.run_tests", "arguments": {"command": "python3 -m pytest -q test_app.py"}},
                    ],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )
            verifier = build_task_envelope(
                role="verifier",
                task_id="verify-child",
                parent_task_id="queen-parent",
                goal="Verify second",
                latency_budget="low_latency",
                inputs={
                    "task_class": "file_inspection",
                    "depends_on": ["coder-child"],
                    "runtime_tools": [{"intent": "workspace.run_tests", "arguments": {"command": "python3 -m pytest -q test_app.py"}}],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )
            queen = build_task_envelope(
                role="queen",
                task_id="queen-parent",
                goal="Coordinate both steps",
                merge_strategy="highest_score",
                inputs={"subtasks": [coder.to_dict(), verifier.to_dict()]},
            )

            result = execute_runtime_tool(
                "orchestration.execute_envelope",
                {"task_envelope": queen.to_dict()},
                source_context={"workspace": tmpdir, "session_id": "session-queen"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.details["scheduled_children"], ["coder-child", "verify-child"])
            self.assertEqual(result.details["observation"]["task_role"], "queen")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_planned_orchestrated_operator_envelope_executes_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            decision = plan_tool_workflow(
                user_text="replace `return 41` with `return 42` in app.py, then run `python3 -m pytest -q test_app.py`",
                task_class="debugging",
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(decision.handled)
            self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")

            result = execute_runtime_tool(
                decision.next_payload["intent"],
                dict(decision.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-planned-envelope"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.details["observation"]["task_role"], "queen")
            self.assertEqual(result.details["scheduled_children"][0][:6], "coder-")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_planned_orchestrated_operator_envelope_can_locate_target_before_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            decision = plan_tool_workflow(
                user_text="replace `return 41` with `return 42`, then run `python3 -m pytest -q test_app.py`",
                task_class="debugging",
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(decision.handled)
            self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")
            subtasks = list(decision.next_payload["arguments"]["task_envelope"]["inputs"]["subtasks"])
            coder_steps = list(subtasks[0]["inputs"]["runtime_tools"])
            self.assertEqual(coder_steps[0]["intent"], "workspace.search_text")
            self.assertEqual(coder_steps[1]["arguments"]["path"]["$from_step"], "locate-replacement-target")

            result = execute_runtime_tool(
                decision.next_payload["intent"],
                dict(decision.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-planned-envelope-search"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")


if __name__ == "__main__":
    unittest.main()
