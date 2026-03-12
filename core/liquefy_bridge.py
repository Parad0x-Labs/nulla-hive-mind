from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import audit_logger
from core.runtime_paths import data_path
from storage.db import get_connection

# Attempt to load the Liquefy ecosystem (package: liquefy-openclaw).
# Liquefy's modules use bare imports (e.g. "from common_zstd import ...") so
# the api/ directory must be on sys.path for them to resolve.
try:
    import importlib
    import sys as _sys
    _api_pkg = importlib.import_module("api")
    _api_dir = str(Path(_api_pkg.__path__[0]))
    if _api_dir not in _sys.path:
        _sys.path.insert(0, _api_dir)

    from api.liquefy_audit_chain import AuditChain
    from api.liquefy_safety import LiquefySafety
    from api.common_zstd import make_cctx
    import zstandard as zstd
    LIQUEFY_AVAILABLE = True
except ImportError:
    LIQUEFY_AVAILABLE = False

_AUDIT_CHAIN: AuditChain | None = None
_NULLA_VAULT = Path(os.environ.get("NULLA_LIQUEFY_HOME", str(data_path("liquefy_vault")))).expanduser().resolve()
_DEFAULT_ARCHIVE_LEVEL = max(1, min(19, int(os.environ.get("NULLA_LIQUEFY_ARCHIVE_LEVEL", "12"))))
_DEFAULT_KNOWLEDGE_LEVEL = max(1, min(19, int(os.environ.get("NULLA_LIQUEFY_KNOWLEDGE_LEVEL", "19"))))


def _get_audit_chain() -> AuditChain | None:
    global _AUDIT_CHAIN
    if not LIQUEFY_AVAILABLE:
        return None
    if _AUDIT_CHAIN is None:
        _AUDIT_CHAIN = AuditChain(audit_dir=_vault_dir("audit"), tenant="nulla")
    return _AUDIT_CHAIN


def _vault_dir(category: str) -> Path:
    preferred = (_NULLA_VAULT / str(category or "artifacts").strip()).resolve()
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except OSError:
        fallback = data_path("liquefy_vault", str(category or "artifacts").strip())
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback.resolve()


def _async_run(func):
    """Decorator to ensure Liquefy never blocks the NULLA hot path."""
    def wrapper(*args, **kwargs):
        t = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
        t.start()
    return wrapper


@_async_run
def stream_telemetry_event(event_type: str, target_id: str, details: dict[str, Any]) -> None:
    """
    Records a runtime telemetry event in Liquefy's tamper-proof audit chain.
    Never blocks the main processing path.
    """
    if not LIQUEFY_AVAILABLE:
        return

    try:
        chain = _get_audit_chain()
        if chain:
            chain.append(
                event_type,
                span_id=target_id,
                payload=details,
            )
    except Exception as e:
        audit_logger.log("liquefy_telemetry_error", target_id=target_id, target_type="system", details={"error": str(e)})


