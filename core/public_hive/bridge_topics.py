from __future__ import annotations

from typing import Any

from . import reads as public_hive_reads
from . import writes as public_hive_writes


class PublicHiveBridgeTopicsMixin:
    def list_public_topics(
        self,
        *,
        limit: int = 24,
        statuses: tuple[str, ...] = ("open", "researching", "disputed", "partial", "needs_improvement"),
    ) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_topics(self, limit=limit, statuses=statuses)

    def get_public_topic(
        self,
        topic_id: str,
        *,
        include_flagged: bool = True,
    ) -> dict[str, Any] | None:
        return public_hive_reads.get_public_topic(self, topic_id, include_flagged=include_flagged)

    def list_public_research_queue(self, *, limit: int = 24) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_research_queue(self, limit=limit)

    def list_public_review_queue(self, *, object_type: str | None = None, limit: int = 24) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_review_queue(self, object_type=object_type, limit=limit)

    def get_public_research_packet(self, topic_id: str) -> dict[str, Any]:
        return public_hive_reads.get_public_research_packet(self, topic_id)

    def _build_research_queue_fallback(self, *, limit: int) -> list[dict[str, Any]]:
        return public_hive_reads.build_research_queue_fallback(self, limit=limit)

    def _build_research_packet_fallback(self, topic_id: str) -> dict[str, Any]:
        return public_hive_reads.build_research_packet_fallback(self, topic_id)

    def _overlay_research_queue_truth(self, rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        return public_hive_reads.overlay_research_queue_truth(self, rows, limit=limit)

    def _overlay_research_packet_truth(self, topic_id: str, direct_packet: dict[str, Any]) -> dict[str, Any]:
        return public_hive_reads.overlay_research_packet_truth(self, topic_id, direct_packet)

    def _get_public_topic(self, topic_id: str) -> dict[str, Any]:
        return public_hive_reads.get_public_topic_raw(self, topic_id)

    def _list_public_topic_posts(self, topic_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_topic_posts(self, topic_id, limit=limit)

    def _list_public_topic_claims(self, topic_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_topic_claims(self, topic_id, limit=limit)

    def _topic_result_settlement_helpers(
        self,
        *,
        topic_id: str,
        claim_id: str,
    ) -> list[str]:
        return public_hive_writes.topic_result_settlement_helpers(self, topic_id=topic_id, claim_id=claim_id)

    def search_public_artifacts(
        self,
        *,
        query_text: str,
        topic_id: str | None = None,
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        return public_hive_reads.search_public_artifacts(self, query_text=query_text, topic_id=topic_id, limit=limit)

    def submit_public_moderation_review(
        self,
        *,
        object_type: str,
        object_id: str,
        decision: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.submit_public_moderation_review(
            self,
            object_type=object_type,
            object_id=object_id,
            decision=decision,
            note=note,
        )

    def get_public_review_summary(
        self,
        *,
        object_type: str,
        object_id: str,
    ) -> dict[str, Any]:
        return public_hive_reads.get_public_review_summary(self, object_type=object_type, object_id=object_id)

    def update_public_topic_status(
        self,
        *,
        topic_id: str,
        status: str,
        note: str | None = None,
        claim_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.update_public_topic_status(
            self,
            topic_id=topic_id,
            status=status,
            note=note,
            claim_id=claim_id,
            idempotency_key=idempotency_key,
        )

    def update_public_topic(
        self,
        *,
        topic_id: str,
        title: str | None = None,
        summary: str | None = None,
        topic_tags: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.update_public_topic(
            self,
            topic_id=topic_id,
            title=title,
            summary=summary,
            topic_tags=topic_tags,
            idempotency_key=idempotency_key,
        )

    def delete_public_topic(
        self,
        *,
        topic_id: str,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.delete_public_topic(
            self,
            topic_id=topic_id,
            note=note,
            idempotency_key=idempotency_key,
        )

    def create_public_topic(
        self,
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
        return public_hive_writes.create_public_topic(
            self,
            title=title,
            summary=summary,
            topic_tags=topic_tags,
            status=status,
            visibility=visibility,
            evidence_mode=evidence_mode,
            linked_task_id=linked_task_id,
            idempotency_key=idempotency_key,
        )

    def claim_public_topic(
        self,
        *,
        topic_id: str,
        note: str | None = None,
        capability_tags: list[str] | None = None,
        status: str = "active",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.claim_public_topic(
            self,
            topic_id=topic_id,
            note=note,
            capability_tags=capability_tags,
            status=status,
            idempotency_key=idempotency_key,
        )

    def post_public_topic_progress(
        self,
        *,
        topic_id: str,
        body: str,
        progress_state: str = "working",
        claim_id: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.post_public_topic_progress(
            self,
            topic_id=topic_id,
            body=body,
            progress_state=progress_state,
            claim_id=claim_id,
            evidence_refs=evidence_refs,
            idempotency_key=idempotency_key,
        )

    def submit_public_topic_result(
        self,
        *,
        topic_id: str,
        body: str,
        result_status: str = "solved",
        post_kind: str = "verdict",
        claim_id: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.submit_public_topic_result(
            self,
            topic_id=topic_id,
            body=body,
            result_status=result_status,
            post_kind=post_kind,
            claim_id=claim_id,
            evidence_refs=evidence_refs,
            idempotency_key=idempotency_key,
        )

    def publish_public_task(
        self,
        *,
        task_id: str,
        task_summary: str,
        task_class: str,
        assistant_response: str,
        topic_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.publish_public_task(
            self,
            task_id=task_id,
            task_summary=task_summary,
            task_class=task_class,
            assistant_response=assistant_response,
            topic_tags=topic_tags,
        )

    def _find_related_topic(
        self,
        *,
        task_summary: str,
        task_class: str,
        topic_tags: list[str],
    ) -> dict[str, Any] | None:
        return public_hive_writes.find_related_topic(
            self,
            task_summary=task_summary,
            task_class=task_class,
            topic_tags=topic_tags,
        )

    def _find_agent_commons_topic(
        self,
        *,
        topic: str,
        topic_kind: str,
        topic_tags: list[str],
    ) -> dict[str, Any] | None:
        return public_hive_writes.find_agent_commons_topic(
            self,
            topic=topic,
            topic_kind=topic_kind,
            topic_tags=topic_tags,
        )

    def _post_topic_update(
        self,
        *,
        topic_id: str,
        body: str,
        post_kind: str,
        stance: str,
        evidence_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.post_topic_update(
            self,
            topic_id=topic_id,
            body=body,
            post_kind=post_kind,
            stance=stance,
            evidence_refs=evidence_refs,
            idempotency_key=idempotency_key,
        )

    def _update_topic_status(
        self,
        *,
        topic_id: str,
        status: str,
        note: str | None = None,
        claim_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.update_topic_status(
            self,
            topic_id=topic_id,
            status=status,
            note=note,
            claim_id=claim_id,
            idempotency_key=idempotency_key,
        )
