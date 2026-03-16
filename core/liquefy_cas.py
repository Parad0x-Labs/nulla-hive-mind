import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from core.runtime_paths import data_path

CAS_DIR = data_path("cas")
CHUNK_SIZE = 2 * 1024 * 1024  # 2MB chunks

def _ensure_cas_dir() -> None:
    CAS_DIR.mkdir(parents=True, exist_ok=True)

def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def get_chunk_path(chunk_hash: str) -> Path:
    """Returns the path for a given chunk hash, using a 2-level directory prefix structure."""
    if len(chunk_hash) < 4:
        raise ValueError("Invalid chunk hash")

    prefix_dir = CAS_DIR / chunk_hash[:2] / chunk_hash[2:4]
    prefix_dir.mkdir(parents=True, exist_ok=True)
    return prefix_dir / chunk_hash

def store_chunk(data: bytes) -> str:
    """
    Hashes the data, saves it to disk if it doesn't already exist,
    and returns its SHA-256 hash. (Deduplication)
    """
    _ensure_cas_dir()
    chunk_hash = hash_bytes(data)
    chunk_path = get_chunk_path(chunk_hash)

    if not chunk_path.exists():
        with open(chunk_path, "wb") as f:
            f.write(data)

    return chunk_hash

def get_chunk(chunk_hash: str) -> Optional[bytes]:
    """Retrieves a chunk from disk by its hash."""
    chunk_path = get_chunk_path(chunk_hash)
    if chunk_path.exists():
        with open(chunk_path, "rb") as f:
            return f.read()
    return None

def chunk_file(file_path: str) -> dict[str, Any]:
    """
    Streams a file from disk, chunks it into 2MB pieces, stores them in CAS,
    and returns a manifest dictionary representing the file.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File {file_path} not found")

    file_size = path.stat().st_size
    chunk_hashes = []

    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            chunk_hash = store_chunk(chunk)
            chunk_hashes.append(chunk_hash)

    manifest = {
        "filename": path.name,
        "size_bytes": file_size,
        "chunks": chunk_hashes,
        "chunk_size_bytes": CHUNK_SIZE
    }

    # Store the manifest itself as a chunk so the whole file can be referenced by one root hash
    manifest_json = json.dumps(manifest, sort_keys=True).encode("utf-8")
    root_hash = store_chunk(manifest_json)

    manifest["root_hash"] = root_hash
    return manifest

def reconstruct_file(root_hash: str, output_path: str) -> bool:
    """
    Reconstructs a file from its root manifest hash into the specified output path.
    Returns True if successful, False if chunks are missing.
    """
    manifest_bytes = get_chunk(root_hash)
    if not manifest_bytes:
        return False

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except Exception:
        return False

    chunk_hashes = manifest.get("chunks", [])

    # Verify we have all chunks before writing
    for h in chunk_hashes:
        if not get_chunk_path(h).exists():
            return False

    # Reconstruct
    with open(output_path, "wb") as f:
        for h in chunk_hashes:
            data = get_chunk(h)
            if data:
                f.write(data)

    return True
