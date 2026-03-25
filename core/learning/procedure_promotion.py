from __future__ import annotations

from typing import Any

from .procedure_shards import ProcedureShardV1, save_procedure_shard


def promote_verified_procedure(
    *,
    task_class: str,
    title: str,
    preconditions: list[str] | tuple[str, ...],
    steps: list[str] | tuple[str, ...],
    tool_receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    validation: dict[str, Any],
    rollback: dict[str, Any],
    privacy_class: str = "local_private",
    shareability: str = "local_only",
    success_signal: str = "verified_success",
    liquefy_bundle_ref: str = "",
) -> ProcedureShardV1 | None:
    if not bool(validation.get("ok")):
        return None
    shard = ProcedureShardV1.create(
        task_class=task_class,
        title=title,
        preconditions=preconditions,
        steps=steps,
        tool_receipts=tool_receipts,
        validation=validation,
        rollback=rollback,
        privacy_class=privacy_class,
        shareability=shareability,
        success_signal=success_signal,
        liquefy_bundle_ref=liquefy_bundle_ref,
    )
    save_procedure_shard(shard)
    return shard
