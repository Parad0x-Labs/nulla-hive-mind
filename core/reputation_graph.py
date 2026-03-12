from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core import audit_logger
from storage.db import get_connection


@dataclass
class PeerGraphMetrics:
    peer_id: str
    total_interactions: int
    distinct_counterparties: int
    top_counterparty_peer_id: str | None
    top_counterparty_count: int
    pair_concentration: float
    mean_helpfulness: float
    closed_loop_risk: float


@dataclass
class PairGraphRisk:
    parent_peer_id: str
    helper_peer_id: str
    pair_count_7d: int
    parent_distinct_counterparties_7d: int
    helper_distinct_counterparties_7d: int
    pair_share_of_parent: float
    pair_share_of_helper: float
    risk_score: float
    hard_block: bool
    reasons: list[str]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _window_start(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _record_signal(
    *,
    peer_id: str | None,
    related_peer_id: str | None,
    task_id: str | None,
    signal_type: str,
    severity: float,
    details: dict[str, Any],
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO anti_abuse_signals (
                signal_id, peer_id, related_peer_id, task_id,
                signal_type, severity, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                peer_id,
                related_peer_id,
                task_id,
                signal_type,
                _clamp(severity),
                json.dumps(details, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    audit_logger.log(
        "reputation_graph_signal",
        target_id=task_id or peer_id,
        target_type="task" if task_id else "peer",
        details={
            "signal_type": signal_type,
            "severity": round(_clamp(severity), 4),
            "peer_id": peer_id,
            "related_peer_id": related_peer_id,
        },
    )


def peer_graph_metrics(peer_id: str, *, days: int = 7) -> PeerGraphMetrics:
    since = _window_start(days)

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                parent_peer_id,
                helper_peer_id,
                helpfulness_score
            FROM contribution_ledger
            WHERE created_at >= ?
              AND (parent_peer_id = ? OR helper_peer_id = ?)
              AND outcome IN ('pending', 'released', 'slashed', 'rejected')
            """,
            (since, peer_id, peer_id),
        ).fetchall()
    finally:
        conn.close()

    total = len(rows)
    counterparty_counts: dict[str, int] = {}
    helpfulness_values: list[float] = []

    for row in rows:
        parent = str(row["parent_peer_id"])
        helper = str(row["helper_peer_id"])
        cp = helper if parent == peer_id else parent

        counterparty_counts[cp] = counterparty_counts.get(cp, 0) + 1
        helpfulness_values.append(float(row["helpfulness_score"] or 0.0))

    distinct = len(counterparty_counts)
    top_peer = None
    top_count = 0

    if counterparty_counts:
        top_peer, top_count = max(counterparty_counts.items(), key=lambda kv: kv[1])

    pair_concentration = (top_count / max(1, total)) if total > 0 else 0.0
    mean_helpfulness = (sum(helpfulness_values) / max(1, len(helpfulness_values))) if helpfulness_values else 0.0

    # closed-loop suspicion rises when:
    # - lots of traffic
    # - very few counterparties
    # - one counterparty dominates
    closed_loop_risk = _clamp(
        (0.45 * pair_concentration)
        + (0.35 * (1.0 if distinct <= 2 and total >= 6 else 0.0))
        + (0.20 * (1.0 if distinct <= 1 and total >= 4 else 0.0))
    )

    return PeerGraphMetrics(
        peer_id=peer_id,
        total_interactions=total,
        distinct_counterparties=distinct,
        top_counterparty_peer_id=top_peer,
        top_counterparty_count=top_count,
        pair_concentration=pair_concentration,
        mean_helpfulness=mean_helpfulness,
        closed_loop_risk=closed_loop_risk,
    )


def _pair_count(parent_peer_id: str, helper_peer_id: str, *, days: int = 7) -> int:
    since = _window_start(days)

    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM contribution_ledger
            WHERE created_at >= ?
              AND (
                   (parent_peer_id = ? AND helper_peer_id = ?)
                OR (parent_peer_id = ? AND helper_peer_id = ?)
              )
            """,
            (since, parent_peer_id, helper_peer_id, helper_peer_id, parent_peer_id),
        ).fetchone()
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def pair_graph_risk(parent_peer_id: str, helper_peer_id: str, *, days: int = 7) -> PairGraphRisk:
    pair_count = _pair_count(parent_peer_id, helper_peer_id, days=days)
    parent_metrics = peer_graph_metrics(parent_peer_id, days=days)
    helper_metrics = peer_graph_metrics(helper_peer_id, days=days)

    pair_share_of_parent = pair_count / max(1, parent_metrics.total_interactions)
    pair_share_of_helper = pair_count / max(1, helper_metrics.total_interactions)

    reasons: list[str] = []
    hard_block = False

    if parent_peer_id == helper_peer_id:
        reasons.append("same_peer")
        hard_block = True

    if pair_count >= 14:
        reasons.append("extreme_pair_repetition")
    elif pair_count >= 8:
        reasons.append("high_pair_repetition")
    elif pair_count >= 4:
        reasons.append("moderate_pair_repetition")

    if pair_share_of_parent >= 0.80 and parent_metrics.total_interactions >= 5:
        reasons.append("parent_pair_dominance")

    if pair_share_of_helper >= 0.80 and helper_metrics.total_interactions >= 5:
        reasons.append("helper_pair_dominance")

    if (
        pair_count >= 8
        and parent_metrics.distinct_counterparties <= 2
        and helper_metrics.distinct_counterparties <= 2
    ):
        reasons.append("closed_loop_cluster")
        hard_block = True

    risk_score = _clamp(
        (0.30 * min(1.0, pair_count / 12.0))
        + (0.20 * pair_share_of_parent)
        + (0.20 * pair_share_of_helper)
        + (0.15 * parent_metrics.closed_loop_risk)
        + (0.15 * helper_metrics.closed_loop_risk)
    )

    if hard_block:
        risk_score = max(risk_score, 0.90)

    return PairGraphRisk(
        parent_peer_id=parent_peer_id,
        helper_peer_id=helper_peer_id,
        pair_count_7d=pair_count,
        parent_distinct_counterparties_7d=parent_metrics.distinct_counterparties,
        helper_distinct_counterparties_7d=helper_metrics.distinct_counterparties,
        pair_share_of_parent=pair_share_of_parent,
        pair_share_of_helper=pair_share_of_helper,
        risk_score=risk_score,
        hard_block=hard_block,
        reasons=reasons,
    )


def record_pair_graph_signal(
    *,
    parent_peer_id: str,
    helper_peer_id: str,
    task_id: str | None = None,
    days: int = 7,
) -> PairGraphRisk:
    risk = pair_graph_risk(parent_peer_id, helper_peer_id, days=days)

    if risk.risk_score >= 0.35:
        _record_signal(
            peer_id=helper_peer_id,
            related_peer_id=parent_peer_id,
            task_id=task_id,
            signal_type="pair_graph_risk",
            severity=risk.risk_score,
            details={
                "pair_count_7d": risk.pair_count_7d,
                "pair_share_of_parent": round(risk.pair_share_of_parent, 4),
                "pair_share_of_helper": round(risk.pair_share_of_helper, 4),
                "parent_distinct_counterparties_7d": risk.parent_distinct_counterparties_7d,
                "helper_distinct_counterparties_7d": risk.helper_distinct_counterparties_7d,
                "hard_block": risk.hard_block,
                "reasons": risk.reasons,
            },
        )

    return risk
