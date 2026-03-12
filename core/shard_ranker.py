from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core import policy_engine


def _freshness_score(freshness_ts: str | None) -> float:
    if not freshness_ts:
        return 0.5

    try:
        ts = datetime.fromisoformat(freshness_ts)
        age_days = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0)
    except Exception:
        return 0.5

    # 1.0 fresh, decays toward 0.2
    return max(0.2, 1.0 - (age_days / 180.0))


def _validation_rate(candidate: dict[str, Any]) -> float:
    ok = int(candidate.get("local_validation_count", 0))
    fail = int(candidate.get("local_failure_count", 0))
    total = ok + fail
    if total <= 0:
        return 0.5
    return ok / total


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def rank(candidates: list[dict], task: Any) -> list[dict]:
    if not isinstance(candidates, list):
        return []

    min_trust = float(policy_engine.get("trust.min_trust_to_consider_shard", 0.30))
    ranked: list[dict] = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        trust = float(candidate.get("trust_score", 0.0))
        if trust < min_trust:
            continue

        semantic = float(candidate.get("semantic_match", 0.0))
        env_match = float(candidate.get("environment_match", 0.0))
        quality = float(candidate.get("quality_score", 0.0))
        freshness = _freshness_score(candidate.get("freshness_ts"))
        validation = _validation_rate(candidate)

        risk_flags = set(candidate.get("risk_flags") or [])
        risk_penalty = 0.20 if risk_flags else 0.0

        score = _clamp(
            (0.30 * semantic)
            + (0.25 * env_match)
            + (0.20 * trust)
            + (0.10 * quality)
            + (0.10 * freshness)
            + (0.05 * validation)
            - risk_penalty
        )

        enriched = dict(candidate)
        enriched["freshness_score"] = freshness
        enriched["validation_rate"] = validation
        enriched["score"] = score
        ranked.append(enriched)

    ranked.sort(key=lambda c: c["score"], reverse=True)
    return ranked