@_async_run
def export_task_bundle(parent_task_id: str) -> None:
    """
    Builds a deterministic bundle of a finalized task, compresses it via
    Liquefy's zstd pipeline, and records the operation in the audit chain.
    """
    if not LIQUEFY_AVAILABLE:
        return

    conn = get_connection()
    try:
        parent = conn.execute("SELECT * FROM local_tasks WHERE task_id = ?", (parent_task_id,)).fetchone()
        if not parent:
            return

        capsules = conn.execute("SELECT * FROM task_capsules WHERE task_id LIKE ?", (f"{parent_task_id}%",)).fetchall()
        final = conn.execute("SELECT * FROM finalized_responses WHERE parent_task_id = ?", (parent_task_id,)).fetchone()

        bundle = {
            "trace_id": parent_task_id,
            "metadata": dict(parent),
            "capsules": [dict(c) for c in capsules],
            "final_response": dict(final) if final else None,
            "vault_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        raw = json.dumps(bundle).encode("utf-8")
        cctx = make_cctx(level=12, text_like=True)
        compressed = cctx.compress(raw)

        vault_dir = _vault_dir("bundles")
        out_path = vault_dir / f"{parent_task_id}.zst"
        out_path.write_bytes(compressed)

        chain = _get_audit_chain()
        if chain:
            chain.append(
                "task_bundle_packed",
                task_id=parent_task_id,
                raw_bytes=len(raw),
                compressed_bytes=len(compressed),
                ratio=round(len(raw) / max(1, len(compressed)), 2),
                path=str(out_path),
            )

        audit_logger.log(
            "liquefy_vault_packed",
            target_id=parent_task_id,
            target_type="task",
            details={"path": str(out_path), "ratio": round(len(raw) / max(1, len(compressed)), 2)}
        )
    except Exception as e:
        audit_logger.log("liquefy_vault_error", target_id=parent_task_id, target_type="task", details={"error": str(e)})
    finally:
        conn.close()


def apply_local_execution_safety(sandbox_context: dict[str, Any], payload: dict[str, Any]) -> bool:
    """
    Validates payload integrity through Liquefy's MRTV (Mandatory Round-Trip
    Verification) safety layer. Returns False if execution should halt.
    """
    if not LIQUEFY_AVAILABLE:
        return True

    try:
        safety = LiquefySafety(enabled=True)
        raw = json.dumps(payload).encode("utf-8")
        sealed = safety.seal(
            raw,
            compress_func=lambda d: make_cctx(level=3).compress(d),
            decompress_func=lambda d: zstd.ZstdDecompressor().decompress(d),
            engine_id=b"NULA",
        )
        return sealed is not None
    except Exception as e:
        audit_logger.log("liquefy_safety_guard_error", target_id="", target_type="system", details={"error": str(e)})

    return True


def lookup_cold_archive_candidates(query_text: str, *, limit: int = 3) -> list[dict[str, Any]]:
    """
    Metadata-only archive lookup used by the cold context gate.
    This never promotes Liquefy into the hot prompt path.
    """
    query_tokens = {
        token
        for token in "".join(ch if ch.isalnum() else " " for ch in (query_text or "").lower()).split()
        if len(token) >= 3
    }
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT parent_task_id, rendered_persona_text, raw_synthesized_text, status_marker, confidence_score, created_at
            FROM finalized_responses
            ORDER BY created_at DESC
            LIMIT 40
            """
        ).fetchall()
    finally:
        conn.close()

    ranked: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        rendered = str(row["rendered_persona_text"] or "")
        raw = str(row["raw_synthesized_text"] or "")
        combined = f"{rendered} {raw}".lower()
        overlap = len(query_tokens & {token for token in "".join(ch if ch.isalnum() else " " for ch in combined).split() if len(token) >= 3})
        if query_tokens and overlap == 0:
            continue
        ranked.append(
            (
                overlap,
                {
                    "archive_id": row["parent_task_id"],
                    "source_type": "cold_archive",
                    "storage_backend": "liquefy" if LIQUEFY_AVAILABLE else "local_archive",
                    "status_marker": row["status_marker"],
                    "confidence_score": float(row["confidence_score"] or 0.0),
                    "created_at": row["created_at"],
                    "preview": (rendered or raw)[:220],
                },
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in ranked[:limit]]


def pack_bytes_artifact(
    *,
    artifact_id: str,
    payload: bytes,
    category: str = "artifacts",
    file_stem: str | None = None,
    compression_level: int | None = None,
    text_like: bool = True,
    profile: str = "archive",
) -> dict[str, Any]:
    """
    Pack already-serialized bytes into the NULLA vault and return the packing
    metadata plus the compressed payload so callers can index it elsewhere.
    """
    clean_artifact_id = str(artifact_id or "").strip()
    if not clean_artifact_id:
        raise ValueError("artifact_id is required")

    raw = bytes(payload)
    raw_digest = hashlib.sha256(raw).hexdigest()
    safe_stem = _safe_file_stem(file_stem or clean_artifact_id)
    out_dir = _vault_dir(str(category or "artifacts"))

    level = int(compression_level or (_DEFAULT_KNOWLEDGE_LEVEL if profile == "knowledge" else _DEFAULT_ARCHIVE_LEVEL))
    if LIQUEFY_AVAILABLE:
        cctx = make_cctx(level=max(1, min(19, level)), text_like=text_like)
        compressed = cctx.compress(raw)
        suffix = ".zst"
        storage_backend = "liquefy"
    else:
        compressed = gzip.compress(raw, compresslevel=max(1, min(9, level)))
        suffix = ".gz"
        storage_backend = "local_archive"

    compressed_digest = hashlib.sha256(compressed).hexdigest()
    out_path = (out_dir / f"{safe_stem}{suffix}").resolve()
    out_path.write_bytes(compressed)

    return {
        "artifact_id": clean_artifact_id,
        "path": str(out_path),
        "storage_backend": storage_backend,
        "content_sha256": raw_digest,
        "compressed_sha256": compressed_digest,
        "raw_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "compression_ratio": round(len(raw) / max(1, len(compressed)), 4),
        "compression_level": max(1, level),
        "profile": profile,
        "compressed_payload": compressed,
    }


def load_packed_bytes(*, payload: bytes, storage_backend: str) -> bytes:
    clean_backend = str(storage_backend or "").strip().lower()
    if clean_backend == "liquefy":
        if not LIQUEFY_AVAILABLE:
            raise RuntimeError("Liquefy payload requested but Liquefy runtime is unavailable.")
        return zstd.ZstdDecompressor().decompress(payload)
    if clean_backend in {"local_archive", "gzip"}:
        return gzip.decompress(payload)
    raise ValueError(f"Unsupported packed payload backend: {storage_backend}")


def pack_json_artifact(
    *,
    artifact_id: str,
    payload: Any,
    category: str = "artifacts",
    file_stem: str | None = None,
) -> dict[str, Any]:
    """
    Synchronously pack a JSON artifact into the NULLA vault so it can be indexed
    and referenced from Hive/topic research flows.
    """
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    packed = pack_bytes_artifact(
        artifact_id=artifact_id,
        payload=raw,
        category=category,
        file_stem=file_stem,
        profile="archive",
        text_like=True,
    )

    chain = _get_audit_chain()
    if chain:
        try:
            chain.append(
                "research_artifact_packed",
                span_id=str(packed["artifact_id"]),
                payload={
                    "artifact_id": str(packed["artifact_id"]),
                    "storage_backend": str(packed["storage_backend"]),
                    "path": str(packed["path"]),
                    "raw_bytes": int(packed["raw_bytes"]),
                    "compressed_bytes": int(packed["compressed_bytes"]),
                    "compression_ratio": float(packed["compression_ratio"]),
                    "content_sha256": str(packed["content_sha256"]),
                },
            )
        except Exception:
            pass

    audit_logger.log(
        "liquefy_json_artifact_packed",
        target_id=str(packed["artifact_id"]),
        target_type="artifact",
        details={
            "storage_backend": str(packed["storage_backend"]),
            "path": str(packed["path"]),
            "raw_bytes": int(packed["raw_bytes"]),
            "compressed_bytes": int(packed["compressed_bytes"]),
            "compression_ratio": float(packed["compression_ratio"]),
            "content_sha256": str(packed["content_sha256"]),
        },
    )
    return packed


def _safe_file_stem(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(value or "").strip())
    compact = "-".join(part for part in text.split("-") if part)
    return compact[:96] or "artifact"
