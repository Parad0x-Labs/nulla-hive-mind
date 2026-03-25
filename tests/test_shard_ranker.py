from __future__ import annotations

from types import SimpleNamespace

from core.shard_ranker import rank


def _candidate(*, shard_id: str, source_type: str = "peer_received", trust_score: float = 0.7) -> dict:
    return {
        "shard_id": shard_id,
        "source_type": source_type,
        "trust_score": trust_score,
        "semantic_match": 0.76,
        "environment_match": 0.75,
        "quality_score": 0.72,
        "freshness_ts": "",
        "local_validation_count": 1,
        "local_failure_count": 0,
        "risk_flags": [],
        "reuse_outcomes": {},
    }


def test_rank_prefers_peer_received_shard_with_proven_reuse_success() -> None:
    task = SimpleNamespace(task_id="task-1")
    baseline = _candidate(shard_id="peer-baseline")
    proven = _candidate(shard_id="peer-proven")
    proven["reuse_outcomes"] = {
        "total_count": 4,
        "success_count": 4,
        "durable_count": 3,
    }

    ranked = rank([baseline, proven], task)

    assert ranked[0]["shard_id"] == "peer-proven"
    assert ranked[0]["reuse_outcome_adjustment"] > ranked[1]["reuse_outcome_adjustment"]


def test_rank_does_not_apply_remote_reuse_bonus_to_local_generated_shard() -> None:
    task = SimpleNamespace(task_id="task-2")
    local_candidate = _candidate(shard_id="local-1", source_type="local_generated")
    local_candidate["reuse_outcomes"] = {
        "total_count": 8,
        "success_count": 8,
        "durable_count": 8,
    }

    ranked = rank([local_candidate], task)

    assert ranked[0]["reuse_outcome_adjustment"] == 0.0
