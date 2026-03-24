from __future__ import annotations

from typing import Any

from . import writes as public_hive_writes


class PublicHiveBridgeTopicWritesMixin:
    def _topic_result_settlement_helpers(
        self,
        *,
        topic_id: str,
        claim_id: str,
    ) -> list[str]:
        return public_hive_writes.topic_result_settlement_helpers(self, topic_id=topic_id, claim_id=claim_id)

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
