from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core.identity_lifecycle import enforce_active_identity
from core.meet_and_greet_models import SignedApiWriteEnvelope
from network import signer
from network.protocol import consume_nonce_once


_ROUTE_BINDINGS: dict[str, tuple[str, ...]] = {
    "/v1/presence/register": ("agent_id",),
    "/v1/presence/heartbeat": ("agent_id",),
    "/v1/presence/withdraw": ("agent_id",),
    "/v1/knowledge/advertise": ("holder_peer_id",),
    "/v1/knowledge/replicate": ("holder_peer_id",),
    "/v1/knowledge/refresh": ("holder_peer_id",),
    "/v1/knowledge/withdraw": ("holder_peer_id",),
    "/v1/knowledge/challenges/issue": ("requester_peer_id",),
    "/v1/knowledge/challenges/respond": ("holder_peer_id",),
    "/v1/knowledge/challenges/verify": ("requester_peer_id",),
    "/v1/hive/topics": ("created_by_agent_id",),
    "/v1/hive/posts": ("author_agent_id",),
    "/v1/hive/topic-claims": ("agent_id",),
    "/v1/hive/topic-status": ("updated_by_agent_id",),
    "/v1/hive/claim-links": ("agent_id",),
    "/v1/hive/moderation/reviews": ("reviewer_agent_id",),
}


def canonical_signed_write_bytes(
    *,
    target_path: str,
    signer_peer_id: str,
    nonce: str,
    timestamp: datetime,
    payload: dict[str, Any],
) -> bytes:
    body = {
        "target_path": target_path,
        "signer_peer_id": signer_peer_id,
        "nonce": nonce,
        "timestamp": timestamp.isoformat(),
        "payload": payload,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_signed_write_bytes_from_raw(
    *,
    target_path: str,
    signer_peer_id: str,
    nonce: str,
    timestamp_str: str,
    payload: dict[str, Any],
) -> bytes:
    """Reconstruct canonical bytes using the raw timestamp string from the
    wire payload so that precision/format exactly matches what was signed."""
    body = {
        "target_path": target_path,
        "signer_peer_id": signer_peer_id,
        "nonce": nonce,
        "timestamp": timestamp_str,
        "payload": payload,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_signed_write_envelope(
    *,
    target_path: str,
    payload: dict[str, Any],
    signer_peer_id: str | None = None,
    nonce: str | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    active_peer_id = signer.get_local_peer_id()
    resolved_peer_id = signer_peer_id or active_peer_id
    if resolved_peer_id != active_peer_id:
        raise ValueError("Local signing helper can only sign as the local peer id.")
    resolved_timestamp = timestamp or datetime.now(timezone.utc)
    resolved_nonce = nonce or uuid.uuid4().hex
    raw = canonical_signed_write_bytes(
        target_path=target_path,
        signer_peer_id=resolved_peer_id,
        nonce=resolved_nonce,
        timestamp=resolved_timestamp,
        payload=payload,
    )
    envelope = SignedApiWriteEnvelope(
        signer_peer_id=resolved_peer_id,
        nonce=resolved_nonce,
        timestamp=resolved_timestamp,
        target_path=target_path,
        payload=payload,
        signature=signer.sign(raw),
    )
    return {
        "signer_peer_id": envelope.signer_peer_id,
        "nonce": envelope.nonce,
        "timestamp": resolved_timestamp.isoformat(),
        "target_path": envelope.target_path,
        "payload": dict(envelope.payload),
        "signature": envelope.signature,
    }


def unwrap_signed_write(
    *,
    target_path: str,
    raw_payload: dict[str, Any],
    max_skew_minutes: int = 10,
) -> dict[str, Any]:
    payload, _meta = unwrap_signed_write_with_meta(
        target_path=target_path,
        raw_payload=raw_payload,
        max_skew_minutes=max_skew_minutes,
    )
    return payload


def unwrap_signed_write_with_meta(
    *,
    target_path: str,
    raw_payload: dict[str, Any],
    max_skew_minutes: int = 10,
) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope = SignedApiWriteEnvelope.model_validate(raw_payload)
    if envelope.target_path.rstrip("/") != target_path.rstrip("/"):
        raise ValueError("Signed write target_path does not match request path.")
    now = datetime.now(timezone.utc)
    timestamp = envelope.timestamp if envelope.timestamp.tzinfo else envelope.timestamp.replace(tzinfo=timezone.utc)
    if timestamp < now - timedelta(minutes=max_skew_minutes) or timestamp > now + timedelta(minutes=max_skew_minutes):
        raise ValueError("Signed write timestamp outside allowed skew window.")
    raw_timestamp_str = str(raw_payload.get("timestamp") or timestamp.isoformat())
    raw_payload_body = dict(raw_payload.get("payload") or {})
    # Use the exact timestamp string from the wire payload so that the
    # canonical bytes match what was signed.  Re-parsing through datetime
    # can alter precision (e.g. trailing zeros) and break verification.
    raw = _canonical_signed_write_bytes_from_raw(
        target_path=envelope.target_path,
        signer_peer_id=envelope.signer_peer_id,
        nonce=envelope.nonce,
        timestamp_str=raw_timestamp_str,
        payload=raw_payload_body,
    )
    enforce_active_identity(envelope.signer_peer_id, scope="signed_write")
    if not signer.verify(raw, envelope.signature, envelope.signer_peer_id):
        raise ValueError("Invalid signed write signature.")
    payload = dict(raw_payload_body)
    write_grant = payload.pop("write_grant", None)
    _enforce_route_binding(target_path, payload, envelope.signer_peer_id)
    if not consume_nonce_once(envelope.signer_peer_id, envelope.nonce):
        raise ValueError("Replay detected for signed write envelope.")
    return payload, {
        "signer_peer_id": envelope.signer_peer_id,
        "nonce": envelope.nonce,
        "timestamp": raw_timestamp_str,
        "write_grant": dict(write_grant) if isinstance(write_grant, dict) else None,
    }


def _enforce_route_binding(target_path: str, payload: dict[str, Any], signer_peer_id: str) -> None:
    path = target_path.rstrip("/") or "/"
    bound_fields = _ROUTE_BINDINGS.get(path)
    if path == "/v1/payments/status":
        payer = str(payload.get("payer_peer_id") or "")
        payee = str(payload.get("payee_peer_id") or "")
        if signer_peer_id not in {payer, payee}:
            raise ValueError("Signed write signer must match the payer or payee for payment status updates.")
        return
    if path == "/v1/cluster/nodes":
        metadata = dict(payload.get("metadata") or {})
        owner_peer_id = str(metadata.get("owner_peer_id") or signer_peer_id)
        if owner_peer_id != signer_peer_id:
            raise ValueError("Signed write signer must match metadata.owner_peer_id for meet node registration.")
        metadata["owner_peer_id"] = signer_peer_id
        payload["metadata"] = metadata
        return
    if not bound_fields:
        return
    for field in bound_fields:
        if str(payload.get(field) or "") != signer_peer_id:
            raise ValueError(f"Signed write signer must match payload field '{field}'.")
