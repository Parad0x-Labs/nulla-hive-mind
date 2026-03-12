from __future__ import annotations

from core.candidate_knowledge_lane import invalidate_candidate, recent_candidates


def invalidate_candidate_output(candidate_id: str, *, reason: str) -> None:
    invalidate_candidate(candidate_id, reason=reason)


def invalidate_stale_candidates() -> int:
    invalidated = 0
    for candidate in recent_candidates(limit=200):
        if candidate.get("invalidated_at"):
            continue
        if candidate.get("expires_at") and candidate["expires_at"] <= candidate["created_at"]:
            invalidate_candidate(candidate["candidate_id"], reason="invalid_expiry_window")
            invalidated += 1
    return invalidated
