from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core import audit_logger
from core.brain_hive_artifacts import store_artifact_manifest
from core.candidate_knowledge_lane import get_candidate_by_id
from core.curiosity_roamer import CuriosityRoamer
from core.hive_activity_tracker import HiveActivityTracker
from core.public_hive_bridge import PublicHiveBridge
from core.research_promotion_gate import evaluate_research_promotion_candidate
from core.runtime_continuity import append_runtime_event
from core.trading_feature_miner import mine_exported_trading_features
from network.signer import get_local_peer_id


@dataclass
class AutonomousResearchResult:
    ok: bool
    status: str
    topic_id: str = ""
    claim_id: str = ""
    result_status: str = ""
    artifact_ids: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    promotion_decisions: list[dict[str, Any]] = field(default_factory=list)
    response_text: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "topic_id": self.topic_id,
            "claim_id": self.claim_id,
            "result_status": self.result_status,
            "artifact_ids": list(self.artifact_ids),
            "candidate_ids": list(self.candidate_ids),
            "promotion_decisions": [dict(item) for item in self.promotion_decisions],
            "response_text": self.response_text,
            "details": dict(self.details),
        }


def research_topic_from_signal(
    signal: dict[str, Any] | str,
    *,
    public_hive_bridge: PublicHiveBridge,
    curiosity: CuriosityRoamer | None = None,
    hive_activity_tracker: HiveActivityTracker | None = None,
    session_id: str | None = None,
    auto_claim: bool = True,
) -> AutonomousResearchResult:
    if not public_hive_bridge.enabled():
        return AutonomousResearchResult(
            ok=False,
            status="disabled",
            response_text="Public Hive bridge is disabled on this runtime.",
        )
    signal_row = dict(signal) if isinstance(signal, dict) else {"topic_id": str(signal or "").strip()}
    topic_id = str(signal_row.get("topic_id") or "").strip()
    if not topic_id:
        return AutonomousResearchResult(
            ok=False,
            status="missing_topic_id",
            response_text="Autonomous research needs a concrete topic_id.",
        )
    event_session = str(session_id or f"auto-research:{topic_id}")
    _event(
        event_session,
        "task_received",
        f"Autonomous research queued for topic {topic_id}.",
        topic_id=topic_id,
        task_class="autonomous_research",
    )

    packet = public_hive_bridge.get_public_research_packet(topic_id)
    if not packet:
        return AutonomousResearchResult(
            ok=False,
            status="missing_packet",
            topic_id=topic_id,
            response_text=f"Research packet for topic `{topic_id}` is unavailable.",
        )
    topic = dict(packet.get("topic") or {})
    claims = [dict(item) for item in list(packet.get("claims") or [])]
    title = str(topic.get("title") or topic_id)
    _event(
        event_session,
        "task_classified",
        f"Loaded machine-readable research packet for {title}.",
        topic_id=topic_id,
        request_preview=title,
        execution_state=str(dict(packet.get("execution_state") or {}).get("execution_state") or ""),
    )

    local_peer_id = get_local_peer_id()
    active_claims = [
        claim
        for claim in claims
        if str(claim.get("status") or "").strip().lower() == "active"
    ]
    foreign_active = [claim for claim in active_claims if str(claim.get("agent_id") or "").strip() != local_peer_id]

    claim_id = ""
    our_claim = next(
        (
            claim
            for claim in active_claims
            if str(claim.get("agent_id") or "").strip() == local_peer_id
        ),
        None,
    )
    if our_claim:
        claim_id = str(our_claim.get("claim_id") or "")
    elif auto_claim:
        claim_result = public_hive_bridge.claim_public_topic(
            topic_id=topic_id,
            note=(
                "Autonomous research lane claimed this topic for packet export, external research, and heuristic mining."
                if not foreign_active
                else "Parallel autonomous research lane joined this topic for replication, independent evidence, and heuristic mining."
            ),
            capability_tags=_claim_tags(topic),
            idempotency_key=_idempotency_key("auto-claim", topic_id),
        )
        if not claim_result.get("ok"):
            return AutonomousResearchResult(
                ok=False,
                status=str(claim_result.get("status") or "claim_failed"),
                topic_id=topic_id,
                response_text=f"Failed to claim topic `{topic_id}` for autonomous research.",
                details=dict(claim_result),
            )
        claim_id = str(claim_result.get("claim_id") or "")
        _event(
            event_session,
            "tool_executed",
            f"Claimed topic {topic_id} for autonomous research.",
            topic_id=topic_id,
            topic_title=title,
            claim_id=claim_id,
            tool_name="hive.claim_task",
        )
        if hive_activity_tracker is not None:
            try:
                hive_activity_tracker.note_watched_topic(session_id=event_session, topic_id=topic_id)
            except Exception:
                pass

    public_hive_bridge.post_public_topic_progress(
        topic_id=topic_id,
        body=(
            (
                f"Parallel autonomous research started for {title}. "
                "Another agent already has an active claim, so this lane is adding replication, independent evidence, and bounded external research."
            )
            if foreign_active
            else (
                f"Autonomous research started for {title}. "
                "Building a machine-readable packet, mining exported signals, and running bounded external research."
            )
        ),
        progress_state="started",
        claim_id=claim_id or None,
        evidence_refs=[
            {
                "kind": "autonomous_research_state",
                "state": "started",
                "topic_id": topic_id,
                "parallel_lane": bool(foreign_active),
            }
        ],
        idempotency_key=_idempotency_key("auto-progress-start", topic_id),
    )

    packet_artifact = store_artifact_manifest(
        source_kind="hive_topic_packet",
        title=f"Research packet: {title}",
        summary=f"Machine-readable research packet for {title}.",
        payload=packet,
        topic_id=topic_id,
        claim_id=claim_id or None,
        session_id=event_session,
        tags=list(topic.get("topic_tags") or []) + ["research_packet"],
        metadata={"packet_schema": packet.get("packet_schema"), "topic_status": topic.get("status")},
    )
    _event(
        event_session,
        "tool_executed",
        f"Packed research packet artifact {packet_artifact['artifact_id']}.",
        topic_id=topic_id,
        topic_title=title,
        artifact_id=packet_artifact["artifact_id"],
        artifact_role="packet",
        tool_name="liquefy.pack_research_packet",
    )

    miner_output = mine_exported_trading_features(packet)
    query_results: list[dict[str, Any]] = []
    candidate_ids: list[str] = []
    roamer = curiosity or CuriosityRoamer()
    derived_queries = list(packet.get("derived_research_questions") or [])[:4]
    query_total = len(derived_queries)
    for query_index, query in enumerate(derived_queries, start=1):
        topic_kind = _research_topic_kind(topic, packet)
        _event(
            event_session,
            "tool_started",
            f"Running bounded research {query_index}/{query_total}: {query}",
            topic_id=topic_id,
            topic_title=title,
            query=query,
            query_index=query_index,
            query_total=query_total,
            tool_name="curiosity.run_external_topic",
        )
        result = roamer.run_external_topic(
            session_id=event_session,
            topic_text=str(query),
            topic_kind=topic_kind,
            reason=f"research_topic_from_signal:{topic_id}",
            task_id=f"research:{topic_id}",
            trace_id=f"research:{topic_id}",
        )
        candidate_id = str(result.get("candidate_id") or "")
        if candidate_id:
            candidate_ids.append(candidate_id)
        query_results.append(_query_result_preview(query=str(query), result=result))
        _event(
            event_session,
            "tool_executed",
            f"Finished bounded research {query_index}/{query_total}: {query}",
            topic_id=topic_id,
            topic_title=title,
            query=query,
            query_index=query_index,
            query_total=query_total,
            candidate_id=candidate_id,
            tool_name="curiosity.run_external_topic",
        )

    promotion_decisions: list[dict[str, Any]] = []
    for candidate in list(miner_output.get("heuristic_candidates") or []) + list(miner_output.get("script_ideas") or []):
        gate = evaluate_research_promotion_candidate(candidate, research_packet=packet)
        promotion_decisions.append(
            {
                **dict(candidate),
                "gate": gate.to_dict(),
            }
        )

    bundle_payload = {
        "bundle_schema": "brain_hive.autonomous_research_bundle.v1",
        "topic_id": topic_id,
        "title": title,
        "packet_artifact_id": packet_artifact["artifact_id"],
        "query_results": query_results,
        "candidate_ids": candidate_ids,
        "mined_features": miner_output,
        "promotion_decisions": promotion_decisions,
    }
    bundle_artifact = store_artifact_manifest(
        source_kind="research_bundle",
        title=f"Autonomous research bundle: {title}",
        summary=f"Bounded external research, trading feature mining, and gate decisions for {title}.",
        payload=bundle_payload,
        topic_id=topic_id,
        claim_id=claim_id or None,
        session_id=event_session,
        tags=list(topic.get("topic_tags") or []) + ["autonomous_research", "research_bundle"],
        metadata={
            "query_count": len(query_results),
            "candidate_count": len(candidate_ids),
            "promotable_count": sum(1 for item in promotion_decisions if dict(item.get("gate") or {}).get("can_promote")),
        },
    )
    _event(
        event_session,
        "tool_executed",
        f"Packed research bundle artifact {bundle_artifact['artifact_id']}.",
        topic_id=topic_id,
        topic_title=title,
        claim_id=claim_id,
        artifact_id=bundle_artifact["artifact_id"],
        artifact_role="bundle",
        tool_name="liquefy.pack_research_bundle",
    )

    promotable = [item for item in promotion_decisions if dict(item.get("gate") or {}).get("can_promote")]
    result_status = "solved" if promotable and len(query_results) >= 2 else "researching"
    result_body = _render_result_body(
        title=title,
        packet_artifact=packet_artifact,
        bundle_artifact=bundle_artifact,
        query_results=query_results,
        promotion_decisions=promotion_decisions,
    )
    post_result = public_hive_bridge.submit_public_topic_result(
        topic_id=topic_id,
        body=result_body,
        result_status=result_status,
        claim_id=claim_id or None,
        evidence_refs=[
            _artifact_ref(packet_artifact, kind="research_packet_artifact"),
            _artifact_ref(bundle_artifact, kind="research_bundle_artifact"),
            *[_candidate_ref(candidate_id) for candidate_id in candidate_ids[:8]],
            *[_promotion_ref(item) for item in promotion_decisions[:8]],
        ],
        idempotency_key=_idempotency_key("auto-result", f"{topic_id}:{bundle_artifact['artifact_id']}"),
    )
    if post_result.get("ok"):
        _event(
            event_session,
            "tool_executed",
            f"Submitted Hive result with status {result_status}.",
            topic_id=topic_id,
            topic_title=title,
            claim_id=claim_id,
            result_status=result_status,
            artifact_id=bundle_artifact["artifact_id"],
            artifact_role="bundle",
            post_id=str(post_result.get("post_id") or ""),
            tool_name="hive.submit_result",
        )
    _event(
        event_session,
        "task_completed",
        f"Autonomous research finished for {title} with status {result_status}.",
        topic_id=topic_id,
        topic_title=title,
        claim_id=claim_id,
        result_status=result_status,
        artifact_id=bundle_artifact["artifact_id"],
        artifact_role="bundle",
        query_count=len(query_results),
        artifact_count=2,
        candidate_count=len(candidate_ids),
    )

    response_text = (
        f"Autonomous research on `{topic_id}` packed {len(query_results)} research queries, "
        f"{len(candidate_ids)} candidate notes, and {len(promotion_decisions)} gate decisions."
    )
    return AutonomousResearchResult(
        ok=bool(post_result.get("ok")),
        status=(
            "completed_parallel"
            if post_result.get("ok") and foreign_active
            else "completed"
            if post_result.get("ok")
            else str(post_result.get("status") or "result_failed")
        ),
        topic_id=topic_id,
        claim_id=claim_id,
        result_status=result_status,
        artifact_ids=[packet_artifact["artifact_id"], bundle_artifact["artifact_id"]],
        candidate_ids=candidate_ids,
        promotion_decisions=promotion_decisions,
        response_text=response_text,
        details={
            "packet_artifact": packet_artifact,
            "bundle_artifact": bundle_artifact,
            "query_results": query_results,
            "post_result": dict(post_result),
            "foreign_active_claims": foreign_active,
            "parallel_lane": bool(foreign_active),
        },
    )


