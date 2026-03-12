from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from network import signer


@dataclass(frozen=True)
class ContextManifest:
    manifest_id: str
    task_id: str
    trace_id: str
    evidence_hashes: list[str]
    source_metadata: list[dict[str, Any]]
    redaction_markers: list[str]
    truncation_markers: list[str]
    signature: str


def hash_evidence_item(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_context_manifest(
    *,
    task_id: str,
    trace_id: str,
    evidence_items: list[Any],
    source_metadata: list[dict[str, Any]],
    redaction_markers: list[str] | None = None,
    truncation_markers: list[str] | None = None,
) -> ContextManifest:
    manifest_id = str(uuid.uuid4())
    payload = {
        "manifest_id": manifest_id,
        "task_id": task_id,
        "trace_id": trace_id,
        "evidence_hashes": [hash_evidence_item(item) for item in evidence_items],
        "source_metadata": source_metadata,
        "redaction_markers": list(redaction_markers or []),
        "truncation_markers": list(truncation_markers or []),
    }
    signature = signer.sign(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return ContextManifest(
        manifest_id=manifest_id,
        task_id=task_id,
        trace_id=trace_id,
        evidence_hashes=payload["evidence_hashes"],
        source_metadata=payload["source_metadata"],
        redaction_markers=payload["redaction_markers"],
        truncation_markers=payload["truncation_markers"],
        signature=signature,
    )
