from __future__ import annotations

import hashlib
from pathlib import Path

from core.runtime_paths import data_path

CHUNK_ROOT = data_path("cas_chunks")
DEFAULT_CHUNK_SIZE = 64 * 1024


def _chunk_dir(chunk_hash: str) -> Path:
    path = CHUNK_ROOT / chunk_hash[:2] / chunk_hash[2:4]
    path.mkdir(parents=True, exist_ok=True)
    return path


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_chunk(data: bytes) -> tuple[str, Path]:
    chunk_hash = hash_bytes(data)
    path = _chunk_dir(chunk_hash) / chunk_hash
    if not path.exists():
        path.write_bytes(data)
    return chunk_hash, path


def load_chunk(chunk_hash: str) -> bytes | None:
    path = _chunk_dir(chunk_hash) / chunk_hash
    if not path.exists():
        return None
    return path.read_bytes()
