from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable

from storage.blob_index import get_blob, upsert_blob
from storage.chunk_store import DEFAULT_CHUNK_SIZE, load_chunk, store_chunk
from storage.manifest_store import load_manifest, save_manifest


def _chunk_bytes(data: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterable[bytes]:
    for idx in range(0, len(data), chunk_size):
        yield data[idx : idx + chunk_size]


def put_bytes(data: bytes, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict:
    chunk_hashes: list[str] = []
    for chunk in _chunk_bytes(data, chunk_size=chunk_size):
        chunk_hash, _ = store_chunk(chunk)
        chunk_hashes.append(chunk_hash)

    blob_hash = hashlib.sha256(data).hexdigest()
    manifest_id = str(uuid.uuid4())
    manifest = {
        "manifest_id": manifest_id,
        "blob_hash": blob_hash,
        "chunk_hashes": chunk_hashes,
        "chunk_size": chunk_size,
        "total_bytes": len(data),
    }
    save_manifest(manifest_id, blob_hash, manifest)
    upsert_blob(blob_hash, len(data), len(chunk_hashes), manifest_id)
    return manifest


def get_bytes(blob_hash: str) -> bytes | None:
    meta = get_blob(blob_hash)
    if not meta:
        return None
    manifest = load_manifest(meta["manifest_id"])
    if not manifest:
        return None
    parts: list[bytes] = []
    for chunk_hash in manifest["chunk_hashes"]:
        data = load_chunk(chunk_hash)
        if data is None:
            return None
        parts.append(data)
    return b"".join(parts)
