from __future__ import annotations

from .procedure_shards import ProcedureShardV1


def rank_reusable_procedures(
    *,
    task_class: str,
    query_text: str,
    procedures: list[ProcedureShardV1],
    limit: int = 5,
) -> list[ProcedureShardV1]:
    query_tokens = {token for token in "".join(ch if ch.isalnum() else " " for ch in str(query_text or "").lower()).split() if token}
    scored: list[tuple[float, ProcedureShardV1]] = []
    for shard in procedures:
        score = 0.0
        if shard.task_class == task_class:
            score += 5.0
        haystack = " ".join([shard.title, *shard.preconditions, *shard.steps]).lower()
        overlap = len(query_tokens & {token for token in "".join(ch if ch.isalnum() else " " for ch in haystack).split() if token})
        score += float(overlap)
        score += min(float(shard.reuse_count or 0), 10.0) * 0.1
        score += min(float(shard.verified_reuse_count or 0), 5.0) * 0.5
        if shard.shareability in {"local_only", "trusted_hive"}:
            score += 0.25
        if score > 0:
            scored.append((score, shard))
    scored.sort(key=lambda item: (item[0], item[1].created_at, item[1].procedure_id), reverse=True)
    return [item[1] for item in scored[: max(1, int(limit or 5))]]
