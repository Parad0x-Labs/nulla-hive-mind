from __future__ import annotations

from collections import defaultdict, deque
from threading import RLock
from time import time

from core import audit_logger, policy_engine
from network import quarantine

_EVENTS: dict[str, deque[float]] = defaultdict(deque)
_BREACHES: dict[str, int] = defaultdict(int)
_LOCK = RLock()
_WINDOW_SECONDS = 60.0


def allow(peer_id: str) -> bool:
    """
    Sliding-window rate limiter.
    Returns True if the peer is allowed to continue, False if throttled.
    """
    if quarantine.is_peer_quarantined(peer_id):
        return False

    limit = int(policy_engine.get("network.max_requests_per_minute_per_peer", 30))
    strike_limit = int(policy_engine.get("network.max_failed_messages_before_quarantine", 3))
    now = time()

    with _LOCK:
        dq = _EVENTS[peer_id]

        while dq and (now - dq[0]) > _WINDOW_SECONDS:
            dq.popleft()

        if len(dq) >= limit:
            _BREACHES[peer_id] += 1
            audit_logger.log(
                "rate_limit_exceeded",
                target_id=peer_id,
                target_type="peer",
                details={"breaches": _BREACHES[peer_id], "limit": limit},
            )

            if _BREACHES[peer_id] >= strike_limit:
                quarantine.quarantine_peer(peer_id, "rate_limit_abuse")

            return False

        dq.append(now)
        return True


def reset_peer(peer_id: str) -> None:
    with _LOCK:
        _EVENTS.pop(peer_id, None)
        _BREACHES.pop(peer_id, None)
