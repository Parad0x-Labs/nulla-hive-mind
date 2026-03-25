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
    assert summary["remote-shard-1"]["last_outcome_label"] == "successful"
    assert summary["remote-shard-1"]["last_response_class"] == "generic_conversation"
    assert summary["remote-shard-1"]["last_validation_state"] == "signature_and_manifest_verified"
