from __future__ import annotations

from typing import Any

from . import writes as public_hive_writes


class PublicHiveBridgeTopicPostWritesMixin:
    def _topic_result_settlement_helpers(
        self,
        *,
        topic_id: str,
        claim_id: str,
    ) -> list[str]:
        return public_hive_writes.topic_result_settlement_helpers(self, topic_id=topic_id, claim_id=claim_id)

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
