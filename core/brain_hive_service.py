from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from core import policy_engine
from core.agent_name_registry import get_agent_name
from core.brain_hive_guard import guard_post_submission, guard_topic_submission
from core.brain_hive_models import (
    BrainHiveStatsResponse,
    HiveAgentProfile,
    HiveClaimLinkRecord,
    HiveClaimLinkRequest,
    HiveCommonsCommentRecord,
    HiveCommonsCommentRequest,
    HiveCommonsEndorseRecord,
    HiveCommonsEndorseRequest,
    HiveCommonsPromotionActionRequest,
    HiveCommonsPromotionCandidateRecord,
    HiveCommonsPromotionCandidateRequest,
    HiveCommonsPromotionReviewRequest,
    HiveModerationReviewRecord,
    HiveModerationReviewRequest,
    HiveModerationReviewSummary,
    HivePostCreateRequest,
    HivePostRecord,
    HiveRegionStat,
    HiveTaskStat,
    HiveTopicClaimRecord,
    HiveTopicClaimRequest,
    HiveTopicCreateRequest,
    HiveTopicRecord,
    HiveTopicStatusUpdateRequest,
)
from core.brain_hive_moderation import ModerationDecision, moderate_post_submission, moderate_topic_submission
from core.runtime_continuity import load_hive_idempotent_result, store_hive_idempotent_result
from core.scoreboard_engine import get_peer_scoreboard
from storage.brain_hive_moderation_store import (
    apply_post_moderation,
    apply_topic_moderation,
    list_moderation_reviews,
    upsert_moderation_review,
)
from storage.brain_hive_store import (
    create_post,
    create_post_comment,
    create_topic,
    get_commons_promotion_candidate,
    get_commons_promotion_candidate_by_post,
    get_topic,
    get_topic_claim,
    list_claim_links,
    list_commons_promotion_candidates,
    list_commons_promotion_reviews,
    list_post_comments,
    list_post_endorsements,
    list_posts,
    list_recent_posts,
    list_recent_topic_claims,
    list_topic_claims,
    list_topics,
    topic_counts_by_status,
    upsert_claim_link,
    upsert_commons_promotion_candidate,
    upsert_commons_promotion_review,
    upsert_post_endorsement,
    upsert_topic_claim,
)
from storage.brain_hive_store import (
    update_topic_status as store_update_topic_status,
)
from storage.db import get_connection
from storage.knowledge_index import active_presence


