from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.runtime_paths import data_path

try:
    from nacl import encoding, signing  # type: ignore

    _SIGNER_BACKEND = "pynacl"
except ImportError:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

    _SIGNER_BACKEND = "cryptography"


_KEY_DIR = data_path("keys")
_PRIV_KEY_PATH = _KEY_DIR / "node_signing_key.b64"
_LOCAL_KEYPAIR: LocalKeypair | None = None


@dataclass
class LocalKeypair:
    signing_key: object
    verify_key: object

    @property
    def peer_id(self) -> str:
        if _SIGNER_BACKEND == "pynacl":
            return self.verify_key.encode(encoder=encoding.HexEncoder).decode("utf-8")
        raw = self.verify_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return raw.hex()


def _ensure_dir() -> None:
    _KEY_DIR.mkdir(parents=True, exist_ok=True)
    (_KEY_DIR / "archive").mkdir(parents=True, exist_ok=True)
    _chmod_safe(_KEY_DIR, 0o700)
    _chmod_safe(_KEY_DIR / "archive", 0o700)


def _chmod_safe(path: Path, mode: int) -> None:
    try:
        if os.name == "posix":
            path.chmod(mode)
    except Exception:
        return


def _enforce_private_key_permissions(path: Path) -> None:
    if os.name != "posix":
        return
    try:
        st_mode = path.stat().st_mode & 0o777
        if st_mode != 0o600:
            path.chmod(0o600)
    except Exception:
        return


def _generate_signing_key():
    if _SIGNER_BACKEND == "pynacl":
        return signing.SigningKey.generate()
    return Ed25519PrivateKey.generate()


def _signing_key_from_seed(seed: bytes):
    if _SIGNER_BACKEND == "pynacl":
        return signing.SigningKey(seed)
    return Ed25519PrivateKey.from_private_bytes(seed)


def _signing_key_bytes(signing_key: object) -> bytes:
    if _SIGNER_BACKEND == "pynacl":
        return bytes(signing_key)
    return signing_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _verify_key(signing_key: object):
    if _SIGNER_BACKEND == "pynacl":
        return signing_key.verify_key
    return signing_key.public_key()


def load_or_create_local_keypair() -> LocalKeypair:
    global _LOCAL_KEYPAIR
    if _LOCAL_KEYPAIR is not None:
        return _LOCAL_KEYPAIR

    _ensure_dir()

    if _PRIV_KEY_PATH.exists():
        _enforce_private_key_permissions(_PRIV_KEY_PATH)
        raw = _PRIV_KEY_PATH.read_text(encoding="utf-8").strip()
        seed = base64.b64decode(raw)
        sk = _signing_key_from_seed(seed)
        _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
        return _LOCAL_KEYPAIR

    sk = _generate_signing_key()
    _PRIV_KEY_PATH.write_text(
        base64.b64encode(_signing_key_bytes(sk)).decode("utf-8"),
        encoding="utf-8",
    )
    _PRIV_KEY_PATH.chmod(0o600)
    _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
    return _LOCAL_KEYPAIR


def sign(payload_bytes: bytes) -> str:
    kp = load_or_create_local_keypair()
    if _SIGNER_BACKEND == "pynacl":
        signed = kp.signing_key.sign(payload_bytes)
        return base64.b64encode(signed.signature).decode("utf-8")
    signature = kp.signing_key.sign(payload_bytes)
    return base64.b64encode(signature).decode("utf-8")


def verify(payload_bytes: bytes, signature: str, peer_id: str) -> bool:
    try:
        sig = base64.b64decode(signature)
        if _SIGNER_BACKEND == "pynacl":
            verify_key = signing.VerifyKey(peer_id, encoder=encoding.HexEncoder)
            verify_key.verify(payload_bytes, sig)
            return True
        verify_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(peer_id))
        verify_key.verify(sig, payload_bytes)
        return True
    except Exception:
        if _SIGNER_BACKEND == "pynacl":
            return False
        return False


def get_local_peer_id() -> str:
    return load_or_create_local_keypair().peer_id


def local_key_path() -> Path:
    _ensure_dir()
    return _PRIV_KEY_PATH


def rotate_local_keypair() -> dict[str, object]:
    global _LOCAL_KEYPAIR
    existing_peer_id = get_local_peer_id()
    existing_path = local_key_path()
    archive_dir = _KEY_DIR / "archive"
    archive_name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{existing_peer_id[:24]}.b64"
    archived_path = archive_dir / archive_name
    archived_path.write_text(existing_path.read_text(encoding="utf-8"), encoding="utf-8")
    _chmod_safe(archived_path, 0o600)
    sk = _generate_signing_key()
    existing_path.write_text(
        base64.b64encode(_signing_key_bytes(sk)).decode("utf-8"),
        encoding="utf-8",
    )
    _enforce_private_key_permissions(existing_path)
    _LOCAL_KEYPAIR = LocalKeypair(signing_key=sk, verify_key=_verify_key(sk))
    return {
        "old_peer_id": existing_peer_id,
        "new_peer_id": _LOCAL_KEYPAIR.peer_id,
        "archived_key_path": archived_path,
    }
