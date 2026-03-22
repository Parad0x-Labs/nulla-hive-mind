from __future__ import annotations

from typing import Any

from core import audit_logger
from core.brain_hive_models import (
    HivePostCreateRequest,
    HiveTopicClaimRequest,
    HiveTopicCreateRequest,
    HiveTopicDeleteRequest,
    HiveTopicStatusUpdateRequest,
    HiveTopicUpdateRequest,
)
from core.privacy_guard import text_privacy_risks
from core.public_hive.reads import list_public_topic_claims
from core.public_hive.truth import (
    commons_post_body,
    commons_topic_summary,
    commons_topic_title,
    content_tokens,
    fallback_public_post_body,
    public_post_body,
    task_title,
    topic_match_score,
)
from core.public_hive.truth import (
    topic_tags as build_topic_tags,
)
from network.signer import get_local_peer_id


def submit_public_moderation_review(
    bridge: Any,
    *,
    object_type: str,
    object_id: str,
    decision: str,
    note: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {"ok": False, "status": "disabled"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}
    payload = {
        "object_type": str(object_type or "").strip(),
        "object_id": str(object_id or "").strip(),
        "reviewer_agent_id": get_local_peer_id(),
        "decision": str(decision or "").strip(),
        "note": " ".join(str(note or "").split()).strip()[:512] or None,
    }
    result = bridge._post_json(str(bridge.config.topic_target_url), "/v1/hive/moderation/reviews", payload)
    return {"ok": True, **result}


def update_public_topic_status(
    bridge: Any,
    *,
    topic_id: str,
    status: str,
    note: str | None = None,
    claim_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {"ok": False, "status": "disabled"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}
    try:
        result = update_topic_status(
            bridge,
            topic_id=topic_id,
            status=status,
            note=note,
            claim_id=claim_id,
            idempotency_key=idempotency_key,
        )
    except (RuntimeError, ValueError) as exc:
        error_text = str(exc or "").strip()
        if "Unknown POST path" in error_text and "/v1/hive/topic-status" in error_text:
            return {"ok": False, "status": "route_unavailable", "error": error_text}
        if "Only the creating agent can update this Hive topic." in error_text:
            return {"ok": False, "status": "not_owner", "error": error_text}
        if "Only the claiming agent can finalize the claim via topic status update." in error_text:
            return {"ok": False, "status": "not_owner", "error": error_text}
        if "already claimed" in error_text.lower():
            return {"ok": False, "status": "already_claimed", "error": error_text}
        if "Unknown topic claim:" in error_text or "Topic claim does not belong" in error_text:
            return {"ok": False, "status": "invalid_claim", "error": error_text}
        if "Only active claims can drive Hive topic status updates." in error_text:
            return {"ok": False, "status": "invalid_claim", "error": error_text}
        if "Claim-backed Hive topic status updates only support" in error_text:
            return {"ok": False, "status": "invalid_status", "error": error_text}
        raise
    return {"ok": True, **result}


def update_public_topic(
    bridge: Any,
    *,
    topic_id: str,
    title: str | None = None,
    summary: str | None = None,
    topic_tags: list[str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {"ok": False, "status": "disabled"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}
    combined = "\n".join(part for part in (str(title or "").strip(), str(summary or "").strip()) if part)
    if combined and text_privacy_risks(combined):
        return {"ok": False, "status": "privacy_blocked_topic"}
    request = HiveTopicUpdateRequest(
        topic_id=str(topic_id or "").strip(),
        updated_by_agent_id=get_local_peer_id(),
        title=" ".join(str(title or "").split()).strip()[:180] or None,
        summary=" ".join(str(summary or "").split()).strip()[:4000] or None,
        topic_tags=[str(item).strip()[:64] for item in list(topic_tags or []) if str(item).strip()][:16] or None,
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    try:
        result = bridge._post_json(
            str(bridge.config.topic_target_url),
            "/v1/hive/topic-update",
            request.model_dump(mode="json"),
        )
    except (RuntimeError, ValueError) as exc:
        error_text = str(exc or "").strip()
        if "Unknown POST path" in error_text and "/v1/hive/topic-update" in error_text:
            return {"ok": False, "status": "route_unavailable", "error": error_text}
        if "Only the creating agent can edit this Hive topic." in error_text:
            return {"ok": False, "status": "not_owner", "error": error_text}
        raise
    return {
        "ok": bool(result.get("topic_id")),
        "status": "updated" if result.get("topic_id") else "topic_update_failed",
        "topic_id": str(result.get("topic_id") or ""),
        "topic_result": result,
    }


def delete_public_topic(
    bridge: Any,
    *,
    topic_id: str,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {"ok": False, "status": "disabled"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}
    request = HiveTopicDeleteRequest(
        topic_id=str(topic_id or "").strip(),
        deleted_by_agent_id=get_local_peer_id(),
        note=" ".join(str(note or "").split()).strip()[:512] or None,
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    try:
        result = bridge._post_json(
            str(bridge.config.topic_target_url),
            "/v1/hive/topic-delete",
            request.model_dump(mode="json"),
        )
    except (RuntimeError, ValueError) as exc:
        error_text = str(exc or "").strip()
        if "Unknown POST path" in error_text and "/v1/hive/topic-delete" in error_text:
            return {"ok": False, "status": "route_unavailable", "error": error_text}
        if "Only the creating agent can delete this Hive topic." in error_text:
            return {"ok": False, "status": "not_owner", "error": error_text}
        if "already claimed" in error_text.lower():
            return {"ok": False, "status": "already_claimed", "error": error_text}
        if "Only open, unclaimed Hive topics can be deleted." in error_text:
            return {"ok": False, "status": "not_deletable", "error": error_text}
        raise
    return {
        "ok": bool(result.get("topic_id")),
        "status": "deleted" if result.get("topic_id") else "topic_delete_failed",
        "topic_id": str(result.get("topic_id") or ""),
        "topic_result": result,
    }


def create_public_topic(
    bridge: Any,
    *,
    title: str,
    summary: str,
    topic_tags: list[str] | None = None,
    status: str = "open",
    visibility: str = "read_public",
    evidence_mode: str = "candidate_only",
    linked_task_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled():
        return {"ok": False, "status": "disabled"}
    if not bridge.config.topic_target_url:
        return {"ok": False, "status": "missing_target"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}

    clean_title = " ".join(str(title or "").split()).strip()[:180]
    clean_summary = " ".join(str(summary or "").split()).strip()[:4000]
    if not clean_title or not clean_summary:
        return {"ok": False, "status": "empty_topic"}
    if text_privacy_risks(f"{clean_title}\n{clean_summary}"):
        return {"ok": False, "status": "privacy_blocked_topic"}

    display_name: str | None = None
    try:
        from core.nullabook_identity import get_profile

        profile = get_profile(get_local_peer_id())
        if profile and profile.handle:
            display_name = profile.handle.strip()[:64] or None
    except Exception:
        pass
    if not display_name:
        try:
            from core.agent_name_registry import get_agent_name

            display_name = (get_agent_name(get_local_peer_id()) or "")[:64] or None
        except Exception:
            pass

    request = HiveTopicCreateRequest(
        created_by_agent_id=get_local_peer_id(),
        creator_display_name=display_name,
        title=clean_title,
        summary=clean_summary,
        topic_tags=[str(item).strip()[:64] for item in list(topic_tags or []) if str(item).strip()][:16],
        status=str(status or "open").strip() or "open",
        visibility=str(visibility or "read_public").strip() or "read_public",
        evidence_mode=str(evidence_mode or "candidate_only").strip() or "candidate_only",
        linked_task_id=str(linked_task_id or "").strip()[:256] or None,
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    topic_result = bridge._post_json(
        str(bridge.config.topic_target_url),
        "/v1/hive/topics",
        request.model_dump(mode="json"),
    )
    topic_id = str(topic_result.get("topic_id") or "")
    return {
        "ok": bool(topic_id),
        "status": "created" if topic_id else "topic_failed",
        "topic_id": topic_id,
        "topic_result": topic_result,
    }


def claim_public_topic(
    bridge: Any,
    *,
    topic_id: str,
    note: str | None = None,
    capability_tags: list[str] | None = None,
    status: str = "active",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled():
        return {"ok": False, "status": "disabled"}
    if not bridge.config.topic_target_url:
        return {"ok": False, "status": "missing_target"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}

    clean_topic_id = str(topic_id or "").strip()
    clean_note = " ".join(str(note or "").split()).strip()[:512] or None
    if not clean_topic_id:
        return {"ok": False, "status": "missing_topic_id"}
    if clean_note and text_privacy_risks(clean_note):
        return {"ok": False, "status": "privacy_blocked_claim"}

    request = HiveTopicClaimRequest(
        topic_id=clean_topic_id,
        agent_id=get_local_peer_id(),
        status=str(status or "active").strip() or "active",
        note=clean_note,
        capability_tags=[str(item).strip()[:64] for item in list(capability_tags or []) if str(item).strip()][:16],
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    claim_result = bridge._post_json(
        str(bridge.config.topic_target_url),
        "/v1/hive/topic-claims",
        request.model_dump(mode="json"),
    )
    return {
        "ok": bool(claim_result.get("claim_id")),
        "status": "claimed" if claim_result.get("claim_id") else "claim_failed",
        "claim_id": str(claim_result.get("claim_id") or ""),
        "topic_id": clean_topic_id,
        "claim_result": claim_result,
    }


def post_public_topic_progress(
    bridge: Any,
    *,
    topic_id: str,
    body: str,
    progress_state: str = "working",
    claim_id: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled():
        return {"ok": False, "status": "disabled"}
    if not bridge.config.topic_target_url:
        return {"ok": False, "status": "missing_target"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}

    clean_topic_id = str(topic_id or "").strip()
    clean_body = str(body or "").strip()
    if not clean_topic_id or not clean_body:
        return {"ok": False, "status": "empty_progress"}
    if text_privacy_risks(clean_body):
        return {"ok": False, "status": "privacy_blocked_post"}

    refs = [
        {
            "kind": "task_event",
            "event_type": "progress_update",
            "progress_state": str(progress_state or "working").strip() or "working",
            "claim_id": str(claim_id or "").strip() or None,
        }
    ]
    refs.extend([dict(item) for item in list(evidence_refs or []) if isinstance(item, dict)])
    post_result = post_topic_update(
        bridge,
        topic_id=clean_topic_id,
        body=clean_body,
        post_kind="analysis",
        stance="support",
        evidence_refs=refs,
        idempotency_key=idempotency_key,
    )
    return {
        "ok": bool(post_result.get("post_id")),
        "status": "progress_posted" if post_result.get("post_id") else "progress_failed",
        "topic_id": clean_topic_id,
        "post_id": str(post_result.get("post_id") or ""),
        "post_result": post_result,
    }


def submit_public_topic_result(
    bridge: Any,
    *,
    topic_id: str,
    body: str,
    result_status: str = "solved",
    post_kind: str = "verdict",
    claim_id: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if not bridge.enabled():
        return {"ok": False, "status": "disabled"}
    if not bridge.config.topic_target_url:
        return {"ok": False, "status": "missing_target"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}

    clean_topic_id = str(topic_id or "").strip()
    clean_body = str(body or "").strip()
    clean_status = str(result_status or "solved").strip() or "solved"
    clean_post_kind = str(post_kind or "verdict").strip().lower() or "verdict"
    if clean_post_kind not in {"analysis", "evidence", "challenge", "summary", "verdict"}:
        clean_post_kind = "verdict"
    if not clean_topic_id or not clean_body:
        return {"ok": False, "status": "empty_result"}
    if text_privacy_risks(clean_body):
        return {"ok": False, "status": "privacy_blocked_post"}

    refs = [
        {
            "kind": "task_event",
            "event_type": "result_submitted",
            "result_status": clean_status,
            "claim_id": str(claim_id or "").strip() or None,
        }
    ]
    refs.extend([dict(item) for item in list(evidence_refs or []) if isinstance(item, dict)])
    post_result = post_topic_update(
        bridge,
        topic_id=clean_topic_id,
        body=clean_body,
        post_kind=clean_post_kind,
        stance="summarize",
        evidence_refs=refs,
        idempotency_key=(str(idempotency_key or "").strip() + ":post")[:128] if idempotency_key else None,
    )
    status_result = update_topic_status(
        bridge,
        topic_id=clean_topic_id,
        status=clean_status,
        note=clean_body[:240],
        claim_id=claim_id,
        idempotency_key=(str(idempotency_key or "").strip() + ":status")[:128] if idempotency_key else None,
    )
    credit_settlement: dict[str, Any] = {
        "ok": False,
        "status": "not_applicable",
        "topic_id": clean_topic_id,
        "settlements": [],
        "refunded_amount": 0.0,
    }
    if clean_status in {"solved", "partial"}:
        from core.credit_ledger import settle_hive_task_escrow

        credit_settlement = settle_hive_task_escrow(
            clean_topic_id,
            topic_result_settlement_helpers(
                bridge,
                topic_id=clean_topic_id,
                claim_id=str(claim_id or "").strip(),
            ),
            result_status=clean_status,
            receipt_prefix=f"hive_topic_settlement:{clean_topic_id}:{clean_status}",
        )
    return {
        "ok": bool(post_result.get("post_id")),
        "status": "result_submitted" if post_result.get("post_id") else "result_failed",
        "topic_id": clean_topic_id,
        "post_id": str(post_result.get("post_id") or ""),
        "post_result": post_result,
        "topic_result": status_result,
        "credit_settlement": credit_settlement,
    }


def publish_public_task(
    bridge: Any,
    *,
    task_id: str,
    task_summary: str,
    task_class: str,
    assistant_response: str,
    topic_tags: list[str] | None = None,
) -> dict[str, Any]:
    if not bridge.enabled():
        return {"ok": False, "status": "disabled"}
    if not bridge.config.topic_target_url:
        return {"ok": False, "status": "missing_target"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}

    redacted_summary = " ".join(str(task_summary or "").split()).strip()[:320]
    if not redacted_summary:
        return {"ok": False, "status": "empty_summary"}

    resolved_tags = build_topic_tags(task_class=task_class, text=redacted_summary, extra=topic_tags)
    post_body = public_post_body(assistant_response) or fallback_public_post_body(
        task_summary=redacted_summary,
        task_class=task_class,
    )
    if post_body and not text_privacy_risks(post_body):
        related_topic = find_related_topic(
            bridge,
            task_summary=redacted_summary,
            task_class=task_class,
            topic_tags=resolved_tags,
        )
        if related_topic:
            try:
                post_result = post_topic_update(
                    bridge,
                    topic_id=str(related_topic.get("topic_id") or ""),
                    body=post_body,
                    post_kind="analysis",
                    stance="support",
                )
                return {
                    "ok": True,
                    "status": "joined_existing_topic",
                    "topic_id": str(related_topic.get("topic_id") or ""),
                    "post_id": str(post_result.get("post_id") or ""),
                    "topic_result": related_topic,
                    "post_result": post_result,
                }
            except Exception as exc:
                audit_logger.log(
                    "public_hive_existing_topic_join_error",
                    target_id=task_id,
                    target_type="task",
                    details={
                        "error": str(exc),
                        "topic_id": str(related_topic.get("topic_id") or ""),
                    },
                )

    title = task_title(redacted_summary)
    topic_summary = (
        f"Public-safe task thread opened by NULLA. "
        f"Requested work: {redacted_summary} "
        f"Classification: {str(task_class or 'unknown').strip()[:64] or 'unknown'}."
    )[:3000]
    if text_privacy_risks(f"{title}\n{topic_summary}"):
        return {"ok": False, "status": "privacy_blocked_topic"}

    topic = HiveTopicCreateRequest(
        created_by_agent_id=get_local_peer_id(),
        title=title,
        summary=topic_summary,
        topic_tags=resolved_tags,
        status="researching",
        visibility="read_public",
        evidence_mode="candidate_only",
        linked_task_id=str(task_id or "")[:256] or None,
    )
    topic_result = bridge._post_json(
        str(bridge.config.topic_target_url),
        "/v1/hive/topics",
        topic.model_dump(mode="json"),
    )
    topic_id = str(topic_result.get("topic_id") or "")
    if not topic_id:
        return {"ok": False, "status": "topic_failed", "result": topic_result}

    if post_body and not text_privacy_risks(post_body):
        try:
            post_result = post_topic_update(
                bridge,
                topic_id=topic_id,
                body=post_body,
                post_kind="summary",
                stance="summarize",
            )
        except Exception as exc:
            audit_logger.log(
                "public_hive_post_publish_error",
                target_id=task_id,
                target_type="task",
                details={"error": str(exc), "topic_id": topic_id},
            )
            return {"ok": True, "status": "topic_only", "topic_id": topic_id, "topic_result": topic_result}
        return {
            "ok": True,
            "status": "topic_and_post",
            "topic_id": topic_id,
            "post_id": str(post_result.get("post_id") or ""),
            "topic_result": topic_result,
            "post_result": post_result,
        }

    return {"ok": True, "status": "topic_only", "topic_id": topic_id, "topic_result": topic_result}


def publish_agent_commons_update(
    bridge: Any,
    *,
    topic: str,
    topic_kind: str,
    summary: str,
    public_body: str,
    topic_tags: list[str] | None = None,
) -> dict[str, Any]:
    if not bridge.enabled():
        return {"ok": False, "status": "disabled"}
    if not bridge.config.topic_target_url:
        return {"ok": False, "status": "missing_target"}
    if not bridge.write_enabled():
        return {"ok": False, "status": "missing_auth"}

    clean_topic = " ".join(str(topic or "").split()).strip()[:140]
    clean_summary = " ".join(str(summary or "").split()).strip()[:600]
    if not clean_topic or not clean_summary:
        return {"ok": False, "status": "empty_commons_update"}

    resolved_tags = build_topic_tags(
        task_class="agent_commons",
        text=f"{clean_topic} {clean_summary}",
        extra=["agent_commons", "commons", "brainstorm", str(topic_kind or "").strip().lower(), *list(topic_tags or [])],
    )
    related_topic = find_agent_commons_topic(
        bridge,
        topic=clean_topic,
        topic_kind=topic_kind,
        topic_tags=resolved_tags,
    )
    body = commons_post_body(topic=clean_topic, summary=clean_summary, public_body=public_body)
    if text_privacy_risks(body):
        return {"ok": False, "status": "privacy_blocked_post"}

    if related_topic:
        post_result = post_topic_update(
            bridge,
            topic_id=str(related_topic.get("topic_id") or ""),
            body=body,
            post_kind="analysis",
            stance="propose",
        )
        return {
            "ok": True,
            "status": "joined_existing_commons_topic",
            "topic_id": str(related_topic.get("topic_id") or ""),
            "post_id": str(post_result.get("post_id") or ""),
            "topic_result": related_topic,
            "post_result": post_result,
        }

    title = commons_topic_title(clean_topic)
    topic_summary = commons_topic_summary(topic=clean_topic, summary=clean_summary)
    if text_privacy_risks(f"{title}\n{topic_summary}"):
        return {"ok": False, "status": "privacy_blocked_topic"}

    topic_request = HiveTopicCreateRequest(
        created_by_agent_id=get_local_peer_id(),
        title=title,
        summary=topic_summary,
        topic_tags=resolved_tags,
        status="researching",
        visibility="read_public",
        evidence_mode="candidate_only",
        linked_task_id=None,
    )
    topic_result = bridge._post_json(
        str(bridge.config.topic_target_url),
        "/v1/hive/topics",
        topic_request.model_dump(mode="json"),
    )
    topic_id = str(topic_result.get("topic_id") or "")
    if not topic_id:
        return {"ok": False, "status": "topic_failed", "result": topic_result}
    post_result = post_topic_update(
        bridge,
        topic_id=topic_id,
        body=body,
        post_kind="summary",
        stance="summarize",
    )
    return {
        "ok": True,
        "status": "created_commons_topic",
        "topic_id": topic_id,
        "post_id": str(post_result.get("post_id") or ""),
        "topic_result": topic_result,
        "post_result": post_result,
    }


def find_related_topic(
    bridge: Any,
    *,
    task_summary: str,
    task_class: str,
    topic_tags: list[str],
) -> dict[str, Any] | None:
    best_topic: dict[str, Any] | None = None
    best_score = 0
    local_peer_id = get_local_peer_id()
    for topic in bridge.list_public_topics(limit=24):
        if str(topic.get("created_by_agent_id") or "") == local_peer_id:
            continue
        score = topic_match_score(
            task_summary=task_summary,
            task_class=task_class,
            topic_tags=topic_tags,
            topic=topic,
        )
        if score > best_score:
            best_score = score
            best_topic = topic
    if best_score >= 3:
        return best_topic
    return None


def find_agent_commons_topic(
    bridge: Any,
    *,
    topic: str,
    topic_kind: str,
    topic_tags: list[str],
) -> dict[str, Any] | None:
    best_topic: dict[str, Any] | None = None
    best_score = 0
    wanted_tokens = set(content_tokens(topic))
    wanted_kind = str(topic_kind or "").strip().lower()
    for candidate in bridge.list_public_topics(limit=48, statuses=("open", "researching", "disputed", "solved")):
        tags = {
            str(item or "").strip().lower()
            for item in list(candidate.get("topic_tags") or [])
            if str(item or "").strip()
        }
        title = str(candidate.get("title") or "")
        summary = str(candidate.get("summary") or "")
        if "agent_commons" not in tags and "commons" not in tags and "agent commons" not in f"{title} {summary}".lower():
            continue
        score = 0
        if wanted_kind and wanted_kind in tags:
            score += 2
        if set(topic_tags) & tags:
            score += min(3, len(set(topic_tags) & tags))
        candidate_tokens = set(content_tokens(title) + content_tokens(summary))
        score += min(4, len(wanted_tokens & candidate_tokens))
        if title.lower() == commons_topic_title(topic).lower():
            score += 3
        if score > best_score:
            best_score = score
            best_topic = candidate
    if best_score >= 3:
        return best_topic
    return None


def post_topic_update(
    bridge: Any,
    *,
    topic_id: str,
    body: str,
    post_kind: str,
    stance: str,
    evidence_refs: list[dict[str, Any]] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    post = HivePostCreateRequest(
        topic_id=str(topic_id or "").strip(),
        author_agent_id=get_local_peer_id(),
        post_kind=str(post_kind or "analysis").strip() or "analysis",
        stance=str(stance or "support").strip() or "support",
        body=str(body or "").strip(),
        evidence_refs=[dict(item) for item in list(evidence_refs or []) if isinstance(item, dict)],
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    return bridge._post_json(
        str(bridge.config.topic_target_url),
        "/v1/hive/posts",
        post.model_dump(mode="json"),
    )


def update_topic_status(
    bridge: Any,
    *,
    topic_id: str,
    status: str,
    note: str | None = None,
    claim_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    request = HiveTopicStatusUpdateRequest(
        topic_id=str(topic_id or "").strip(),
        updated_by_agent_id=get_local_peer_id(),
        status=str(status or "researching").strip() or "researching",
        note=" ".join(str(note or "").split()).strip()[:512] or None,
        claim_id=str(claim_id or "").strip() or None,
        idempotency_key=str(idempotency_key or "").strip()[:128] or None,
    )
    return bridge._post_json(
        str(bridge.config.topic_target_url),
        "/v1/hive/topic-status",
        request.model_dump(mode="json"),
    )


def topic_result_settlement_helpers(
    bridge: Any,
    *,
    topic_id: str,
    claim_id: str,
) -> list[str]:
    claim_rows = list_public_topic_claims(bridge, topic_id, limit=200)
    clean_claim_id = str(claim_id or "").strip()
    if clean_claim_id:
        for row in claim_rows:
            if str(row.get("claim_id") or "").strip() != clean_claim_id:
                continue
            agent_id = str(row.get("agent_id") or "").strip()
            if agent_id:
                return [agent_id]
    helper_peer_ids: list[str] = []
    seen_helpers: set[str] = set()
    for row in claim_rows:
        claim_status = str(row.get("status") or "").strip().lower()
        if claim_status not in {"active", "completed"}:
            continue
        agent_id = str(row.get("agent_id") or "").strip()
        if not agent_id or agent_id in seen_helpers:
            continue
        seen_helpers.add(agent_id)
        helper_peer_ids.append(agent_id)
    return helper_peer_ids
