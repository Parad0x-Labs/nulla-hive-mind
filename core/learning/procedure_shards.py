from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from core.runtime_paths import data_path


@dataclass(frozen=True)
class ProcedureShardV1:
    procedure_id: str
    task_class: str
    title: str
    preconditions: tuple[str, ...]
    steps: tuple[str, ...]
    tool_receipts: tuple[dict[str, Any], ...]
    validation: dict[str, Any]
    rollback: dict[str, Any]
    privacy_class: str
    shareability: str
    success_signal: str
    liquefy_bundle_ref: str = ""
    reuse_count: int = 0
    verified_reuse_count: int = 0
    last_reused_at: str = ""
    last_reuse_task_class: str = ""
    last_reuse_outcome: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = "nulla.procedure_shard.v1"
        payload["preconditions"] = list(self.preconditions)
        payload["steps"] = list(self.steps)
        payload["tool_receipts"] = [dict(item) for item in self.tool_receipts]
        return payload

    @classmethod
    def create(
        cls,
        *,
        task_class: str,
        title: str,
        preconditions: list[str] | tuple[str, ...],
        steps: list[str] | tuple[str, ...],
        tool_receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        validation: dict[str, Any],
        rollback: dict[str, Any],
        privacy_class: str,
        shareability: str,
        success_signal: str,
        liquefy_bundle_ref: str = "",
        reuse_count: int = 0,
        verified_reuse_count: int = 0,
        last_reused_at: str = "",
        last_reuse_task_class: str = "",
        last_reuse_outcome: str = "",
    ) -> ProcedureShardV1:
        return cls(
            procedure_id=f"procedure-{uuid.uuid4().hex}",
            task_class=str(task_class or "").strip() or "general",
            title=str(title or "").strip(),
            preconditions=tuple(str(item).strip() for item in preconditions if str(item).strip()),
            steps=tuple(str(item).strip() for item in steps if str(item).strip()),
            tool_receipts=tuple(dict(item) for item in tool_receipts if isinstance(item, dict)),
            validation=dict(validation or {}),
            rollback=dict(rollback or {}),
            privacy_class=str(privacy_class or "local_private"),
            shareability=str(shareability or "local_only"),
            success_signal=str(success_signal or "").strip() or "verified_success",
            liquefy_bundle_ref=str(liquefy_bundle_ref or "").strip(),
            reuse_count=max(0, int(reuse_count or 0)),
            verified_reuse_count=max(0, int(verified_reuse_count or 0)),
            last_reused_at=str(last_reused_at or "").strip(),
            last_reuse_task_class=str(last_reuse_task_class or "").strip(),
            last_reuse_outcome=str(last_reuse_outcome or "").strip(),
        )


def procedures_dir() -> str:
    return str(data_path("learning", "procedures"))


def save_procedure_shard(shard: ProcedureShardV1) -> str:
    root = data_path("learning", "procedures")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{shard.procedure_id}.json"
    path.write_text(json.dumps(shard.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def load_procedure_shards() -> list[ProcedureShardV1]:
    root = data_path("learning", "procedures")
    if not root.exists():
        return []
    shards: list[ProcedureShardV1] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        shards.append(_procedure_shard_from_payload(payload))
    return shards


def record_procedure_reuse(
    *,
    procedure_ids: list[str] | tuple[str, ...],
    task_class: str,
    verified: bool,
    outcome: str,
) -> list[ProcedureShardV1]:
    clean_ids = {str(item).strip() for item in procedure_ids if str(item).strip()}
    if not clean_ids:
        return []
    root = data_path("learning", "procedures")
    if not root.exists():
        return []
    reused_at = datetime.now(timezone.utc).isoformat()
    updated: list[ProcedureShardV1] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        shard = _procedure_shard_from_payload(payload)
        if shard.procedure_id not in clean_ids:
            continue
        next_shard = replace(
            shard,
            reuse_count=int(shard.reuse_count or 0) + 1,
            verified_reuse_count=int(shard.verified_reuse_count or 0) + (1 if verified else 0),
            last_reused_at=reused_at,
            last_reuse_task_class=str(task_class or "").strip() or shard.task_class,
            last_reuse_outcome=str(outcome or "").strip() or ("verified_success" if verified else "completed"),
        )
        path.write_text(json.dumps(next_shard.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        updated.append(next_shard)
    return updated


def _procedure_shard_from_payload(payload: dict[str, Any]) -> ProcedureShardV1:
    return ProcedureShardV1(
        procedure_id=str(payload.get("procedure_id") or ""),
        task_class=str(payload.get("task_class") or ""),
        title=str(payload.get("title") or ""),
        preconditions=tuple(str(item).strip() for item in list(payload.get("preconditions") or []) if str(item).strip()),
        steps=tuple(str(item).strip() for item in list(payload.get("steps") or []) if str(item).strip()),
        tool_receipts=tuple(dict(item) for item in list(payload.get("tool_receipts") or []) if isinstance(item, dict)),
        validation=dict(payload.get("validation") or {}),
        rollback=dict(payload.get("rollback") or {}),
        privacy_class=str(payload.get("privacy_class") or "local_private"),
        shareability=str(payload.get("shareability") or "local_only"),
        success_signal=str(payload.get("success_signal") or "verified_success"),
        liquefy_bundle_ref=str(payload.get("liquefy_bundle_ref") or ""),
        reuse_count=max(0, int(payload.get("reuse_count") or 0)),
        verified_reuse_count=max(0, int(payload.get("verified_reuse_count") or 0)),
        last_reused_at=str(payload.get("last_reused_at") or ""),
        last_reuse_task_class=str(payload.get("last_reuse_task_class") or ""),
        last_reuse_outcome=str(payload.get("last_reuse_outcome") or ""),
        created_at=str(payload.get("created_at") or ""),
    )
