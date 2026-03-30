from __future__ import annotations

from core.runtime_execution_history import build_runtime_execution_history, summarize_runtime_surface


def test_execution_history_surfaces_pending_approval_and_touched_paths() -> None:
    history = build_runtime_execution_history(
        session={
            "session_id": "openclaw:approval",
            "request_preview": "Clean temp files under alpha",
            "task_class": "operator_action",
            "status": "pending_approval",
            "last_message": "Awaiting approval.",
            "updated_at": "2026-03-30T12:00:00+00:00",
            "resume_available": False,
        },
        checkpoint={
            "checkpoint_id": "runtime-approval",
            "status": "pending_approval",
            "step_count": 1,
            "resume_count": 0,
            "last_tool_name": "operator.cleanup_temp_files",
            "pending_intent": {"intent": "operator.cleanup_temp_files"},
        },
        events=[
            {
                "event_type": "task_received",
                "message": "Received request.",
                "request_preview": "Clean temp files under alpha",
            },
            {
                "event_type": "tool_preview",
                "message": "Approval required for operator.cleanup_temp_files.",
                "tool_name": "operator.cleanup_temp_files",
                "status": "pending_approval",
                "target_path": "alpha/tmp",
            },
        ],
        receipts=[
            {
                "tool_name": "workspace.write_file",
                "arguments": {"target_path": "alpha/plan.txt"},
                "execution": {},
            }
        ],
    )

    assert history["bounded_execution"]["approval_state"] == "pending"
    assert history["bounded_execution"]["checkpoint_status"] == "pending_approval"
    assert history["latest_tool"] == "operator.cleanup_temp_files"
    assert "alpha/tmp" in history["changed_paths"]
    assert "alpha/plan.txt" in history["touched_paths"]
    assert history["timeline"][1]["value"] == "pending"


def test_execution_history_surfaces_verifier_failure_and_completed_rollback() -> None:
    history = build_runtime_execution_history(
        session={
            "session_id": "openclaw:repair",
            "request_preview": "Patch the failing function",
            "task_class": "debugging",
            "status": "failed",
            "last_message": "Verifier stayed red after patch.",
            "updated_at": "2026-03-30T12:05:00+00:00",
            "resume_available": False,
        },
        checkpoint={
            "checkpoint_id": "runtime-repair",
            "status": "failed",
            "step_count": 3,
            "resume_count": 1,
            "last_tool_name": "workspace.run_tests",
            "pending_intent": {},
        },
        events=[
            {
                "event_type": "task_envelope_started",
                "message": "coder envelope `coder-1` started.",
                "task_role": "coder",
            },
            {
                "event_type": "task_envelope_step_completed",
                "message": "coder envelope `coder-1` ran `workspace.apply_unified_diff`.",
                "task_role": "coder",
                "intent": "workspace.apply_unified_diff",
                "status": "executed",
                "target_path": "app.py",
            },
            {
                "event_type": "task_envelope_step_failed",
                "message": "verifier envelope `verify-1` failed while running `workspace.run_tests`.",
                "task_role": "verifier",
                "intent": "workspace.run_tests",
                "status": "failed",
            },
            {
                "event_type": "task_envelope_rollback_completed",
                "message": "coder envelope `coder-1` rolled back the last workspace mutation after failed validation.",
                "task_role": "coder",
                "intent": "workspace.rollback_last_change",
                "status": "executed",
            },
            {
                "event_type": "task_envelope_failed",
                "message": "coder envelope `coder-1` failed.",
                "task_role": "coder",
                "status": "failed",
                "receipt_types": ["tool_receipt", "validation_result"],
            },
        ],
        receipts=[
            {
                "tool_name": "workspace.apply_unified_diff",
                "arguments": {"target_path": "app.py"},
                "execution": {},
            },
            {
                "tool_name": "workspace.rollback_last_change",
                "arguments": {},
                "execution": {"restored_paths": ["app.py"]},
            },
        ],
    )

    assert history["bounded_execution"]["verifier_state"] == "failed"
    assert history["bounded_execution"]["rollback_state"] == "completed"
    assert history["bounded_execution"]["mutating_tool_count"] == 2
    assert history["receipt_types"] == ["tool_receipt", "validation_result"]
    assert history["timeline"][3]["value"] == "failed"
    assert history["timeline"][4]["value"] == "completed"
    assert "app.py" in history["touched_paths"]


def test_runtime_surface_summary_counts_resume_and_failures() -> None:
    summary = summarize_runtime_surface(
        [
            {
                "session_id": "openclaw:one",
                "status": "completed",
                "updated_at": "2026-03-30T12:10:00+00:00",
                "execution_history": {
                    "title": "Finished request",
                    "status": "completed",
                    "request_status": "completed",
                    "latest_tool": "workspace.write_file",
                    "bounded_execution": {
                        "resume_available": False,
                        "approval_state": "not_required",
                        "verifier_state": "not_run",
                        "rollback_state": "not_triggered",
                        "restore_state": "not_triggered",
                        "failure_count": 0,
                    },
                },
            },
            {
                "session_id": "openclaw:two",
                "status": "failed",
                "updated_at": "2026-03-30T12:11:00+00:00",
                "execution_history": {
                    "title": "Broken request",
                    "status": "running",
                    "request_status": "running",
                    "latest_tool": "workspace.run_tests",
                    "bounded_execution": {
                        "resume_available": True,
                        "approval_state": "pending",
                        "verifier_state": "running",
                        "rollback_state": "running",
                        "restore_state": "not_triggered",
                        "failure_count": 1,
                    },
                },
            },
        ]
    )

    assert summary["session_count"] == 2
    assert summary["status_counts"]["completed"] == 1
    assert summary["status_counts"]["running"] == 1
    assert summary["active_execution_count"] == 1
    assert summary["resume_ready_count"] == 1
    assert summary["session_pending_approval_count"] == 1
    assert summary["approval_pending_count"] == 1
    assert summary["verifier_pending_count"] == 1
    assert summary["rollback_pending_count"] == 1
    assert summary["recovery_pending_count"] == 1
    assert summary["failure_count"] == 1
    assert summary["latest_session"]["session_id"] == "openclaw:one"


def test_execution_history_clears_stale_approval_after_completion() -> None:
    history = build_runtime_execution_history(
        session={
            "session_id": "openclaw:completed-after-approval",
            "request_preview": "Post the result",
            "task_class": "integration_orchestration",
            "status": "completed",
            "last_message": "Completed after confirmation moved on.",
            "updated_at": "2026-03-30T12:15:00+00:00",
            "resume_available": False,
        },
        checkpoint={
            "checkpoint_id": "runtime-completed-after-approval",
            "status": "completed",
            "step_count": 2,
            "resume_count": 0,
            "last_tool_name": "",
            "pending_intent": {},
        },
        events=[
            {
                "event_type": "task_pending_approval",
                "message": "Awaiting approval to post.",
                "status": "pending_approval",
            },
            {
                "event_type": "task_completed",
                "message": "Posted successfully.",
                "status": "completed",
            },
        ],
    )

    assert history["bounded_execution"]["approval_state"] == "cleared"
    assert history["timeline"][1]["value"] == "cleared"
