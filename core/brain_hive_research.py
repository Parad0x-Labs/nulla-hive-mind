from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urlsplit

from core.brain_hive_artifacts import count_artifact_manifests, list_artifact_manifests


def build_topic_research_packet(
    *,
    topic: Any,
    posts: list[Any],
    claims: list[Any],
) -> dict[str, Any]:
    topic_row = _as_dict(topic)
    post_rows = [_as_dict(item) for item in list(posts or [])]
    claim_rows = [_as_dict(item) for item in list(claims or [])]
    evidence_kind_counts: Counter[str] = Counter()
    post_kind_counts: Counter[str] = Counter()
    source_domains: Counter[str] = Counter()
    evidence_count = 0
    for post in post_rows:
        post_kind_counts[str(post.get("post_kind") or "analysis")] += 1
        for ref in list(post.get("evidence_refs") or []):
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or ref.get("type") or "reference").strip() or "reference"
            evidence_kind_counts[kind] += 1
            evidence_count += 1
            for domain in _ref_domains(ref):
                source_domains[domain] += 1

    trading_feature_export = _extract_trading_feature_export(topic_row=topic_row, post_rows=post_rows)
    active_claims = [
        claim
        for claim in claim_rows
        if str(claim.get("status") or "").strip().lower() == "active"
    ]
    artifacts = list_artifact_manifests(topic_id=str(topic_row.get("topic_id") or ""), limit=8)
    packet = {
        "packet_schema": "brain_hive.research_packet.v1",
        "topic": {
            "topic_id": str(topic_row.get("topic_id") or ""),
            "title": str(topic_row.get("title") or "Untitled topic"),
            "summary": str(topic_row.get("summary") or ""),
            "status": str(topic_row.get("status") or "open"),
            "visibility": str(topic_row.get("visibility") or ""),
            "evidence_mode": str(topic_row.get("evidence_mode") or ""),
            "linked_task_id": str(topic_row.get("linked_task_id") or ""),
            "topic_tags": [str(item) for item in list(topic_row.get("topic_tags") or []) if str(item).strip()][:16],
            "created_at": str(topic_row.get("created_at") or ""),
            "updated_at": str(topic_row.get("updated_at") or ""),
            "creator": {
                "agent_id": str(topic_row.get("created_by_agent_id") or ""),
                "display_name": str(topic_row.get("creator_display_name") or ""),
                "claim_label": str(topic_row.get("creator_claim_label") or ""),
            },
        },
        "execution_state": {
            "topic_status": str(topic_row.get("status") or "open"),
            "claim_count": len(claim_rows),
            "active_claim_count": len(active_claims),
            "execution_state": _execution_state(topic_row=topic_row, claim_rows=claim_rows),
            "artifact_count": count_artifact_manifests(topic_id=str(topic_row.get("topic_id") or "")),
        },
        "counts": {
            "post_count": len(post_rows),
            "claim_count": len(claim_rows),
            "active_claim_count": len(active_claims),
            "evidence_count": evidence_count,
            "source_domain_count": len(source_domains),
        },
        "post_kind_counts": [
            {"kind": kind, "count": int(count)}
            for kind, count in post_kind_counts.most_common(8)
        ],
        "evidence_kind_counts": [
            {"kind": kind, "count": int(count)}
            for kind, count in evidence_kind_counts.most_common(12)
        ],
        "source_domains": [
            {"domain": domain, "count": int(count)}
            for domain, count in source_domains.most_common(16)
        ],
        "claims": [
            {
                "claim_id": str(claim.get("claim_id") or ""),
                "agent_id": str(claim.get("agent_id") or ""),
                "agent_label": str(claim.get("agent_claim_label") or claim.get("agent_display_name") or ""),
                "status": str(claim.get("status") or ""),
                "note": str(claim.get("note") or ""),
                "capability_tags": [str(item) for item in list(claim.get("capability_tags") or []) if str(item).strip()][:12],
                "updated_at": str(claim.get("updated_at") or ""),
            }
            for claim in claim_rows
        ],
        "posts": [
            {
                "post_id": str(post.get("post_id") or ""),
                "post_kind": str(post.get("post_kind") or "analysis"),
                "stance": str(post.get("stance") or ""),
                "author_agent_id": str(post.get("author_agent_id") or ""),
                "author_label": str(post.get("author_claim_label") or post.get("author_display_name") or ""),
                "body": str(post.get("body") or ""),
                "created_at": str(post.get("created_at") or ""),
                "evidence_refs": [dict(ref) for ref in list(post.get("evidence_refs") or []) if isinstance(ref, dict)],
            }
            for post in post_rows
        ],
        "event_flow": _build_topic_event_flow(topic_row=topic_row, post_rows=post_rows, claim_rows=claim_rows),
        "derived_research_questions": derive_research_questions(
            topic_row=topic_row,
            claim_rows=claim_rows,
            evidence_kind_counts=evidence_kind_counts,
            trading_feature_export=trading_feature_export,
        ),
        "trading_feature_export": trading_feature_export,
        "artifacts": [
            {
                "artifact_id": str(item.get("artifact_id") or ""),
                "source_kind": str(item.get("source_kind") or ""),
                "title": str(item.get("title") or ""),
                "summary": str(item.get("summary") or ""),
                "tags": [str(tag) for tag in list(item.get("tags") or []) if str(tag).strip()][:12],
                "created_at": str(item.get("created_at") or ""),
                "storage_backend": str(item.get("storage_backend") or ""),
                "file_path": str(item.get("file_path") or ""),
            }
            for item in artifacts
        ],
    }
    return packet


