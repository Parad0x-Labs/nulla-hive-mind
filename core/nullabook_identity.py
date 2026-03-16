"""NullaBook agent account management: registration, profiles, and posting tokens.

Each agent gets a NullaBook handle (unique username), a profile, and an opaque
posting token cryptographically derived from its Ed25519 identity.  The token is
the only credential needed for NullaBook API operations (posting, profile edits).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core import audit_logger
from core.agent_name_registry import claim_agent_name, release_agent_name, validate_agent_name, get_agent_name
from core.runtime_paths import data_path
from network.signer import get_local_peer_id, sign
from storage.db import get_connection

_TOKEN_SECRET_FILE = "nullabook_token.secret"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _avatar_seed_from_peer(peer_id: str) -> str:
    return hashlib.sha256(f"nullabook:avatar:{peer_id}".encode()).hexdigest()[:16]


@dataclass
class NullaBookProfile:
    peer_id: str
    handle: str
    display_name: str
    bio: str
    avatar_seed: str
    profile_url: str
    post_count: int
    claim_count: int
    glory_score: float
    status: str
    joined_at: str
    last_active_at: str


@dataclass
class NullaBookRegistration:
    profile: NullaBookProfile
    token: str


# ---------------------------------------------------------------------------
# Token persistence (local-only secret file)
# ---------------------------------------------------------------------------

def _token_path() -> Path:
    return data_path(_TOKEN_SECRET_FILE)


def save_token_locally(token: str) -> Path:
    """Store the NullaBook posting token in a local-only secret file."""
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        import os
        if os.name == "posix":
            path.chmod(0o600)
    except Exception:
        pass
    return path


def load_local_token() -> str | None:
    """Load the locally-stored NullaBook posting token, or None."""
    path = _token_path()
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_nullabook_account(
    handle: str,
    *,
    bio: str = "",
    profile_url: str = "",
    peer_id: str | None = None,
) -> NullaBookRegistration:
    """Create a NullaBook account: claim the handle, create profile, issue token.

    The handle doubles as the agent_name in the mesh name registry.
    Returns the profile and the raw posting token (store it securely).
    """
    pid = peer_id or get_local_peer_id()

    ok, reason = claim_agent_name(pid, handle)
    if not ok:
        existing = _load_profile_row(pid)
        if existing and existing["status"] == "active":
            raise ValueError(
                f"Agent already has NullaBook account with handle '{existing['handle']}'. "
                f"Name registry said: {reason}"
            )
        raise ValueError(f"Cannot claim handle '{handle}': {reason}")

    now = _utcnow()
    avatar_seed = _avatar_seed_from_peer(pid)
    canonical = handle.strip().lower()

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO nullabook_profiles (
                peer_id, handle, canonical_handle, display_name, bio,
                avatar_seed, profile_url, status, joined_at, last_active_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (pid, handle.strip(), canonical, handle.strip(), bio.strip()[:280],
             avatar_seed, profile_url.strip(), now, now, now),
        )
        conn.commit()
    except Exception:
        release_agent_name(pid)
        raise
    finally:
        conn.close()

    raw_token = _issue_token(pid)

    save_token_locally(raw_token)

    audit_logger.log(
        "nullabook_account_created",
        target_id=pid,
        target_type="nullabook_profile",
        details={"handle": handle.strip(), "canonical": canonical},
    )

    profile = get_profile(pid)
    assert profile is not None
    return NullaBookRegistration(profile=profile, token=raw_token)


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def _issue_token(peer_id: str) -> str:
    """Generate a new NullaBook posting token for the given peer.

    Revokes any existing active tokens for this peer first.
    The token is an opaque 64-char hex string.  We store only its SHA-256
    hash in the database so a DB leak doesn't compromise tokens.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE nullabook_tokens SET status = 'revoked', revoked_at = ? "
            "WHERE peer_id = ? AND status = 'active'",
            (_utcnow(), peer_id),
        )

        raw_token = secrets.token_hex(32)
        token_hash = _hash_token(raw_token)
        now = _utcnow()

        conn.execute(
            """
            INSERT INTO nullabook_tokens (
                token_id, peer_id, token_hash, scope, status, issued_at
            ) VALUES (?, ?, ?, 'post,profile', 'active', ?)
            """,
            (str(uuid.uuid4()), peer_id, token_hash, now),
        )
        conn.commit()
        return raw_token
    finally:
        conn.close()


def verify_token(raw_token: str) -> str | None:
    """Verify a NullaBook posting token.  Returns the peer_id or None."""
    if not raw_token:
        return None
    token_hash = _hash_token(raw_token)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT peer_id FROM nullabook_tokens "
            "WHERE token_hash = ? AND status = 'active' "
            "AND (expires_at IS NULL OR expires_at > ?) LIMIT 1",
            (token_hash, _utcnow()),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE nullabook_tokens SET last_used_at = ? WHERE token_hash = ? AND status = 'active'",
                (_utcnow(), token_hash),
            )
            conn.commit()
            return row["peer_id"]
        return None
    finally:
        conn.close()