def pick_autonomous_research_signal(
    queue_rows: list[dict[str, Any]],
    *,
    local_peer_id: str | None = None,
) -> dict[str, Any] | None:
    clean_local_peer_id = str(local_peer_id or get_local_peer_id()).strip()
    candidates: list[dict[str, Any]] = []
    for row in list(queue_rows or []):
        if str(row.get("status") or "").strip().lower() in {"solved", "closed"}:
            continue
        candidates.append(dict(row))
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            float(row.get("research_priority") or 0.0),
            0
            if not any(
                str(item.get("agent_id") or "").strip() != clean_local_peer_id
                for item in list(row.get("claims") or [])
                if str(item.get("status") or "").strip().lower() == "active"
            )
            else -1,
            0 if int(row.get("artifact_count") or 0) <= 0 else -1,
            -int(row.get("active_claim_count") or 0),
            str(row.get("updated_at") or ""),
        ),
        reverse=True,
    )
    return candidates[0]


def _query_result_preview(*, query: str, result: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(result.get("candidate_id") or "")
    candidate = get_candidate_by_id(candidate_id) if candidate_id else None
    summary = str(result.get("summary") or "").strip()
    if not summary and candidate:
        summary = str(candidate.get("normalized_output") or candidate.get("raw_output") or "").strip()
    return {
        "query": str(query),
        "topic_id": str(result.get("topic_id") or ""),
        "candidate_id": candidate_id,
        "cached": bool(result.get("cached")),
        "summary": summary[:1200],
        "snippet_count": len(list(result.get("snippets") or [])),
    }


def _render_result_body(
    *,
    title: str,
    packet_artifact: dict[str, Any],
    bundle_artifact: dict[str, Any],
    query_results: list[dict[str, Any]],
    promotion_decisions: list[dict[str, Any]],
) -> str:
    promotable = [item for item in promotion_decisions if dict(item.get("gate") or {}).get("can_promote")]
    blocked = [item for item in promotion_decisions if not dict(item.get("gate") or {}).get("can_promote")]
    lines = [
        f"Autonomous research bundle for {title}.",
        f"Packet artifact: {packet_artifact['artifact_id']}.",
        f"Bundle artifact: {bundle_artifact['artifact_id']}.",
        f"External research runs: {len(query_results)}.",
        f"Promotion gate: {len(promotable)} promotable, {len(blocked)} held candidate-only.",
    ]
    for item in query_results[:2]:
        clean_summary = " ".join(str(item.get("summary") or "").split()).strip()
        if clean_summary:
            lines.append(f"Research note: {clean_summary[:260]}.")
    for item in blocked[:2]:
        label = str(item.get("label") or item.get("candidate_id") or "").strip()
        missing = ", ".join(str(part) for part in list(dict(item.get("gate") or {}).get("missing_requirements") or [])[:3])
        if label:
            lines.append(f"Held back `{label}` until {missing or 'evaluation evidence'} exists.")
    return " ".join(part.strip() for part in lines if part.strip())[:3500]


def _artifact_ref(artifact: dict[str, Any], *, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "artifact_id": str(artifact.get("artifact_id") or ""),
        "storage_backend": str(artifact.get("storage_backend") or ""),
        "content_sha256": str(artifact.get("content_sha256") or ""),
        "file_path": str(artifact.get("file_path") or ""),
    }


def _candidate_ref(candidate_id: str) -> dict[str, Any]:
    return {
        "kind": "candidate_reference",
        "candidate_id": str(candidate_id or "").strip(),
    }


def _promotion_ref(item: dict[str, Any]) -> dict[str, Any]:
    gate = dict(item.get("gate") or {})
    return {
        "kind": "promotion_gate_decision",
        "candidate_id": str(item.get("candidate_id") or ""),
        "candidate_kind": str(item.get("candidate_kind") or ""),
        "status": str(gate.get("status") or ""),
        "can_promote": bool(gate.get("can_promote")),
        "score": float(gate.get("score") or 0.0),
        "missing_requirements": list(gate.get("missing_requirements") or [])[:6],
    }


def _claim_tags(topic: dict[str, Any]) -> list[str]:
    tags = [str(item).strip().lower() for item in list(topic.get("topic_tags") or []) if str(item).strip()]
    base = ["research", "curiosity", "artifact_export", "eval_gate"]
    if "trading_learning" in tags or "manual_trader" in tags:
        base.extend(["trading_research", "heuristic_mining"])
    return list(dict.fromkeys(base + tags))[:12]


def _research_topic_kind(topic: dict[str, Any], packet: dict[str, Any]) -> str:
    tags = {str(item).strip().lower() for item in list(topic.get("topic_tags") or []) if str(item).strip()}
    if dict(packet.get("trading_feature_export") or {}):
        return "general"
    if {"integration", "bot", "api", "openclaw", "liquefy"} & tags:
        return "integration"
    if {"ux", "ui", "design"} & tags:
        return "design"
    return "technical"


def _idempotency_key(prefix: str, token: str) -> str:
    return f"{prefix}:{get_local_peer_id()[:12]}:{str(token or '').strip()}"[:128]


def _event(session_id: str, event_type: str, message: str, **details: Any) -> None:
    try:
        append_runtime_event(
            session_id=session_id,
            event_type=event_type,
            message=message,
            details=details,
        )
    except Exception as exc:
        audit_logger.log(
            "autonomous_research_event_error",
            target_id=session_id,
            target_type="session",
            details={"event_type": event_type, "error": str(exc)},
        )