def build_research_queue_entry(
    *,
    topic: Any,
    posts: list[Any],
    claims: list[Any],
    commons_signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    packet = build_topic_research_packet(topic=topic, posts=posts, claims=claims)
    topic_row = dict(packet["topic"])
    trading_export = dict(packet.get("trading_feature_export") or {})
    priority, steering_reasons, commons_signal_strength = _research_priority(packet, commons_signal=commons_signal)
    return {
        "topic_id": str(topic_row.get("topic_id") or ""),
        "title": str(topic_row.get("title") or ""),
        "summary": str(topic_row.get("summary") or ""),
        "status": str(topic_row.get("status") or "open"),
        "topic_tags": [str(item) for item in list(topic_row.get("topic_tags") or []) if str(item).strip()][:16],
        "linked_task_id": str(topic_row.get("linked_task_id") or ""),
        "post_count": int(dict(packet.get("counts") or {}).get("post_count") or 0),
        "claim_count": int(dict(packet.get("counts") or {}).get("claim_count") or 0),
        "active_claim_count": int(dict(packet.get("counts") or {}).get("active_claim_count") or 0),
        "evidence_count": int(dict(packet.get("counts") or {}).get("evidence_count") or 0),
        "artifact_count": int(dict(packet.get("execution_state") or {}).get("artifact_count") or 0),
        "execution_state": str(dict(packet.get("execution_state") or {}).get("execution_state") or "open"),
        "research_priority": priority,
        "commons_signal_strength": commons_signal_strength,
        "steering_reasons": steering_reasons[:8],
        "commons_signal": dict(commons_signal or {}),
        "suggested_questions": list(packet.get("derived_research_questions") or [])[:4],
        "trading_signal_count": int(
            len(list(trading_export.get("heuristic_seed_signals") or []))
            + len(list(trading_export.get("hidden_edges") or []))
            + len(list(trading_export.get("discoveries") or []))
        ),
        "packet_schema": str(packet.get("packet_schema") or ""),
    }


def derive_research_questions(
    *,
    topic_row: dict[str, Any],
    claim_rows: list[dict[str, Any]],
    evidence_kind_counts: Counter[str],
    trading_feature_export: dict[str, Any],
) -> list[str]:
    title = str(topic_row.get("title") or "this topic").strip()
    summary = str(topic_row.get("summary") or "").strip()
    tags = [str(item).strip().lower() for item in list(topic_row.get("topic_tags") or []) if str(item).strip()]
    questions: list[str] = [
        f"Best way to research topic: {title}",
        f"What evidence is still missing or weak for {title}?",
    ]
    if summary:
        questions.append(f"How should agents split research and scripting work for {title} based on: {summary[:180]}?")
    if not any(str(claim.get("status") or "").strip().lower() == "active" for claim in claim_rows):
        questions.append(f"What is the fastest credible first-pass research plan for {title}?")
    if "script" in " ".join(tags) or "automation" in " ".join(tags):
        questions.append(f"What validation or benchmark gate should block weak scripts for {title}?")
    if trading_feature_export:
        questions.append(f"Which exported trading features best explain misses or hidden edges in {title}?")
        for signal in list(trading_feature_export.get("heuristic_seed_signals") or [])[:2]:
            label = str(signal.get("label") or signal.get("metric") or signal.get("reason") or "").strip()
            if label:
                questions.append(f"How should {label} be researched or backtested before promotion?")
    deduped: list[str] = []
    seen: set[str] = set()
    for question in questions:
        clean = " ".join(str(question or "").split()).strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(clean[:240])
    return deduped[:6]


def _research_priority(packet: dict[str, Any], *, commons_signal: dict[str, Any] | None = None) -> tuple[float, list[str], float]:
    topic = dict(packet.get("topic") or {})
    state = dict(packet.get("execution_state") or {})
    counts = dict(packet.get("counts") or {})
    tags = {str(item).strip().lower() for item in list(topic.get("topic_tags") or []) if str(item).strip()}
    priority = 0.20
    steering_reasons: list[str] = []
    status = str(topic.get("status") or "open").strip().lower()
    if status == "open":
        priority += 0.26
        steering_reasons.append("topic_open")
    elif status == "researching":
        priority += 0.18
        steering_reasons.append("topic_researching")
    elif status == "disputed":
        priority += 0.12
        steering_reasons.append("topic_disputed")
    if int(state.get("active_claim_count") or 0) == 0:
        priority += 0.24
        steering_reasons.append("no_active_claims")
    if int(counts.get("evidence_count") or 0) <= 2:
        priority += 0.10
        steering_reasons.append("thin_evidence")
    if {"trading_learning", "manual_trader", "agent_commons"} & tags:
        priority += 0.08
        steering_reasons.append("priority_lane")
    if dict(packet.get("trading_feature_export") or {}):
        priority += 0.08
        steering_reasons.append("trading_signal_export")
    if int(state.get("artifact_count") or 0) == 0:
        priority += 0.04
        steering_reasons.append("no_artifacts")

    commons_signal_strength = _commons_signal_strength(commons_signal or {})
    if commons_signal_strength > 0.0:
        priority += min(0.22, commons_signal_strength * 0.22)
        steering_reasons.extend(
            reason
            for reason in list((commons_signal or {}).get("reasons") or [])
            if isinstance(reason, str) and reason.strip()
        )

    deduped_reasons: list[str] = []
    seen: set[str] = set()
    for reason in steering_reasons:
        clean = str(reason or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped_reasons.append(clean)
    return round(max(0.0, min(1.0, priority)), 4), deduped_reasons, commons_signal_strength


def _commons_signal_strength(commons_signal: dict[str, Any]) -> float:
    if not commons_signal:
        return 0.0
    candidate_count = max(0, int(commons_signal.get("candidate_count") or 0))
    review_required_count = max(0, int(commons_signal.get("review_required_count") or 0))
    approved_count = max(0, int(commons_signal.get("approved_count") or 0))
    promoted_count = max(0, int(commons_signal.get("promoted_count") or 0))
    top_score = max(0.0, float(commons_signal.get("top_score") or 0.0))
    support_weight = max(0.0, float(commons_signal.get("support_weight") or 0.0))
    challenge_weight = max(0.0, float(commons_signal.get("challenge_weight") or 0.0))
    training_signal_count = max(0, int(commons_signal.get("training_signal_count") or 0))
    downstream_use_count = max(0, int(commons_signal.get("downstream_use_count") or 0))

    strength = 0.0
    strength += min(0.18, candidate_count * 0.04)
    strength += min(0.20, review_required_count * 0.08)
    strength += min(0.24, approved_count * 0.10)
    strength += min(0.24, promoted_count * 0.12)
    strength += min(0.18, top_score * 0.04)
    strength += min(0.10, max(0.0, support_weight - challenge_weight) * 0.04)
    strength += min(0.08, training_signal_count * 0.02)
    strength += min(0.08, downstream_use_count * 0.02)
    strength -= min(0.18, max(0.0, challenge_weight - support_weight) * 0.05)
    return round(max(0.0, min(1.0, strength)), 4)


def _execution_state(*, topic_row: dict[str, Any], claim_rows: list[dict[str, Any]]) -> str:
    status = str(topic_row.get("status") or "open").strip().lower()
    if status in {"solved", "closed"}:
        return status
    active_claims = [
        claim
        for claim in claim_rows
        if str(claim.get("status") or "").strip().lower() == "active"
    ]
    if active_claims:
        return "claimed"
    if status == "researching":
        return "idle_researching"
    if status == "disputed":
        return "disputed"
    return "open"


def _build_topic_event_flow(
    *,
    topic_row: dict[str, Any],
    post_rows: list[dict[str, Any]],
    claim_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "event_type": "topic_created",
            "timestamp": str(topic_row.get("created_at") or ""),
            "detail": str(topic_row.get("summary") or ""),
            "status": str(topic_row.get("status") or "open"),
        }
    ]
    for claim in claim_rows:
        events.append(
            {
                "event_type": f"claim_{str(claim.get('status') or 'active').strip().lower()}",
                "timestamp": str(claim.get("updated_at") or claim.get("created_at") or ""),
                "detail": str(claim.get("note") or ""),
                "claim_id": str(claim.get("claim_id") or ""),
                "agent_label": str(claim.get("agent_claim_label") or claim.get("agent_display_name") or claim.get("agent_id") or ""),
            }
        )
    for post in post_rows:
        meta = _task_event_meta(post)
        events.append(
            {
                "event_type": str(meta.get("event_type") or f"post_{str(post.get('post_kind') or 'analysis')}"),
                "timestamp": str(post.get("created_at") or ""),
                "detail": str(post.get("body") or ""),
                "post_kind": str(post.get("post_kind") or "analysis"),
                "progress_state": str(meta.get("progress_state") or ""),
                "claim_id": str(meta.get("claim_id") or ""),
            }
        )
    return sorted(events, key=lambda row: str(row.get("timestamp") or ""), reverse=True)[:32]


def _task_event_meta(post: dict[str, Any]) -> dict[str, Any]:
    for ref in list(post.get("evidence_refs") or []):
        if isinstance(ref, dict) and str(ref.get("kind") or "").strip().lower() == "task_event":
            return dict(ref)
    return {}


def _extract_trading_feature_export(*, topic_row: dict[str, Any], post_rows: list[dict[str, Any]]) -> dict[str, Any]:
    tags = {str(item).strip().lower() for item in list(topic_row.get("topic_tags") or []) if str(item).strip()}
    title = str(topic_row.get("title") or "").lower()
    summary = str(topic_row.get("summary") or "").lower()
    if "trading_learning" not in tags and "manual_trader" not in tags and "trading" not in f"{title} {summary}":
        return {}

    latest_summary: dict[str, Any] = {}
    latest_heartbeat: dict[str, Any] = {}
    lab_summary: dict[str, Any] = {}
    decision_funnel: dict[str, Any] = {}
    pattern_health: dict[str, Any] = {}
    calls_by_key: dict[str, dict[str, Any]] = {}
    missed_by_key: dict[str, dict[str, Any]] = {}
    edges_by_key: dict[str, dict[str, Any]] = {}
    discoveries_by_key: dict[str, dict[str, Any]] = {}
    flow_rows: list[dict[str, Any]] = []
    lessons: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []

    for post in post_rows:
        for ref in list(post.get("evidence_refs") or []):
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or "").strip().lower()
            if kind == "trading_learning_summary" and isinstance(ref.get("summary"), dict):
                latest_summary = dict(ref.get("summary") or {})
            elif kind == "trading_runtime_heartbeat" and isinstance(ref.get("heartbeat"), dict):
                latest_heartbeat = dict(ref.get("heartbeat") or {})
            elif kind == "trading_learning_lab_summary" and isinstance(ref.get("summary"), dict):
                lab_summary = dict(ref.get("summary") or {})
            elif kind == "trading_decision_funnel" and isinstance(ref.get("summary"), dict):
                decision_funnel = dict(ref.get("summary") or {})
            elif kind == "trading_pattern_health" and isinstance(ref.get("summary"), dict):
                pattern_health = dict(ref.get("summary") or {})
            elif kind == "trading_calls":
                for item in list(ref.get("items") or []):
                    if isinstance(item, dict):
                        key = str(item.get("token_mint") or item.get("call_id") or "").strip()
                        if key:
                            calls_by_key[key] = _merge_sparse(calls_by_key.get(key), item)
            elif kind == "trading_missed_mooners":
                for item in list(ref.get("items") or []):
                    if isinstance(item, dict):
                        key = str(item.get("id") or item.get("token_mint") or "").strip()
                        if key:
                            missed_by_key[key] = _merge_sparse(missed_by_key.get(key), item)
            elif kind == "trading_hidden_edges":
                for item in list(ref.get("items") or []):
                    if isinstance(item, dict):
                        key = str(item.get("id") or item.get("metric") or "").strip()
                        if key:
                            edges_by_key[key] = _merge_sparse(edges_by_key.get(key), item)
            elif kind == "trading_discoveries":
                for item in list(ref.get("items") or []):
                    if isinstance(item, dict):
                        key = str(item.get("id") or item.get("discovery") or "").strip()
                        if key:
                            discoveries_by_key[key] = _merge_sparse(discoveries_by_key.get(key), item)
            elif kind == "trading_live_flow":
                flow_rows.extend([dict(item) for item in list(ref.get("items") or []) if isinstance(item, dict)])
            elif kind == "trading_lessons":
                lessons.extend([dict(item) for item in list(ref.get("items") or []) if isinstance(item, dict)])
            elif kind == "trading_ath_updates":
                updates.extend([dict(item) for item in list(ref.get("items") or []) if isinstance(item, dict)])

    hidden_edges = sorted(edges_by_key.values(), key=lambda row: float(row.get("score", 0.0) or 0.0), reverse=True)[:20]
    discoveries = sorted(discoveries_by_key.values(), key=lambda row: float(row.get("ts", 0.0) or 0.0), reverse=True)[:20]
    flow = sorted(flow_rows, key=lambda row: float(row.get("ts", 0.0) or 0.0), reverse=True)[:30]
    flow_reason_counts: Counter[str] = Counter()
    for item in flow:
        reason = str(item.get("detail") or item.get("kind") or "unknown").strip()
        if reason:
            flow_reason_counts[reason] += 1
    heuristic_seed_signals = []
    for edge in hidden_edges[:6]:
        metric = str(edge.get("metric") or edge.get("id") or "").strip()
        heuristic_seed_signals.append(
            {
                "kind": "hidden_edge",
                "metric": metric,
                "label": metric or "hidden_edge",
                "score": float(edge.get("score", 0.0) or 0.0),
                "support": int(edge.get("support", 0) or 0),
            }
        )
    for reason, count in flow_reason_counts.most_common(4):
        heuristic_seed_signals.append(
            {
                "kind": "flow_reason",
                "reason": reason,
                "label": reason,
                "count": int(count),
            }
        )
    return {
        "latest_summary": latest_summary,
        "latest_heartbeat": latest_heartbeat,
        "lab_summary": lab_summary,
        "decision_funnel": decision_funnel,
        "pattern_health": pattern_health,
        "calls": sorted(calls_by_key.values(), key=lambda row: float(row.get("call_ts", 0.0) or 0.0), reverse=True)[:40],
        "missed_mooners": sorted(missed_by_key.values(), key=lambda row: float(row.get("ts", 0.0) or 0.0), reverse=True)[:20],
        "hidden_edges": hidden_edges,
        "discoveries": discoveries,
        "flow": flow,
        "lessons": lessons[:12],
        "updates": updates[:12],
        "flow_reason_counts": [{"reason": reason, "count": int(count)} for reason, count in flow_reason_counts.most_common(8)],
        "heuristic_seed_signals": heuristic_seed_signals[:10],
    }


def _merge_sparse(current: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current or {})
    for key, value in incoming.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _ref_domains(ref: dict[str, Any]) -> list[str]:
    domains: list[str] = []
    for key in ("url", "href", "value", "source_url"):
        raw = str(ref.get(key) or "").strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            domain = str(urlsplit(raw).netloc or "").strip().lower()
            if domain:
                domains.append(domain)
    return list(dict.fromkeys(domains))


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json"))
    return dict(value)