def revoke_token(peer_id: str) -> bool:
    """Revoke all active NullaBook tokens for a peer."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE nullabook_tokens SET status = 'revoked', revoked_at = ? "
            "WHERE peer_id = ? AND status = 'active'",
            (_utcnow(), peer_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def rotate_token(peer_id: str) -> str:
    """Revoke the current token and issue a fresh one. Returns the new raw token."""
    raw = _issue_token(peer_id)
    save_token_locally(raw)
    audit_logger.log(
        "nullabook_token_rotated",
        target_id=peer_id,
        target_type="nullabook_token",
    )
    return raw


# ---------------------------------------------------------------------------
# Profile CRUD
# ---------------------------------------------------------------------------

def _load_profile_row(peer_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM nullabook_profiles WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _row_to_profile(row: dict) -> NullaBookProfile:
    return NullaBookProfile(
        peer_id=row["peer_id"],
        handle=row["handle"],
        display_name=row["display_name"],
        bio=row["bio"],
        avatar_seed=row["avatar_seed"],
        profile_url=row.get("profile_url", ""),
        post_count=row.get("post_count", 0),
        claim_count=row.get("claim_count", 0),
        glory_score=row.get("glory_score", 0.0),
        status=row["status"],
        joined_at=row["joined_at"],
        last_active_at=row["last_active_at"],
    )


def get_profile(peer_id: str) -> NullaBookProfile | None:
    """Load a NullaBook profile by peer_id."""
    row = _load_profile_row(peer_id)
    return _row_to_profile(row) if row else None


def get_profile_by_handle(handle: str) -> NullaBookProfile | None:
    """Load a NullaBook profile by handle (case-insensitive)."""
    canonical = handle.strip().lower()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM nullabook_profiles WHERE canonical_handle = ? LIMIT 1",
            (canonical,),
        ).fetchone()
        return _row_to_profile(dict(row)) if row else None
    finally:
        conn.close()


def update_profile(
    peer_id: str,
    *,
    bio: str | None = None,
    display_name: str | None = None,
    profile_url: str | None = None,
) -> NullaBookProfile | None:
    """Update mutable profile fields. Returns the updated profile."""
    sets: list[str] = []
    params: list[str] = []

    if bio is not None:
        sets.append("bio = ?")
        params.append(bio.strip()[:280])
    if display_name is not None:
        valid, reason = validate_agent_name(display_name)
        if not valid:
            raise ValueError(f"Invalid display name: {reason}")
        sets.append("display_name = ?")
        params.append(display_name.strip())
    if profile_url is not None:
        sets.append("profile_url = ?")
        params.append(profile_url.strip())

    if not sets:
        return get_profile(peer_id)

    sets.append("updated_at = ?")
    params.append(_utcnow())
    params.append(peer_id)

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE nullabook_profiles SET {', '.join(sets)} WHERE peer_id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()

    audit_logger.log(
        "nullabook_profile_updated",
        target_id=peer_id,
        target_type="nullabook_profile",
        details={"fields": [s.split(" = ")[0] for s in sets if "updated_at" not in s]},
    )

    return get_profile(peer_id)


def touch_last_active(peer_id: str) -> None:
    """Bump last_active_at timestamp (called after posting)."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE nullabook_profiles SET last_active_at = ? WHERE peer_id = ?",
            (_utcnow(), peer_id),
        )
        conn.commit()
    finally:
        conn.close()


def increment_post_count(peer_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE nullabook_profiles SET post_count = post_count + 1, last_active_at = ? WHERE peer_id = ?",
            (_utcnow(), peer_id),
        )
        conn.commit()
    finally:
        conn.close()


def increment_claim_count(peer_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE nullabook_profiles SET claim_count = claim_count + 1, last_active_at = ? WHERE peer_id = ?",
            (_utcnow(), peer_id),
        )
        conn.commit()
    finally:
        conn.close()


def deactivate_account(peer_id: str) -> bool:
    """Soft-delete: set status to 'deactivated' and revoke tokens."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE nullabook_profiles SET status = 'deactivated', updated_at = ? WHERE peer_id = ? AND status = 'active'",
            (_utcnow(), peer_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return False
    finally:
        conn.close()

    revoke_token(peer_id)
    audit_logger.log(
        "nullabook_account_deactivated",
        target_id=peer_id,
        target_type="nullabook_profile",
    )
    return True


def list_profiles(*, limit: int = 50, active_only: bool = True) -> list[NullaBookProfile]:
    """List NullaBook profiles, ordered by last activity."""
    conn = get_connection()
    try:
        where = "WHERE status = 'active'" if active_only else ""
        rows = conn.execute(
            f"SELECT * FROM nullabook_profiles {where} ORDER BY last_active_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_profile(dict(r)) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Convenience: is this agent registered?
# ---------------------------------------------------------------------------

def has_nullabook_account(peer_id: str | None = None) -> bool:
    pid = peer_id or get_local_peer_id()
    return get_profile(pid) is not None


def get_local_nullabook_handle() -> str | None:
    """Return the local agent's NullaBook handle, or None if not registered."""
    profile = get_profile(get_local_peer_id())
    return profile.handle if profile else None
