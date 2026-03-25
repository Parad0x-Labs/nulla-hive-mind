from __future__ import annotations

from .procedure_shards import ProcedureShardV1


def summarize_procedure_metrics(procedures: list[ProcedureShardV1]) -> dict[str, int]:
    summary = {
        "total": len(procedures),
        "local_only": 0,
        "trusted_hive": 0,
        "public": 0,
        "verified_success": 0,
    }
    for shard in procedures:
        if shard.shareability == "local_only":
            summary["local_only"] += 1
        elif shard.shareability == "trusted_hive":
            summary["trusted_hive"] += 1
        elif shard.shareability == "public":
            summary["public"] += 1
        if shard.success_signal == "verified_success":
            summary["verified_success"] += 1
    return summary
