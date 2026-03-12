from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelayDecision:
    peer_id: str
    mode: str
    reason: str


def choose_relay_mode(*, direct_reachable: bool, relay_available: bool, peer_id: str) -> RelayDecision:
    if direct_reachable:
        return RelayDecision(peer_id=peer_id, mode="direct", reason="Direct connectivity available.")
    if relay_available:
        return RelayDecision(peer_id=peer_id, mode="relay", reason="Direct connectivity unavailable; relay fallback selected.")
    return RelayDecision(peer_id=peer_id, mode="unreachable", reason="No direct path or relay available.")
