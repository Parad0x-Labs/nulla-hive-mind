from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.orchestration import EnvelopeExecutionResult, build_task_envelope, execute_task_envelope


class OrchestrationExecutionPhase1Tests(unittest.TestCase):
    def test_coder_envelope_executes_patch_and_validation_with_required_receipts(self) -> None:
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
                task_id="coder-1",
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

            result = execute_task_envelope(envelope, workspace_root=tmpdir)

            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")
            receipt_types = {item["receipt_type"] for item in result.receipts}
            self.assertIn("tool_receipt", receipt_types)
            self.assertIn("validation_result", receipt_types)
            self.assertEqual(len(result.details["step_results"]), 2)

    def test_coder_envelope_fails_closed_when_required_validation_receipt_is_missing(self) -> None:
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
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-2",
                goal="Patch without validation",
                inputs={"runtime_tools": [{"intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}}]},
                required_receipts=("tool_receipt", "validation_result"),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir)

            self.assertFalse(result.ok)
            self.assertEqual(result.status, "missing_required_receipts")
            self.assertIn("validation_result", result.details["missing_receipts"])

    def test_verifier_envelope_rejects_mutating_workspace_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1 +1 @@",
                    "-print('hello')",
                    "+print('tampered')",
                    "",
                ]
            )
            envelope = build_task_envelope(
                role="verifier",
                task_id="verify-1",
                goal="Try to mutate the workspace",
                inputs={"runtime_tools": [{"intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}}]},
                required_receipts=("tool_receipt",),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir)

            self.assertFalse(result.ok)
            self.assertEqual(result.status, "permission_denied")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "print('hello')\n")

    def test_coder_envelope_fails_closed_when_attached_provider_lane_is_remote_only(self) -> None:
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
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-remote-lane",
                goal="Patch through remote-only lane",
                model_constraints={
                    "provider_capability_truth": [
                        {
                            "provider_id": "kimi:k2",
                            "role_fit": "queen",
                            "locality": "remote",
                            "tool_support": ["structured_json", "code_complex"],
                            "structured_output_support": True,
                            "queue_depth": 0,
                            "max_safe_concurrency": 4,
                        }
                    ]
                },
                inputs={"runtime_tools": [{"intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}}]},
                required_receipts=("tool_receipt",),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir)

            self.assertFalse(result.ok)
            self.assertEqual(result.status, "capacity_blocked")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "print('hello')\n")
            self.assertEqual(result.details["capacity_state"]["availability_state"], "blocked")

    def test_queen_envelope_executes_children_and_merges_results_deterministically(self) -> None:
        coder = build_task_envelope(
            role="coder",
            task_id="coder-child",
            parent_task_id="queen-parent",
            goal="Patch the code",
            latency_budget="deep",
        )
        verifier = build_task_envelope(
            role="verifier",
            task_id="verify-child",
            parent_task_id="queen-parent",
            goal="Validate the patch",
            latency_budget="low_latency",
        )
        queen = build_task_envelope(
            role="queen",
            task_id="queen-parent",
            goal="Coordinate patch and verification",
            merge_strategy="highest_score",
            inputs={"subtasks": [coder.to_dict(), verifier.to_dict()]},
        )

        def _child_executor(child: object) -> EnvelopeExecutionResult:
            envelope = child if hasattr(child, "role") else verifier
            if envelope.role == "verifier":
                return EnvelopeExecutionResult(
                    envelope=envelope,
                    ok=True,
                    status="completed",
                    output_text="Validated patch and tests passed.",
                    receipts=({"receipt_type": "validation_result", "ok": True},),
                    details={"score": 0.95},
                )
            return EnvelopeExecutionResult(
                envelope=envelope,
                ok=True,
                status="completed",
                output_text="Applied patch.",
                receipts=({"receipt_type": "tool_receipt", "ok": True},),
                details={"score": 0.55},
            )

        result = execute_task_envelope(queen, child_executor=_child_executor)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output_text, "Validated patch and tests passed.")
        self.assertEqual(result.details["scheduled_children"][0], "verify-child")
        self.assertEqual(result.details["merged_result"]["winner"]["task_id"], "verify-child")
        graph_rows = {item["task_id"]: item for item in result.details["graph"]}
        self.assertEqual(graph_rows["queen-parent"]["status"], "completed")
        self.assertEqual(graph_rows["verify-child"]["status"], "completed")
        self.assertEqual(graph_rows["coder-child"]["status"], "completed")

    def test_queen_envelope_respects_child_dependencies_for_real_runtime_steps(self) -> None:
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
                goal="Patch the code first",
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
                goal="Verify after the patch lands",
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
                goal="Coordinate patch and verification",
                merge_strategy="highest_score",
                inputs={"subtasks": [coder.to_dict(), verifier.to_dict()]},
            )

            result = execute_task_envelope(queen, workspace_root=tmpdir)

            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.details["scheduled_children"], ["coder-child", "verify-child"])
            graph_rows = {item["task_id"]: item for item in result.details["graph"]}
            self.assertEqual(graph_rows["coder-child"]["status"], "completed")
            self.assertEqual(graph_rows["verify-child"]["status"], "completed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_coder_envelope_can_resolve_search_reference_into_read_replace_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )
            path_ref = {
                "$from_step": "locate-target",
                "$path": "observation.primary_path",
                "$require_single_match": True,
            }
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-search-replace",
                goal="Find the target file, patch it, and validate the change",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {
                            "step_id": "locate-target",
                            "intent": "workspace.search_text",
                            "arguments": {"query": "return 41", "limit": 2},
                        },
                        {
                            "step_id": "inspect-target",
                            "intent": "workspace.read_file",
                            "arguments": {"path": dict(path_ref), "start_line": 1, "max_lines": 40},
                        },
                        {
                            "step_id": "apply-target",
                            "intent": "workspace.replace_in_file",
                            "arguments": {
                                "path": dict(path_ref),
                                "old_text": "return 41",
                                "new_text": "return 42",
                                "replace_all": True,
                            },
                        },
                        {
                            "step_id": "validate-target",
                            "intent": "workspace.run_tests",
                            "arguments": {"command": "python3 -m pytest -q test_app.py"},
                        },
                    ],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir)

            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")
            self.assertEqual(result.details["step_results"][0]["step_id"], "locate-target")
            self.assertEqual(result.details["step_results"][2]["arguments"]["path"], "app.py")

    def test_coder_envelope_fails_closed_when_search_reference_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "backup.py").write_text("def answer_backup():\n    return 41\n", encoding="utf-8")
            path_ref = {
                "$from_step": "locate-target",
                "$path": "observation.primary_path",
                "$require_single_match": True,
            }
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-ambiguous-search",
                goal="Find the target file and patch it",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {
                            "step_id": "locate-target",
                            "intent": "workspace.search_text",
                            "arguments": {"query": "return 41", "limit": 2},
                        },
                        {
                            "step_id": "apply-target",
                            "intent": "workspace.replace_in_file",
                            "arguments": {
                                "path": dict(path_ref),
                                "old_text": "return 41",
                                "new_text": "return 42",
                                "replace_all": True,
                            },
                        },
                    ],
                },
                required_receipts=("tool_receipt",),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir)

            self.assertFalse(result.ok)
            self.assertEqual(result.status, "unresolved_step_reference")
            self.assertIn("returned 2 matches", result.output_text)
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 41\n")
            self.assertEqual((workspace / "backup.py").read_text(encoding="utf-8"), "def answer_backup():\n    return 41\n")


if __name__ == "__main__":
    unittest.main()
