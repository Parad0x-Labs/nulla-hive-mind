from __future__ import annotations

import uuid
from typing import Any

from core import audit_logger, policy_engine
from core.discovery_index import (
    endpoint_for_peer,
    get_best_helpers,
    recent_peer_endpoints,
)
from network.protocol import encode_message
from network.signer import get_local_peer_id as local_peer_id
from network.transport import send_message


def _nonce() -> str:
    return uuid.uuid4().hex


def broadcast_capability_ad(raw_message: bytes, *, limit: int = 25) -> int:
    sent = 0
    for _peer_id, host, port in recent_peer_endpoints(exclude_peer_id=local_peer_id(), limit=limit):
        if send_message(host, port, raw_message):
            sent += 1

    audit_logger.log(
        "capability_ad_broadcast",
        target_id=local_peer_id(),
        target_type="peer",
        details={"sent": sent},
    )
    return sent


def dispatch_query_shard(query: dict[str, Any], *, limit: int = 5) -> int:
    msg = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="QUERY_SHARD",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=query,
    )

    sent = 0
    for _peer_id, host, port in recent_peer_endpoints(exclude_peer_id=local_peer_id(), limit=limit):
        if send_message(host, port, msg):
            sent += 1

    audit_logger.log(
        "query_shard_dispatched",
        target_id=local_peer_id(),
        target_type="peer",
        details={
            "sent": sent,
            "problem_class": query.get("problem_class"),
            "query_id": query.get("query_id"),
        },
    )
    return sent


def request_specific_shard(*, peer_id: str, query_id: str, shard_id: str) -> bool:
    endpoint = endpoint_for_peer(peer_id)
    if not endpoint:
        return False

    host, port = endpoint
    msg = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="REQUEST_SHARD",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload={
            "query_id": query_id,
            "shard_id": shard_id,
        },
    )
    ok = send_message(host, port, msg)

    audit_logger.log(
        "request_shard_sent",
        target_id=shard_id,
        target_type="shard",
        details={
            "peer_id": peer_id,
            "ok": ok,
            "query_id": query_id,
        },
    )
    return ok


def broadcast_task_offer(
    *,
    offer_payload: dict[str, Any],
    required_capabilities: list[str],
    exclude_host_group_hint_hash: str | None = None,
    limit: int | None = None,
) -> int:
    helpers = get_best_helpers(
        required_capabilities=required_capabilities,
        exclude_peer_id=local_peer_id(),
        limit=limit or 10,
        exclude_host_group_hint_hash=exclude_host_group_hint_hash,
    )

    msg = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="TASK_OFFER",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=offer_payload,
    )

    sent = 0
    for helper in helpers:
        endpoint = endpoint_for_peer(helper.peer_id)
        if not endpoint:
            continue
        host, port = endpoint
        if send_message(host, port, msg):
            sent += 1

    if sent == 0 and bool(policy_engine.get("orchestration.local_loopback_offer_on_no_helpers", True)):
        try:
            from core.order_book import global_order_book

            local_endpoint = endpoint_for_peer(local_peer_id()) or ("127.0.0.1", 49152)
            global_order_book.push(msg, local_endpoint, offer_payload)
            sent += 1
            audit_logger.log(
                "task_offer_loopback_enqueued",
                target_id=offer_payload.get("task_id"),
                target_type="task",
                details={"local_endpoint": f"{local_endpoint[0]}:{int(local_endpoint[1])}"},
            )
        except Exception as exc:
            audit_logger.log(
                "task_offer_loopback_enqueue_failed",
                target_id=offer_payload.get("task_id"),
                target_type="task",
                details={"error": str(exc)},
            )

    audit_logger.log(
        "task_offer_broadcast",
        target_id=offer_payload.get("task_id"),
        target_type="task",
        details={
            "sent": sent,
            "required_capabilities": required_capabilities,
        },
    )
    return sent

# Phase 29: Credit Market DEX Broadcast
def broadcast_credit_offer(offer_payload: dict[str, Any], *, limit: int = 50) -> int:
    msg = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="CREDIT_OFFER",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=offer_payload,
    )

    sent = 0
    # Blast the offer to recent peers so they can cache it in their local order books
    for _peer_id, host, port in recent_peer_endpoints(exclude_peer_id=local_peer_id(), limit=limit):
        if send_message(host, port, msg):
            sent += 1

    audit_logger.log(
        "credit_offer_broadcast",
        target_id=offer_payload.get("offer_id"),
        target_type="dex",
        details={"sent": sent, "amount": offer_payload.get("credits_available")},
    )
    return sent