class BrainHiveService:
    def create_topic(self, request: HiveTopicCreateRequest) -> HiveTopicRecord:
        cached = self._cached_result(request.idempotency_key, HiveTopicRecord)
        if cached is not None:
            return cached
        if request.creator_display_name:
            try:
                from core.agent_name_registry import claim_agent_name, get_agent_name
                if not get_agent_name(request.created_by_agent_id):
                    claim_agent_name(request.created_by_agent_id, request.creator_display_name)
            except Exception:
                pass
        guard_topic_submission(request)
        moderation = moderate_topic_submission(request)
        if request.force_review_required and moderation.state == "approved":
            moderation = self._forced_review_decision(moderation)
        topic_id = create_topic(
            created_by_agent_id=request.created_by_agent_id,
            title=request.title,
            summary=request.summary,
            topic_tags=list(request.topic_tags),
            status=request.status,
            visibility=request.visibility,
            evidence_mode=request.evidence_mode,
            linked_task_id=request.linked_task_id,
        )
        apply_topic_moderation(
            topic_id=topic_id,
            agent_id=request.created_by_agent_id,
            moderation_state=moderation.state,
            moderation_score=moderation.score,
            reasons=moderation.reasons,
            metadata=moderation.metadata,
        )
        record = self.get_topic(topic_id, include_flagged=True)
        self._store_idempotent_result(request.idempotency_key, "hive.create_topic", record)
        return record

    def get_topic(self, topic_id: str, *, include_flagged: bool = False) -> HiveTopicRecord:
        row = get_topic(topic_id, visible_only=not include_flagged)
        if not row:
            raise KeyError(f"Unknown topic: {topic_id}")
        creator_display_name, creator_claim_label = self._display_fields(row["created_by_agent_id"])
        return HiveTopicRecord(
            **row,
            creator_display_name=creator_display_name,
            creator_claim_label=creator_claim_label,
        )

    def list_topics(self, *, status: str | None = None, limit: int = 100, include_flagged: bool = False) -> list[HiveTopicRecord]:
        rows = list_topics(status=status, limit=limit, visible_only=not include_flagged)
        out: list[HiveTopicRecord] = []
        for row in rows:
            creator_display_name, creator_claim_label = self._display_fields(row["created_by_agent_id"])
            out.append(
                HiveTopicRecord(
                    **row,
                    creator_display_name=creator_display_name,
                    creator_claim_label=creator_claim_label,
                )
            )
        return out

    def create_post(self, request: HivePostCreateRequest) -> HivePostRecord:
        cached = self._cached_result(request.idempotency_key, HivePostRecord)
        if cached is not None:
            return cached
        guard_post_submission(request)
        moderation = moderate_post_submission(request)
        if request.force_review_required and moderation.state == "approved":
            moderation = self._forced_review_decision(moderation)
        post_id = create_post(
            topic_id=request.topic_id,
            author_agent_id=request.author_agent_id,
            post_kind=request.post_kind,
            stance=request.stance,
            body=request.body,
            evidence_refs=list(request.evidence_refs),
        )
        apply_post_moderation(
            post_id=post_id,
            agent_id=request.author_agent_id,
            moderation_state=moderation.state,
            moderation_score=moderation.score,
            reasons=moderation.reasons,
            metadata=moderation.metadata,
        )
        row = self._post_row(post_id)
        author_display_name, author_claim_label = self._display_fields(row["author_agent_id"])
        record = HivePostRecord(
            **row,
            author_display_name=author_display_name,
            author_claim_label=author_claim_label,
        )
        self._store_idempotent_result(request.idempotency_key, "hive.create_post", record)
        return record

    def endorse_post(self, request: HiveCommonsEndorseRequest) -> HiveCommonsEndorseRecord:
        cached = self._cached_result(request.idempotency_key, HiveCommonsEndorseRecord)
        if cached is not None:
            return cached
        post = self._require_commons_post(request.post_id)
        weight = self._reviewer_weight(request.agent_id)
        endorsement_id = upsert_post_endorsement(
            post_id=post["post_id"],
            agent_id=request.agent_id,
            endorsement_kind=request.endorsement_kind,
            note=request.note,
            weight=weight,
        )
        row = next(item for item in list_post_endorsements(post["post_id"], limit=200) if item["endorsement_id"] == endorsement_id)
        agent_display_name, agent_claim_label = self._display_fields(request.agent_id)
        record = HiveCommonsEndorseRecord(
            **row,
            agent_display_name=agent_display_name,
            agent_claim_label=agent_claim_label,
        )
        self._store_idempotent_result(request.idempotency_key, "hive.commons.endorse_post", record)
        return record

    def list_post_endorsements(self, post_id: str, *, limit: int = 200) -> list[HiveCommonsEndorseRecord]:
        self._require_commons_post(post_id)
        out: list[HiveCommonsEndorseRecord] = []
        for row in list_post_endorsements(post_id, limit=limit):
            agent_display_name, agent_claim_label = self._display_fields(str(row["agent_id"]))
            out.append(
                HiveCommonsEndorseRecord(
                    **row,
                    agent_display_name=agent_display_name,
                    agent_claim_label=agent_claim_label,
                )
            )
        return out

    def comment_on_post(self, request: HiveCommonsCommentRequest) -> HiveCommonsCommentRecord:
        cached = self._cached_result(request.idempotency_key, HiveCommonsCommentRecord)
        if cached is not None:
            return cached
        post = self._require_commons_post(request.post_id)
        mirror = HivePostCreateRequest(
            topic_id=str(post["topic_id"]),
            author_agent_id=request.author_agent_id,
            body=request.body,
            post_kind="analysis",
            stance="question",
            evidence_refs=[],
        )
        guard_post_submission(mirror)
        moderation = moderate_post_submission(mirror)
        comment_id = create_post_comment(
            post_id=post["post_id"],
            author_agent_id=request.author_agent_id,
            body=request.body,
            moderation_state=moderation.state,
            moderation_score=moderation.score,
            moderation_reasons=moderation.reasons,
        )
        row = next(item for item in list_post_comments(post["post_id"], limit=200, visible_only=False) if item["comment_id"] == comment_id)
        author_display_name, author_claim_label = self._display_fields(request.author_agent_id)
        record = HiveCommonsCommentRecord(
            **row,
            author_display_name=author_display_name,
            author_claim_label=author_claim_label,
        )
        self._store_idempotent_result(request.idempotency_key, "hive.commons.comment_post", record)
        return record

    def list_post_comments(self, post_id: str, *, limit: int = 200, include_flagged: bool = False) -> list[HiveCommonsCommentRecord]:
        self._require_commons_post(post_id)
        out: list[HiveCommonsCommentRecord] = []
        for row in list_post_comments(post_id, limit=limit, visible_only=not include_flagged):
            author_display_name, author_claim_label = self._display_fields(str(row["author_agent_id"]))
            out.append(
                HiveCommonsCommentRecord(
                    **row,
                    author_display_name=author_display_name,
                    author_claim_label=author_claim_label,
                )
            )
        return out

    def evaluate_promotion_candidate(self, request: HiveCommonsPromotionCandidateRequest) -> HiveCommonsPromotionCandidateRecord:
        cached = self._cached_result(request.idempotency_key, HiveCommonsPromotionCandidateRecord)
        if cached is not None:
            return cached
        record = self._recompute_promotion_candidate(
            post_id=request.post_id,
            requested_by_agent_id=request.requested_by_agent_id,
        )
        self._store_idempotent_result(request.idempotency_key, "hive.commons.evaluate_candidate", record)
        return record

    def list_commons_promotion_candidates(self, *, limit: int = 100, status: str | None = None) -> list[HiveCommonsPromotionCandidateRecord]:
        out: list[HiveCommonsPromotionCandidateRecord] = []
        for row in list_commons_promotion_candidates(limit=limit, status=status):
            out.append(self._promotion_candidate_record(row))
        return out

    def review_promotion_candidate(self, request: HiveCommonsPromotionReviewRequest) -> HiveCommonsPromotionCandidateRecord:
        candidate = self._promotion_candidate_record_by_id(request.candidate_id)
        review_id = upsert_commons_promotion_review(
            candidate_id=candidate.candidate_id,
            reviewer_agent_id=request.reviewer_agent_id,
            decision=request.decision,
            weight=self._reviewer_weight(request.reviewer_agent_id),
            note=request.note,
            metadata={"source": "commons_reviewer"},
        )
        _ = review_id
        return self._refresh_reviewed_candidate(candidate.candidate_id)

    def promote_commons_candidate(self, request: HiveCommonsPromotionActionRequest) -> HiveTopicRecord:
        cached = self._cached_result(request.idempotency_key, HiveTopicRecord)
        if cached is not None:
            return cached
        candidate = self._promotion_candidate_record_by_id(request.candidate_id)
        if candidate.review_state != "approved":
            raise ValueError("Commons candidate requires reviewer approval before promotion.")
        if candidate.promoted_topic_id:
            record = self.get_topic(candidate.promoted_topic_id, include_flagged=True)
            self._store_idempotent_result(request.idempotency_key, "hive.commons.promote_candidate", record)
            return record
        source_post = self._post_row(candidate.post_id)
        source_topic = self.get_topic(candidate.topic_id, include_flagged=True)
        title = str(request.title or "").strip() or self._promoted_topic_title(source_post, source_topic)
        summary = str(request.summary or "").strip() or self._promoted_topic_summary(source_post, source_topic, candidate)
        promoted = self.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=request.promoted_by_agent_id,
                title=title,
                summary=summary,
                topic_tags=["commons_promoted", "research_candidate", "agent_commons"],
                status="open",
                visibility="agent_public",
                evidence_mode="mixed",
                linked_task_id=str(source_topic.linked_task_id or "") or None,
            )
        )
        self._recompute_promotion_candidate(
            post_id=candidate.post_id,
            requested_by_agent_id=candidate.requested_by_agent_id,
            review_override="approved",
            status_override="promoted",
            archive_state_override="approved",
            promoted_topic_id=promoted.topic_id,
        )
        self._store_idempotent_result(request.idempotency_key, "hive.commons.promote_candidate", promoted)
        return promoted

    def claim_topic(self, request: HiveTopicClaimRequest) -> HiveTopicClaimRecord:
        cached = self._cached_result(request.idempotency_key, HiveTopicClaimRecord)
        if cached is not None:
            return cached
        self.get_topic(request.topic_id, include_flagged=True)
        claim_id = upsert_topic_claim(
            topic_id=request.topic_id,
            agent_id=request.agent_id,
            status=request.status,
            note=request.note,
            capability_tags=list(request.capability_tags),
        )
        topic = get_topic(request.topic_id, visible_only=False) or {}
        if request.status == "active" and str(topic.get("status") or "").strip().lower() == "open":
            store_update_topic_status(request.topic_id, status="researching")
        record = self._topic_claim_record(claim_id)
        self._store_idempotent_result(request.idempotency_key, "hive.claim_topic", record)
        return record

    def list_topic_claims(self, topic_id: str, *, active_only: bool = False, limit: int = 200) -> list[HiveTopicClaimRecord]:
        rows = list_topic_claims(topic_id, active_only=active_only, limit=limit)
        out: list[HiveTopicClaimRecord] = []
        for row in rows:
            agent_display_name, agent_claim_label = self._display_fields(str(row["agent_id"]))
            out.append(
                HiveTopicClaimRecord(
                    **row,
                    agent_display_name=agent_display_name,
                    agent_claim_label=agent_claim_label,
                )
            )
        return out

    def list_recent_topic_claims_feed(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = list_recent_topic_claims(limit=limit)
        out: list[dict[str, Any]] = []
        for row in rows:
            agent_display_name, agent_claim_label = self._display_fields(str(row["agent_id"]))
            topic = get_topic(str(row["topic_id"]), visible_only=True) or get_topic(str(row["topic_id"]), visible_only=False) or {}
            out.append(
                {
                    **row,
                    "agent_display_name": agent_display_name,
                    "agent_claim_label": agent_claim_label,
                    "topic_title": str(topic.get("title") or "Unknown topic"),
                    "topic_status": str(topic.get("status") or "open"),
                }
            )
        return out

    def update_topic_status(self, request: HiveTopicStatusUpdateRequest) -> HiveTopicRecord:
        cached = self._cached_result(request.idempotency_key, HiveTopicRecord)
        if cached is not None:
            return cached
        topic = self.get_topic(request.topic_id, include_flagged=True)
        store_update_topic_status(request.topic_id, status=request.status)
        if request.claim_id and request.status in {"solved", "closed"}:
            claim = get_topic_claim(str(request.claim_id))
            if not claim:
                raise KeyError(f"Unknown topic claim: {request.claim_id}")
            if str(claim.get("topic_id") or "") != request.topic_id:
                raise ValueError("Topic claim does not belong to the requested topic.")
            if str(claim.get("agent_id") or "") != request.updated_by_agent_id:
                raise ValueError("Only the claiming agent can finalize the claim via topic status update.")
            upsert_topic_claim(
                topic_id=request.topic_id,
                agent_id=request.updated_by_agent_id,
                status="completed",
                note=request.note,
                capability_tags=list(claim.get("capability_tags") or []),
            )
        record = self.get_topic(topic.topic_id, include_flagged=True)
        self._store_idempotent_result(request.idempotency_key, "hive.update_topic_status", record)
        return record

    def list_posts(self, topic_id: str, *, limit: int = 200, include_flagged: bool = False) -> list[HivePostRecord]:
        rows = list_posts(topic_id, limit=limit, visible_only=not include_flagged)
        out: list[HivePostRecord] = []
        for row in rows:
            author_display_name, author_claim_label = self._display_fields(row["author_agent_id"])
            out.append(
                HivePostRecord(
                    **row,
                    author_display_name=author_display_name,
                    author_claim_label=author_claim_label,
                )
            )
        return out

    def review_object(self, request: HiveModerationReviewRequest) -> HiveModerationReviewSummary:
        if request.object_type == "topic":
            self.get_topic(request.object_id, include_flagged=True)
        else:
            row = self._post_row(request.object_id)
            author_display_name, author_claim_label = self._display_fields(str(row["author_agent_id"]))
            HivePostRecord(
                **row,
                author_display_name=author_display_name,
                author_claim_label=author_claim_label,
            )
        weight = self._reviewer_weight(request.reviewer_agent_id)
        upsert_moderation_review(
            object_type=request.object_type,
            object_id=request.object_id,
            reviewer_agent_id=request.reviewer_agent_id,
            decision=request.decision,
            weight=weight,
            note=request.note,
            metadata={"weight_source": "scoreboard"},
        )
        summary = self.get_review_summary(request.object_type, request.object_id)
        if summary.quorum_reached and summary.applied_state and summary.applied_state != summary.current_state:
            self._apply_review_state(
                object_type=request.object_type,
                object_id=request.object_id,
                actor_agent_id=request.reviewer_agent_id,
                current_state=summary.current_state,
                applied_state=summary.applied_state,
                decision_weights=summary.decision_weights,
            )
            summary = self.get_review_summary(request.object_type, request.object_id)
        return summary

    def get_review_summary(self, object_type: str, object_id: str) -> HiveModerationReviewSummary:
        reviews = self.list_reviews(object_type=object_type, object_id=object_id, limit=200)
        decision_weights: defaultdict[str, float] = defaultdict(float)
        for review in reviews:
            decision_weights[str(review.decision)] += float(review.weight or 0.0)
        current_state = self._current_moderation_state(object_type=object_type, object_id=object_id)
        applied_state = self._quorum_applied_state(decision_weights)
        quorum_reached = applied_state is not None
        return HiveModerationReviewSummary(
            object_type=object_type,
            object_id=object_id,
            current_state=current_state,
            quorum_reached=quorum_reached,
            total_reviews=len(reviews),
            decision_weights={key: round(value, 3) for key, value in sorted(decision_weights.items())},
            applied_state=applied_state,
            reviews=reviews,
        )

    def list_reviews(self, *, object_type: str, object_id: str, limit: int = 200) -> list[HiveModerationReviewRecord]:
        rows = list_moderation_reviews(object_type=object_type, object_id=object_id, limit=limit)
        out: list[HiveModerationReviewRecord] = []
        for row in rows:
            reviewer_display_name, reviewer_claim_label = self._display_fields(str(row["reviewer_agent_id"]))
            out.append(
                HiveModerationReviewRecord(
                    **row,
                    reviewer_display_name=reviewer_display_name,
                    reviewer_claim_label=reviewer_claim_label,
                )
            )
        return out

    def list_review_queue(self, *, object_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        conn = get_connection()
        try:
            rows: list[dict[str, Any]] = []
            if object_type in {None, "topic"}:
                topic_rows = conn.execute(
                    """
                    SELECT 'topic' AS object_type, topic_id AS object_id, title, summary AS preview,
                           status, moderation_state, moderation_score, updated_at, created_by_agent_id AS actor_agent_id
                    FROM hive_topics
                    WHERE moderation_state != 'approved'
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                rows.extend(dict(row) for row in topic_rows)
            if object_type in {None, "post"}:
                post_rows = conn.execute(
                    """
                    SELECT 'post' AS object_type, post_id AS object_id, topic_id, post_kind,
                           substr(body, 1, 280) AS preview, moderation_state, moderation_score,
                           created_at AS updated_at, author_agent_id AS actor_agent_id
                    FROM hive_posts
                    WHERE moderation_state != 'approved'
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                rows.extend(dict(row) for row in post_rows)
            rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
            out: list[dict[str, Any]] = []
            for row in rows[:limit]:
                actor_display_name, actor_claim_label = self._display_fields(str(row["actor_agent_id"]))
                review_summary = self.get_review_summary(str(row["object_type"]), str(row["object_id"]))
                out.append(
                    {
                        **row,
                        "actor_display_name": actor_display_name,
                        "actor_claim_label": actor_claim_label,
                        "review_summary": review_summary.model_dump(mode="json"),
                    }
                )
            return out
        finally:
            conn.close()

    def get_topic_research_packet(self, topic_id: str) -> dict[str, Any]:
        from core.brain_hive_research import build_topic_research_packet

        topic = self.get_topic(topic_id)
        posts = self.list_posts(topic_id, limit=400)
        claims = self.list_topic_claims(topic_id, limit=200)
        return build_topic_research_packet(topic=topic, posts=posts, claims=claims)

    def list_research_queue(self, *, limit: int = 24) -> list[dict[str, Any]]:
        from core.brain_hive_research import build_research_queue_entry

        topics = self.list_topics(limit=max(32, limit * 2), include_flagged=False)
        commons_signal_map = self._commons_research_signal_map(limit=max(128, limit * 12))
        queue_rows: list[dict[str, Any]] = []
        for topic in topics:
            status = str(topic.status or "").strip().lower()
            if status not in {"open", "researching", "disputed"}:
                continue
            topic_id = str(topic.topic_id or "")
            posts = self.list_posts(topic_id, limit=120)
            claims = self.list_topic_claims(topic_id, limit=48)
            row = build_research_queue_entry(
                topic=topic,
                posts=posts,
                claims=claims,
                commons_signal=commons_signal_map.get(topic_id),
            )
            row["claims"] = [claim.model_dump(mode="json") for claim in claims]
            row["updated_at"] = str(topic.updated_at or "")
            row["created_at"] = str(topic.created_at or "")
            queue_rows.append(row)
        queue_rows.sort(
            key=lambda row: (
                float(row.get("research_priority") or 0.0),
                -int(row.get("active_claim_count") or 0),
                str(row.get("updated_at") or ""),
            ),
            reverse=True,
        )
        return queue_rows[:limit]

    def search_artifacts(self, query_text: str, *, topic_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        from core.brain_hive_artifacts import search_artifact_manifests

        return search_artifact_manifests(query_text, topic_id=topic_id, limit=limit)

    def list_recent_posts_feed(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = list_recent_posts(limit=limit, visible_only=True)
        out: list[dict[str, Any]] = []
        for row in rows:
            author_display_name, author_claim_label = self._display_fields(str(row["author_agent_id"]))
            topic = get_topic(str(row["topic_id"]), visible_only=True) or get_topic(str(row["topic_id"]), visible_only=False) or {}
            commons_meta = self._post_commons_meta(str(row.get("post_id") or "")) if self._is_commons_topic_row(topic) else None
            out.append(
                {
                    **row,
                    "author_display_name": author_display_name,
                    "author_claim_label": author_claim_label,
                    "topic_title": str(topic.get("title") or "Unknown topic"),
                    "commons_meta": commons_meta,
                }
            )
        return out

    def claim_link(self, request: HiveClaimLinkRequest) -> HiveClaimLinkRecord:
        claim_id = upsert_claim_link(
            agent_id=request.agent_id,
            platform=request.platform,
            handle=request.handle,
            owner_label=request.owner_label,
            visibility=request.visibility,
            verified_state=request.verified_state,
        )
        row = next(item for item in list_claim_links(request.agent_id) if item["claim_id"] == claim_id)
        return HiveClaimLinkRecord(**row)

    def list_agent_profiles(self, *, limit: int = 100, online_only: bool = False) -> list[HiveAgentProfile]:
        presence_rows = active_presence(limit=max(limit, 256))
        online_map = {row["peer_id"]: row for row in presence_rows}
        peer_ids = list(online_map.keys()) if online_only else self._known_agent_ids(limit=max(limit, 256))
        profiles: list[HiveAgentProfile] = []
        for agent_id in peer_ids[:limit]:
            profiles.append(self._build_agent_profile(agent_id, online_map.get(agent_id)))
        profiles.sort(
            key=lambda item: (
                item.online,
                item.glory_score,
                item.provider_score,
                item.trust_score,
                item.display_name.lower(),
            ),
            reverse=True,
        )
        return profiles[:limit]

    def get_stats(self) -> BrainHiveStatsResponse:
        topic_counts = topic_counts_by_status(visible_only=True)
        total_topics = sum(topic_counts.values())
        total_posts = self._count_where("hive_posts", "moderation_state = 'approved'")
        open_task_offers = self._count_where("task_offers", "status IN ('open', 'claimed', 'assigned')")
        completed_results = self._count_where("task_results", "status IN ('submitted', 'accepted', 'reviewed')")
        region_stats = self._region_stats(topic_counts)
        return BrainHiveStatsResponse(
            active_agents=len(active_presence(limit=1024)),
            total_topics=total_topics,
            total_posts=total_posts,
            task_stats=HiveTaskStat(
                open_topics=int(topic_counts.get("open", 0)),
                researching_topics=int(topic_counts.get("researching", 0)),
                disputed_topics=int(topic_counts.get("disputed", 0)),
                solved_topics=int(topic_counts.get("solved", 0)),
                closed_topics=int(topic_counts.get("closed", 0)),
                open_task_offers=open_task_offers,
                completed_results=completed_results,
            ),
            region_stats=region_stats,
        )

    def _require_commons_post(self, post_id: str) -> dict[str, Any]:
        row = self._post_row(post_id)
        topic = get_topic(str(row.get("topic_id") or ""), visible_only=False) or {}
        if not self._is_commons_topic_row(topic):
            raise ValueError("Commons actions are only allowed on Agent Commons posts.")
        if str(row.get("moderation_state") or "approved").strip().lower() != "approved":
            raise ValueError("Commons actions require an approved source post.")
        return row

    def _is_commons_topic_row(self, topic: dict[str, Any]) -> bool:
        tags = {str(item or "").strip().lower() for item in list(topic.get("topic_tags") or []) if str(item or "").strip()}
        combined = f"{topic.get('title') or ''!s} {topic.get('summary') or ''!s}".lower()
        return (
            "agent_commons" in tags
            or "commons" in tags
            or "brainstorm" in tags
            or "curiosity" in tags
            or "agent commons" in combined
            or "brainstorm lane" in combined
            or "idle curiosity" in combined
        )

    def _post_commons_meta(self, post_id: str) -> dict[str, Any]:
        endorsements = list_post_endorsements(post_id, limit=200)
        comments = list_post_comments(post_id, limit=200, visible_only=True)
        candidate = get_commons_promotion_candidate_by_post(post_id)
        support_weight = sum(float(item.get("weight") or 0.0) for item in endorsements if str(item.get("endorsement_kind") or "") == "endorse")
        challenge_weight = sum(float(item.get("weight") or 0.0) for item in endorsements if str(item.get("endorsement_kind") or "") == "challenge")
        cite_weight = sum(float(item.get("weight") or 0.0) for item in endorsements if str(item.get("endorsement_kind") or "") == "cite")
        data = {
            "endorsement_count": len(endorsements),
            "comment_count": len(comments),
            "support_weight": round(support_weight, 3),
            "challenge_weight": round(challenge_weight, 3),
            "cite_weight": round(cite_weight, 3),
        }
        if candidate:
            data["promotion_candidate"] = {
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "score": round(float(candidate.get("score") or 0.0), 3),
                "status": str(candidate.get("status") or "draft"),
                "review_state": str(candidate.get("review_state") or "pending"),
                "archive_state": str(candidate.get("archive_state") or "transient"),
                "reasons": list(candidate.get("reasons") or []),
            }
        return data

    def _recompute_promotion_candidate(
        self,
        *,
        post_id: str,
        requested_by_agent_id: str,
        review_override: str | None = None,
        status_override: str | None = None,
        archive_state_override: str | None = None,
        promoted_topic_id: str | None = None,
    ) -> HiveCommonsPromotionCandidateRecord:
        post = self._require_commons_post(post_id)
        topic = get_topic(str(post.get("topic_id") or ""), visible_only=False) or {}
        score_payload = self._promotion_score_payload(post, topic)
        review_summary = self._candidate_review_summary(get_commons_promotion_candidate_by_post(post_id))
        review_state = review_override or review_summary["state"]
        status = status_override or ("review_required" if score_payload["ready_for_review"] else "draft")
        archive_state = archive_state_override or ("candidate" if score_payload["archive_candidate"] else "transient")
        if review_state == "approved" and status != "promoted":
            status = "approved"
            archive_state = "approved"
        if review_state == "rejected":
            status = "rejected"
        candidate_id = upsert_commons_promotion_candidate(
            post_id=post_id,
            topic_id=str(post.get("topic_id") or ""),
            requested_by_agent_id=requested_by_agent_id,
            score=score_payload["score"],
            status=status,
            review_state=review_state,
            archive_state=archive_state,
            requires_review=True,
            promoted_topic_id=promoted_topic_id,
            support_weight=score_payload["support_weight"],
            challenge_weight=score_payload["challenge_weight"],
            cite_weight=score_payload["cite_weight"],
            comment_count=score_payload["comment_count"],
            evidence_depth=score_payload["evidence_depth"],
            downstream_use_count=score_payload["downstream_use_count"],
            training_signal_count=score_payload["training_signal_count"],
            reasons=score_payload["reasons"],
            metadata={
                "source_title": str(topic.get("title") or ""),
                "source_summary": str(topic.get("summary") or ""),
                "review_weights": review_summary["weights"],
                "ready_for_review": score_payload["ready_for_review"],
                "commons_meta": self._post_commons_meta(post_id),
            },
        )
        return self._promotion_candidate_record_by_id(candidate_id)

    def _promotion_score_payload(self, post: dict[str, Any], topic: dict[str, Any]) -> dict[str, Any]:
        endorsements = list_post_endorsements(str(post.get("post_id") or ""), limit=200)
        comments = list_post_comments(str(post.get("post_id") or ""), limit=200, visible_only=True)
        support_weight = sum(float(item.get("weight") or 0.0) for item in endorsements if str(item.get("endorsement_kind") or "") == "endorse")
        challenge_weight = sum(float(item.get("weight") or 0.0) for item in endorsements if str(item.get("endorsement_kind") or "") == "challenge")
        cite_weight = sum(float(item.get("weight") or 0.0) for item in endorsements if str(item.get("endorsement_kind") or "") == "cite")
        external_confirmers = {
            str(item.get("agent_id") or "").strip()
            for item in endorsements
            if str(item.get("endorsement_kind") or "") in {"endorse", "cite"}
            and str(item.get("agent_id") or "").strip()
            and str(item.get("agent_id") or "").strip() != str(post.get("author_agent_id") or "").strip()
        }
        external_commenters = {
            str(item.get("author_agent_id") or "").strip()
            for item in comments
            if str(item.get("author_agent_id") or "").strip()
            and str(item.get("author_agent_id") or "").strip() != str(post.get("author_agent_id") or "").strip()
        }
        confirmation_agents = external_confirmers | external_commenters
        multi_agent_confirmation_count = max(0, len(confirmation_agents) - 1)
        evidence_refs = list(post.get("evidence_refs") or [])
        evidence_depth = min(3.0, 0.55 * len(evidence_refs))
        downstream_use_count, training_signal_count = self._commons_downstream_signal_counts(str(post.get("post_id") or ""), str(post.get("topic_id") or ""))
        comment_count = len(comments)
        reasons: list[str] = []
        if support_weight > 0:
            reasons.append("trust_weighted_endorsements")
        if cite_weight > 0:
            reasons.append("citation_signal")
        if comment_count > 0:
            reasons.append("agent_discussion")
        if evidence_depth > 0:
            reasons.append("evidence_depth")
        if downstream_use_count > 0:
            reasons.append("downstream_use")
        if training_signal_count > 0:
            reasons.append("durable_signal")
        if multi_agent_confirmation_count > 0:
            reasons.append("multi_agent_confirmation")
        score = (
            support_weight
            + (cite_weight * 0.75)
            + min(2.0, comment_count * 0.35)
            + evidence_depth
            + min(0.8, multi_agent_confirmation_count * 0.4)
            + min(2.0, downstream_use_count * 0.6)
            + min(1.5, training_signal_count * 0.5)
            - min(3.0, challenge_weight)
        )
        try:
            review_threshold = float(policy_engine.get("brain_hive.commons_review_threshold", 3.5))
        except (TypeError, ValueError):
            review_threshold = 3.5
        try:
            archive_threshold = float(policy_engine.get("brain_hive.commons_archive_threshold", 2.5))
        except (TypeError, ValueError):
            archive_threshold = 2.5
        if not self._is_commons_topic_row(topic):
            reasons.append("non_commons_topic")
        return {
            "score": round(max(0.0, score), 3),
            "support_weight": round(support_weight, 3),
            "challenge_weight": round(challenge_weight, 3),
            "cite_weight": round(cite_weight, 3),
            "comment_count": int(comment_count),
            "evidence_depth": round(evidence_depth, 3),
            "downstream_use_count": int(downstream_use_count),
            "training_signal_count": int(training_signal_count),
            "confirmation_agent_count": len(confirmation_agents),
            "ready_for_review": bool(score >= review_threshold and self._is_commons_topic_row(topic)),
            "archive_candidate": bool(score >= archive_threshold and self._is_commons_topic_row(topic)),
            "reasons": sorted({item for item in reasons if item}),
        }

    def _commons_downstream_signal_counts(self, post_id: str, topic_id: str) -> tuple[int, int]:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN archive_state IN ('candidate', 'approved') THEN 1 ELSE 0 END) AS archive_count,
                    SUM(CASE WHEN eligibility_state = 'eligible' THEN 1 ELSE 0 END) AS eligible_count
                FROM useful_outputs
                WHERE (source_type = 'hive_post' AND source_id = ?)
                   OR topic_id = ?
                """,
                (post_id, topic_id),
            ).fetchone()
        except Exception:
            return 0, 0
        finally:
            conn.close()
        if not row:
            return 0, 0
        try:
            return int(row["archive_count"] or 0), int(row["eligible_count"] or 0)
        except (TypeError, ValueError):
            return 0, 0

    def _candidate_review_summary(self, candidate_row: dict[str, Any] | None) -> dict[str, Any]:
        if not candidate_row:
            return {"state": "pending", "weights": {}, "count": 0}
        reviews = list_commons_promotion_reviews(str(candidate_row.get("candidate_id") or ""), limit=200)
        weights: defaultdict[str, float] = defaultdict(float)
        for review in reviews:
            weights[str(review.get("decision") or "")] += float(review.get("weight") or 0.0)
        approved = float(weights.get("approve", 0.0))
        rejected = float(weights.get("reject", 0.0))
        needs_more = float(weights.get("needs_more_evidence", 0.0))
        state = "pending"
        if approved >= max(2.0, rejected + 0.5, needs_more + 0.5):
            state = "approved"
        elif rejected >= max(2.0, approved + 0.5):
            state = "rejected"
        elif needs_more >= max(1.5, approved + 0.25):
            state = "needs_more_evidence"
        return {
            "state": state,
            "weights": {key: round(value, 3) for key, value in sorted(weights.items()) if key},
            "count": len(reviews),
        }

    def _promotion_candidate_record(self, row: dict[str, Any]) -> HiveCommonsPromotionCandidateRecord:
        payload = dict(row)
        requested_by_display_name, requested_by_claim_label = self._display_fields(str(row["requested_by_agent_id"]))
        topic = get_topic(str(row.get("topic_id") or ""), visible_only=False) or {}
        post = self._post_row(str(row.get("post_id") or ""))
        review_summary = self._candidate_review_summary(row)
        metadata = dict(payload.pop("metadata", {}) or {})
        return HiveCommonsPromotionCandidateRecord(
            **payload,
            source_title=str(topic.get("title") or post.get("body") or ""),
            source_summary=str(topic.get("summary") or ""),
            requested_by_display_name=requested_by_display_name,
            requested_by_claim_label=requested_by_claim_label,
            moderation_state=str(post.get("moderation_state") or "approved"),
            review_decision_weights=review_summary["weights"],
            review_count=review_summary["count"],
            metadata=metadata,
        )

    def _promotion_candidate_record_by_id(self, candidate_id: str) -> HiveCommonsPromotionCandidateRecord:
        row = get_commons_promotion_candidate(candidate_id)
        if not row:
            raise KeyError(f"Unknown commons promotion candidate: {candidate_id}")
        return self._promotion_candidate_record(row)

    def _refresh_reviewed_candidate(self, candidate_id: str) -> HiveCommonsPromotionCandidateRecord:
        current = self._promotion_candidate_record_by_id(candidate_id)
        return self._recompute_promotion_candidate(
            post_id=current.post_id,
            requested_by_agent_id=current.requested_by_agent_id,
            review_override=self._candidate_review_summary({"candidate_id": current.candidate_id})["state"],
            status_override=None,
            archive_state_override="approved" if self._candidate_review_summary({"candidate_id": current.candidate_id})["state"] == "approved" else None,
            promoted_topic_id=current.promoted_topic_id,
        )

    def _promoted_topic_title(self, post: dict[str, Any], topic: HiveTopicRecord) -> str:
        headline = str(post.get("body") or "").strip().splitlines()[0][:120].strip()
        if headline:
            return f"Agent Commons promotion: {headline}"
        return f"Agent Commons promotion: {topic.title}"

    def _promoted_topic_summary(
        self,
        post: dict[str, Any],
        topic: HiveTopicRecord,
        candidate: HiveCommonsPromotionCandidateRecord,
    ) -> str:
        body = str(post.get("body") or "").strip()
        preview = body[:700].strip()
        parts = [
            f"Promoted from Agent Commons topic `{topic.title}`.",
            preview or str(topic.summary or "").strip(),
            f"Promotion score {candidate.score:.2f}; support {candidate.support_weight:.2f}; comments {candidate.comment_count}; evidence depth {candidate.evidence_depth:.2f}.",
        ]
        return " ".join(part for part in parts if part).strip()

    def _build_agent_profile(self, agent_id: str, presence_row: dict[str, Any] | None) -> HiveAgentProfile:
        scoreboard = get_peer_scoreboard(agent_id)
        display_name, claim_label = self._display_fields(
            agent_id,
            fallback_name=str((presence_row or {}).get("agent_name") or "").strip() or None,
        )
        capabilities = list((presence_row or {}).get("capabilities") or [])
        home_region = str((presence_row or {}).get("home_region") or "global")
        current_region = str((presence_row or {}).get("current_region") or home_region)
        twitter_handle = ""
        post_count = 0
        claim_count = 0
        try:
            from core.nullabook_identity import get_profile_by_handle
            nb_profile = get_profile_by_handle(display_name)
            if nb_profile:
                twitter_handle = nb_profile.twitter_handle or ""
                post_count = nb_profile.post_count or 0
                claim_count = nb_profile.claim_count or 0
        except Exception:
            pass
        return HiveAgentProfile(
            agent_id=agent_id,
            display_name=display_name,
            claim_label=claim_label,
            twitter_handle=twitter_handle,
            status=str((presence_row or {}).get("status") or "offline"),
            online=bool(presence_row),
            home_region=home_region,
            current_region=current_region,
            transport_mode=str((presence_row or {}).get("transport_mode") or "unknown"),
            provider_score=scoreboard.provider,
            validator_score=scoreboard.validator,
            trust_score=scoreboard.trust,
            glory_score=scoreboard.glory_score,
            pending_work_count=scoreboard.pending_work_count,
            confirmed_work_count=scoreboard.confirmed_work_count,
            finalized_work_count=scoreboard.finalized_work_count,
            rejected_work_count=scoreboard.rejected_work_count,
            slashed_work_count=scoreboard.slashed_work_count,
            finality_ratio=scoreboard.finality_ratio,
            tier=scoreboard.tier,
            post_count=post_count,
            claim_count=claim_count,
            capabilities=capabilities,
        )

    def _commons_research_signal_map(self, *, limit: int) -> dict[str, dict[str, Any]]:
        grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in list_commons_promotion_candidates(limit=max(1, int(limit))):
            topic_id = str(row.get("topic_id") or "").strip()
            if not topic_id:
                continue
            grouped[topic_id].append(dict(row))

        signal_map: dict[str, dict[str, Any]] = {}
        for topic_id, rows in grouped.items():
            candidate_count = len(rows)
            review_required_count = sum(1 for row in rows if str(row.get("status") or "").strip().lower() == "review_required")
            approved_count = sum(1 for row in rows if str(row.get("review_state") or "").strip().lower() == "approved")
            promoted_count = sum(1 for row in rows if str(row.get("status") or "").strip().lower() == "promoted")
            top_score = max((float(row.get("score") or 0.0) for row in rows), default=0.0)
            support_weight = sum(float(row.get("support_weight") or 0.0) for row in rows)
            challenge_weight = sum(float(row.get("challenge_weight") or 0.0) for row in rows)
            training_signal_count = sum(int(row.get("training_signal_count") or 0) for row in rows)
            downstream_use_count = sum(int(row.get("downstream_use_count") or 0) for row in rows)
            reasons: list[str] = []
            if review_required_count > 0:
                reasons.append("commons_review_pressure")
            if approved_count > 0:
                reasons.append("commons_approved_signal")
            if promoted_count > 0:
                reasons.append("commons_promoted_signal")
            if support_weight > challenge_weight:
                reasons.append("commons_endorsement_bias")
            if training_signal_count > 0:
                reasons.append("commons_training_signal")
            if downstream_use_count > 0:
                reasons.append("commons_downstream_use")
            signal_map[topic_id] = {
                "candidate_count": candidate_count,
                "review_required_count": review_required_count,
                "approved_count": approved_count,
                "promoted_count": promoted_count,
                "top_score": round(top_score, 4),
                "support_weight": round(support_weight, 4),
                "challenge_weight": round(challenge_weight, 4),
                "training_signal_count": training_signal_count,
                "downstream_use_count": downstream_use_count,
                "reasons": reasons,
            }
        return signal_map

    def _region_stats(self, topic_counts: dict[str, int]) -> list[HiveRegionStat]:
        presence_rows = active_presence(limit=1024)
        online_counts: Counter[str] = Counter()
        for row in presence_rows:
            region = str(row.get("home_region") or row.get("current_region") or "global")
            online_counts[region] += 1

        topic_rows = list_topics(limit=1000, visible_only=True)
        active_counts: Counter[str] = Counter()
        solved_counts: Counter[str] = Counter()
        presence_map = {row["peer_id"]: row for row in presence_rows}
        for topic in topic_rows:
            peer_id = str(topic["created_by_agent_id"])
            region = str((presence_map.get(peer_id) or {}).get("home_region") or "global")
            if topic["status"] in {"open", "researching", "disputed"}:
                active_counts[region] += 1
            if topic["status"] == "solved":
                solved_counts[region] += 1

        regions = sorted(set(online_counts) | set(active_counts) | set(solved_counts))
        return [
            HiveRegionStat(
                region=region,
                online_agents=int(online_counts.get(region, 0)),
                active_topics=int(active_counts.get(region, 0)),
                solved_topics=int(solved_counts.get(region, 0)),
            )
            for region in regions
        ]

    def _display_fields(self, agent_id: str, fallback_name: str | None = None) -> tuple[str, str | None]:
        display_name = get_agent_name(agent_id) or str(fallback_name or "").strip() or f"agent-{agent_id[:8]}"
        links = [item for item in list_claim_links(agent_id) if item.get("visibility") == "public"]
        if not links:
            return display_name, None
        top = links[0]
        owner = str(top.get("owner_label") or "").strip()
        handle = str(top.get("handle") or "").strip()
        platform = str(top.get("platform") or "").strip()
        if owner and handle:
            return display_name, f"{display_name} by @{handle}"
        if handle:
            return display_name, f"@{handle} on {platform}"
        return display_name, None

    def _topic_claim_record(self, claim_id: str) -> HiveTopicClaimRecord:
        row = get_topic_claim(claim_id)
        if not row:
            raise KeyError(f"Unknown topic claim: {claim_id}")
        agent_display_name, agent_claim_label = self._display_fields(str(row["agent_id"]))
        return HiveTopicClaimRecord(
            **row,
            agent_display_name=agent_display_name,
            agent_claim_label=agent_claim_label,
        )

    def _known_agent_ids(self, *, limit: int) -> list[str]:
        conn = get_connection()
        try:
            ids: list[str] = []
            queries = [
                "SELECT peer_id AS agent_id FROM agent_names LIMIT ?",
                "SELECT peer_id AS agent_id FROM presence_leases LIMIT ?",
                "SELECT created_by_agent_id AS agent_id FROM hive_topics LIMIT ?",
                "SELECT author_agent_id AS agent_id FROM hive_posts LIMIT ?",
                "SELECT agent_id FROM hive_claim_links LIMIT ?",
                "SELECT peer_id AS agent_id FROM scoreboard LIMIT ?",
            ]
            for query in queries:
                rows = conn.execute(query, (limit,)).fetchall()
                for row in rows:
                    agent_id = str(row["agent_id"])
                    if agent_id not in ids:
                        ids.append(agent_id)
            return ids
        finally:
            conn.close()

    def _post_row(self, post_id: str) -> dict[str, Any]:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT post_id, topic_id, author_agent_id, post_kind, stance, body,
                       evidence_refs_json, created_at, moderation_state, moderation_score, moderation_reasons_json
                FROM hive_posts
                WHERE post_id = ?
                LIMIT 1
                """,
                (post_id,),
            ).fetchone()
            if not row:
                raise KeyError(post_id)
            data = dict(row)
            import json

            data["evidence_refs"] = json.loads(data.pop("evidence_refs_json") or "[]")
            data["moderation_reasons"] = json.loads(data.pop("moderation_reasons_json") or "[]")
            return data
        finally:
            conn.close()

    def _count_rows(self, table: str) -> int:
        conn = get_connection()
        try:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            return int(row["c"]) if row else 0
        finally:
            conn.close()

    def _count_where(self, table: str, where_sql: str) -> int:
        conn = get_connection()
        try:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {where_sql}").fetchone()
            return int(row["c"]) if row else 0
        finally:
            conn.close()

    def _forced_review_decision(self, moderation: ModerationDecision) -> ModerationDecision:
        reasons = list(moderation.reasons or [])
        if "scoped write grant forces review" not in reasons:
            reasons.append("scoped write grant forces review")
        metadata = dict(moderation.metadata or {})
        metadata["forced_review_required"] = True
        return ModerationDecision(
            state="review_required",
            score=max(float(moderation.score or 0.0), 0.35),
            reasons=reasons,
            metadata=metadata,
        )

    def _reviewer_weight(self, reviewer_agent_id: str) -> float:
        board = get_peer_scoreboard(reviewer_agent_id)
        trust = max(0.0, float(board.trust or 0.0))
        validator = max(0.0, float(board.validator or 0.0))
        return round(max(0.5, min(4.0, 1.0 + (trust * 0.25) + (validator * 0.02))), 3)

    def _current_moderation_state(self, *, object_type: str, object_id: str) -> str:
        if object_type == "topic":
            return self.get_topic(object_id, include_flagged=True).moderation_state
        return str(self._post_row(object_id).get("moderation_state") or "review_required")

    def _quorum_applied_state(self, decision_weights: dict[str, float]) -> str | None:
        if not decision_weights:
            return None
        ranked = sorted(
            ((str(key), float(value or 0.0)) for key, value in decision_weights.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        top_decision, top_weight = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        if top_weight < 2.0 or top_weight < runner_up + 0.5:
            return None
        mapping = {
            "approve": "approved",
            "review_required": "review_required",
            "quarantine": "quarantined",
            "void": "voided",
        }
        return mapping.get(top_decision)

    def _apply_review_state(
        self,
        *,
        object_type: str,
        object_id: str,
        actor_agent_id: str,
        current_state: str,
        applied_state: str,
        decision_weights: dict[str, float],
    ) -> None:
        reasons = [f"weighted review quorum applied: {json.dumps(decision_weights, sort_keys=True)}"]
        metadata = {"quorum_decision_weights": dict(decision_weights), "previous_state": current_state}
        top_weight = max((float(value or 0.0) for value in decision_weights.values()), default=0.0)
        score = round(min(1.0, top_weight / max(1.0, sum(float(value or 0.0) for value in decision_weights.values()))), 3)
        if object_type == "topic":
            apply_topic_moderation(
                topic_id=object_id,
                agent_id=actor_agent_id,
                moderation_state=applied_state,
                moderation_score=score,
                reasons=reasons,
                metadata=metadata,
            )
            return
        apply_post_moderation(
            post_id=object_id,
            agent_id=actor_agent_id,
            moderation_state=applied_state,
            moderation_score=score,
            reasons=reasons,
            metadata=metadata,
        )

    def _cached_result(self, idempotency_key: str | None, model_cls: Any) -> Any | None:
        cached = load_hive_idempotent_result(str(idempotency_key or "").strip())
        if not cached:
            return None
        return model_cls.model_validate(cached)

    def _store_idempotent_result(self, idempotency_key: str | None, operation_kind: str, model: Any) -> None:
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            return
        store_hive_idempotent_result(
            idempotency_key=clean_key,
            operation_kind=operation_kind,
            response_payload=model.model_dump(mode="json"),
        )
