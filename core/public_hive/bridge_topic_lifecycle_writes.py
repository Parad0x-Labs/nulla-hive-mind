from __future__ import annotations

from typing import Any

from . import writes as public_hive_writes


class PublicHiveBridgeTopicLifecycleWritesMixin:
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
