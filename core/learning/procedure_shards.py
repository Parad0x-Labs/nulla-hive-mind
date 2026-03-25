from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
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
        shards.append(
            ProcedureShardV1(
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
                created_at=str(payload.get("created_at") or ""),
            )
        )
    return shards
