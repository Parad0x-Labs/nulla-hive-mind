from __future__ import annotations

from storage.shard_reuse_outcomes import (
    record_shard_reuse_outcomes,
    summarize_reuse_outcomes_for_shards,
)


def test_record_shard_reuse_outcomes_dedupes_citations_and_summarizes_latest() -> None:
    citation = {
        "kind": "remote_shard",
        "shard_id": "remote-shard-1",
        "receipt_id": "receipt-1",
        "source_peer_id": "peer-1",
        "source_node_id": "node-1",
        "manifest_id": "manifest-1",
        "content_hash": "content-1",
        "validation_state": "signature_and_manifest_verified",
    }

    rows = record_shard_reuse_outcomes(
        citations=[citation, dict(citation)],
        task_id="task-1",
        session_id="session-1",
        task_class="research",
        response_class="generic_conversation",
        success=True,
        durable=False,
        details={"surface": "openclaw"},
    )

    assert len(rows) == 1
    summary = summarize_reuse_outcomes_for_shards(["remote-shard-1"])
    assert summary["remote-shard-1"]["total_count"] == 1
    assert summary["remote-shard-1"]["success_count"] == 1
    assert summary["remote-shard-1"]["durable_count"] == 0
    assert summary["remote-shard-1"]["selected_count"] == 1
    assert summary["remote-shard-1"]["selected_success_count"] == 1
    assert summary["remote-shard-1"]["answer_backed_count"] == 1
    assert summary["remote-shard-1"]["answer_backed_success_count"] == 1
    assert summary["remote-shard-1"]["last_outcome_label"] == "successful"
    assert summary["remote-shard-1"]["last_response_class"] == "generic_conversation"
    assert summary["remote-shard-1"]["last_validation_state"] == "signature_and_manifest_verified"


def test_record_shard_reuse_outcomes_only_marks_selected_remote_shard_as_answer_backed_when_multiple_citations_present() -> None:
    rows = record_shard_reuse_outcomes(
        citations=[
            {
                "kind": "remote_shard",
                "shard_id": "remote-shard-primary",
                "receipt_id": "receipt-primary",
                "selected_for_plan": True,
                "answer_backed": True,
                "rendered_via": "reasoning_engine",
                "response_reason": "grounded_plan_response",
            },
            {
                "kind": "remote_shard",
                "shard_id": "remote-shard-incidental",
                "receipt_id": "receipt-incidental",
                "selected_for_plan": False,
                "answer_backed": False,
            },
        ],
        task_id="task-2",
        session_id="session-2",
        task_class="research",
        response_class="generic_conversation",
        success=True,
        durable=True,
    )

    assert len(rows) == 2
    summary = summarize_reuse_outcomes_for_shards(["remote-shard-primary", "remote-shard-incidental"])
    assert summary["remote-shard-primary"]["selected_count"] == 1
    assert summary["remote-shard-primary"]["answer_backed_count"] == 1
    assert summary["remote-shard-primary"]["last_rendered_via"] == "reasoning_engine"
    assert summary["remote-shard-primary"]["last_response_reason"] == "grounded_plan_response"
    assert summary["remote-shard-incidental"]["success_count"] == 1
    assert summary["remote-shard-incidental"]["selected_count"] == 0
    assert summary["remote-shard-incidental"]["answer_backed_count"] == 0


def test_record_shard_reuse_outcomes_keeps_single_selected_remote_shard_non_answer_backed_when_explicitly_false() -> None:
    rows = record_shard_reuse_outcomes(
        citations=[
            {
                "kind": "remote_shard",
                "shard_id": "remote-shard-selected-only",
                "receipt_id": "receipt-selected-only",
                "selected_for_plan": True,
                "answer_backed": False,
                "rendered_via": "reasoning_engine",
                "response_reason": "grounded_plan_response",
            }
        ],
        task_id="task-3",
        session_id="session-3",
        task_class="research",
        response_class="generic_conversation",
        success=True,
        durable=True,
    )

    assert len(rows) == 1
    summary = summarize_reuse_outcomes_for_shards(["remote-shard-selected-only"])
    assert summary["remote-shard-selected-only"]["selected_count"] == 1
    assert summary["remote-shard-selected-only"]["answer_backed_count"] == 0
    assert summary["remote-shard-selected-only"]["answer_backed_success_count"] == 0
