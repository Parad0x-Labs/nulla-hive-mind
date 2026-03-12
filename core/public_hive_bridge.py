from __future__ import annotations

import json
import os
import re
import shlex
import ssl
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from core import audit_logger
from core.api_write_auth import build_signed_write_envelope
from core.brain_hive_models import (
    HivePostCreateRequest,
    HiveTopicClaimRequest,
    HiveTopicCreateRequest,
    HiveTopicStatusUpdateRequest,
)
from core.meet_and_greet_models import PresenceUpsertRequest
from core.privacy_guard import text_privacy_risks
from core.runtime_paths import CONFIG_HOME_DIR, PROJECT_ROOT, config_path
from network.signer import get_local_peer_id


_PLACEHOLDER_TOKEN_RE = re.compile(r"(?:replace|set|change).*(?:token|secret)", re.IGNORECASE)


@dataclass(frozen=True)
class PublicHiveBridgeConfig:
    enabled: bool = True
    meet_seed_urls: tuple[str, ...] = ()
    topic_target_url: str | None = None
    home_region: str = "global"
    request_timeout_seconds: int = 8
    auth_token: str | None = None
    auth_tokens_by_base_url: dict[str, str] = field(default_factory=dict)
    write_grants_by_base_url: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    tls_ca_file: str | None = None
    tls_insecure_skip_verify: bool = False


class PublicHiveBridge:
    def __init__(
        self,
        config: PublicHiveBridgeConfig | None = None,
        *,
        urlopen: Any | None = None,
    ) -> None:
        self.config = config or load_public_hive_bridge_config()
        self._urlopen = urlopen or urllib.request.urlopen

    def enabled(self) -> bool:
        return bool(self.config.enabled and self.config.meet_seed_urls)

    def auth_configured(self) -> bool:
        return public_hive_has_auth(self.config)

    def write_enabled(self) -> bool:
        return public_hive_write_enabled(self.config)

    def sync_presence(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str = "idle",
        transport_mode: str = "nulla_agent",
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"ok": False, "status": "disabled", "posted_to": [], "errors": []}
        if not self.write_enabled():
            return {"ok": False, "status": "missing_auth", "posted_to": [], "errors": ["public hive auth is not configured"]}

        request = self._presence_request(
            agent_name=agent_name,
            capabilities=capabilities,
            status=status,
            transport_mode=transport_mode,
        )
        return self._post_many(
            "/v1/presence/register",
            payload=request.model_dump(mode="json"),
            base_urls=self.config.meet_seed_urls,
        )

    def heartbeat_presence(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str = "idle",
        transport_mode: str = "nulla_agent",
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"ok": False, "status": "disabled", "posted_to": [], "errors": []}
        if not self.write_enabled():
            return {"ok": False, "status": "missing_auth", "posted_to": [], "errors": ["public hive auth is not configured"]}
        request = self._presence_request(
            agent_name=agent_name,
            capabilities=capabilities,
            status=status,
            transport_mode=transport_mode,
        )
        return self._post_many(
            "/v1/presence/heartbeat",
            payload=request.model_dump(mode="json"),
            base_urls=self.config.meet_seed_urls,
        )

    def list_public_topics(
        self,
        *,
        limit: int = 24,
        statuses: tuple[str, ...] = ("open", "researching", "disputed"),
    ) -> list[dict[str, Any]]:
        if not self.enabled() or not self.config.topic_target_url:
            return []
        try:
            result = self._get_json(
                str(self.config.topic_target_url),
                f"/v1/hive/topics?limit={max(1, min(int(limit), 100))}",
            )
        except Exception:
            return []
        wanted_statuses = {str(item or "").strip().lower() for item in statuses if str(item or "").strip()}
        rows = list(result if isinstance(result, list) else [])
        out: list[dict[str, Any]] = []
        for row in rows:
            status = str((row or {}).get("status") or "").strip().lower()
            if wanted_statuses and status not in wanted_statuses:
                continue
            out.append(dict(row or {}))
        return out

    def list_public_research_queue(self, *, limit: int = 24) -> list[dict[str, Any]]:
        if not self.enabled() or not self.config.topic_target_url:
            return []
        try:
            result = self._get_json(
                str(self.config.topic_target_url),
                f"/v1/hive/research-queue?limit={max(1, min(int(limit), 100))}",
            )
        except Exception as exc:
            if _route_missing(exc):
                return self._build_research_queue_fallback(limit=max(1, min(int(limit), 100)))
            return []
        return [dict(item or {}) for item in list(result or [])]

    def list_public_review_queue(self, *, object_type: str | None = None, limit: int = 24) -> list[dict[str, Any]]:
        if not self.enabled() or not self.config.topic_target_url:
            return []
        route = f"/v1/hive/review-queue?limit={max(1, min(int(limit), 100))}"
        if str(object_type or "").strip():
            route += f"&object_type={quote(str(object_type or '').strip())}"
        try:
            result = self._get_json(str(self.config.topic_target_url), route)
        except Exception:
            return []
        return [dict(item or {}) for item in list(result or [])]

    def get_public_research_packet(self, topic_id: str) -> dict[str, Any]:
        if not self.enabled() or not self.config.topic_target_url:
            return {}
        clean_topic_id = str(topic_id or "").strip()
        if not clean_topic_id:
            return {}
        try:
            result = self._get_json(
                str(self.config.topic_target_url),
                f"/v1/hive/topics/{clean_topic_id}/research-packet",
            )
        except Exception as exc:
            if _route_missing(exc):
                return self._build_research_packet_fallback(clean_topic_id)
            return {}
        return dict(result or {})

    def _build_research_queue_fallback(self, *, limit: int) -> list[dict[str, Any]]:
        from core.brain_hive_research import build_research_queue_entry

        topics = self.list_public_topics(limit=max(32, int(limit) * 2))
        queue_rows: list[dict[str, Any]] = []
        for topic in topics:
            status = str(topic.get("status") or "").strip().lower()
            if status not in {"open", "researching", "disputed"}:
                continue
            topic_id = str(topic.get("topic_id") or "").strip()
            if not topic_id:
                continue
            posts = self._list_public_topic_posts(topic_id, limit=120)
            claims = self._list_public_topic_claims(topic_id, limit=48)
            row = build_research_queue_entry(topic=topic, posts=posts, claims=claims)
            row["claims"] = [dict(item or {}) for item in claims]
            row["updated_at"] = str(topic.get("updated_at") or "")
            row["created_at"] = str(topic.get("created_at") or "")
            row["compat_fallback"] = True
            queue_rows.append(row)
        queue_rows.sort(
            key=lambda row: (
                float(row.get("research_priority") or 0.0),
                -int(row.get("active_claim_count") or 0),
                str(row.get("updated_at") or ""),
            ),
            reverse=True,
        )
        return queue_rows[: max(1, min(int(limit), 100))]

    def _build_research_packet_fallback(self, topic_id: str) -> dict[str, Any]:
        from core.brain_hive_research import build_topic_research_packet

        topic = self._get_public_topic(topic_id)
        if not topic:
            return {}
        posts = self._list_public_topic_posts(topic_id, limit=400)
        claims = self._list_public_topic_claims(topic_id, limit=200)
        packet = build_topic_research_packet(topic=topic, posts=posts, claims=claims)
        packet["compat_fallback"] = True
        return packet

    def _get_public_topic(self, topic_id: str) -> dict[str, Any]:
        clean_topic_id = str(topic_id or "").strip()
        if not clean_topic_id or not self.config.topic_target_url:
            return {}
        try:
            result = self._get_json(
                str(self.config.topic_target_url),
                f"/v1/hive/topics/{clean_topic_id}",
            )
        except Exception:
            return {}
        return dict(result or {})

    def _list_public_topic_posts(self, topic_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        clean_topic_id = str(topic_id or "").strip()
        if not clean_topic_id or not self.config.topic_target_url:
            return []
        try:
            result = self._get_json(
                str(self.config.topic_target_url),
                f"/v1/hive/topics/{clean_topic_id}/posts?limit={max(1, min(int(limit), 400))}",
            )
        except Exception:
            return []
        return [dict(item or {}) for item in list(result or [])]

    def _list_public_topic_claims(self, topic_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        clean_topic_id = str(topic_id or "").strip()
        if not clean_topic_id or not self.config.topic_target_url:
            return []
        try:
            result = self._get_json(
                str(self.config.topic_target_url),
                f"/v1/hive/topics/{clean_topic_id}/claims?limit={max(1, min(int(limit), 400))}",
            )
        except Exception:
            return []
        return [dict(item or {}) for item in list(result or [])]

    def search_public_artifacts(
        self,
        *,
        query_text: str,
        topic_id: str | None = None,
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        if not self.enabled() or not self.config.topic_target_url:
            return []
        clean_query = " ".join(str(query_text or "").split()).strip()
        if not clean_query:
            return []
        route = f"/v1/hive/artifacts/search?q={quote(clean_query)}&limit={max(1, min(int(limit), 100))}"
        if str(topic_id or "").strip():
            route += f"&topic_id={quote(str(topic_id or '').strip())}"
        try:
            result = self._get_json(str(self.config.topic_target_url), route)
        except Exception:
            return []
        return [dict(item or {}) for item in list(result or [])]

    def submit_public_moderation_review(
        self,
        *,
        object_type: str,
        object_id: str,
        decision: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        if not self.enabled() or not self.config.topic_target_url:
            return {"ok": False, "status": "disabled"}
        if not self.write_enabled():
            return {"ok": False, "status": "missing_auth"}
        payload = {
            "object_type": str(object_type or "").strip(),
            "object_id": str(object_id or "").strip(),
            "reviewer_agent_id": get_local_peer_id(),
            "decision": str(decision or "").strip(),
            "note": " ".join(str(note or "").split()).strip()[:512] or None,
        }
        result = self._post_json(str(self.config.topic_target_url), "/v1/hive/moderation/reviews", payload)
        return {"ok": True, **result}

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
        if not self.enabled():
            return {"ok": False, "status": "disabled"}
        if not self.config.topic_target_url:
            return {"ok": False, "status": "missing_target"}
        if not self.write_enabled():
            return {"ok": False, "status": "missing_auth"}

        clean_title = " ".join(str(title or "").split()).strip()[:180]
        clean_summary = " ".join(str(summary or "").split()).strip()[:4000]
        if not clean_title or not clean_summary:
            return {"ok": False, "status": "empty_topic"}
        if text_privacy_risks(f"{clean_title}\n{clean_summary}"):
            return {"ok": False, "status": "privacy_blocked_topic"}

        request = HiveTopicCreateRequest(
            created_by_agent_id=get_local_peer_id(),
            title=clean_title,
            summary=clean_summary,
            topic_tags=[str(item).strip()[:64] for item in list(topic_tags or []) if str(item).strip()][:16],
            status=str(status or "open").strip() or "open",
            visibility=str(visibility or "read_public").strip() or "read_public",
            evidence_mode=str(evidence_mode or "candidate_only").strip() or "candidate_only",
            linked_task_id=str(linked_task_id or "").strip()[:256] or None,
            idempotency_key=str(idempotency_key or "").strip()[:128] or None,
        )
        topic_result = self._post_json(
            str(self.config.topic_target_url),
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
        self,
        *,
        topic_id: str,
        note: str | None = None,
        capability_tags: list[str] | None = None,
        status: str = "active",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"ok": False, "status": "disabled"}
        if not self.config.topic_target_url:
            return {"ok": False, "status": "missing_target"}
        if not self.write_enabled():
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
        claim_result = self._post_json(
            str(self.config.topic_target_url),
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
        self,
        *,
        topic_id: str,
        body: str,
        progress_state: str = "working",
        claim_id: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"ok": False, "status": "disabled"}
        if not self.config.topic_target_url:
            return {"ok": False, "status": "missing_target"}
        if not self.write_enabled():
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
        post_result = self._post_topic_update(
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
        self,
        *,
        topic_id: str,
        body: str,
        result_status: str = "solved",
        claim_id: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"ok": False, "status": "disabled"}
        if not self.config.topic_target_url:
            return {"ok": False, "status": "missing_target"}
        if not self.write_enabled():
            return {"ok": False, "status": "missing_auth"}

        clean_topic_id = str(topic_id or "").strip()
        clean_body = str(body or "").strip()
        clean_status = str(result_status or "solved").strip() or "solved"
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
        post_result = self._post_topic_update(
            topic_id=clean_topic_id,
            body=clean_body,
            post_kind="verdict",
            stance="summarize",
            evidence_refs=refs,
            idempotency_key=(str(idempotency_key or "").strip() + ":post")[:128] if idempotency_key else None,
        )
        status_result = self._update_topic_status(
            topic_id=clean_topic_id,
            status=clean_status,
            note=clean_body[:240],
            claim_id=claim_id,
            idempotency_key=(str(idempotency_key or "").strip() + ":status")[:128] if idempotency_key else None,
        )
        return {
            "ok": bool(post_result.get("post_id")),
            "status": "result_submitted" if post_result.get("post_id") else "result_failed",
            "topic_id": clean_topic_id,
            "post_id": str(post_result.get("post_id") or ""),
            "post_result": post_result,
            "topic_result": status_result,
        }

    def publish_public_task(
        self,
        *,
        task_id: str,
        task_summary: str,
        task_class: str,
        assistant_response: str,
        topic_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"ok": False, "status": "disabled"}
        if not self.config.topic_target_url:
            return {"ok": False, "status": "missing_target"}
        if not self.write_enabled():
            return {"ok": False, "status": "missing_auth"}

        redacted_summary = " ".join(str(task_summary or "").split()).strip()[:320]
        if not redacted_summary:
            return {"ok": False, "status": "empty_summary"}

        resolved_tags = _topic_tags(task_class=task_class, text=redacted_summary, extra=topic_tags)
        post_body = _public_post_body(assistant_response) or _fallback_public_post_body(
            task_summary=redacted_summary,
            task_class=task_class,
        )
        if post_body and not text_privacy_risks(post_body):
            related_topic = self._find_related_topic(
                task_summary=redacted_summary,
                task_class=task_class,
                topic_tags=resolved_tags,
            )
            if related_topic:
                try:
                    post_result = self._post_topic_update(
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

        title = _task_title(redacted_summary)
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
        topic_result = self._post_json(
            str(self.config.topic_target_url),
            "/v1/hive/topics",
            topic.model_dump(mode="json"),
        )
        topic_id = str(topic_result.get("topic_id") or "")
        if not topic_id:
            return {"ok": False, "status": "topic_failed", "result": topic_result}

        if post_body and not text_privacy_risks(post_body):
            try:
                post_result = self._post_topic_update(
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
        self,
        *,
        topic: str,
        topic_kind: str,
        summary: str,
        public_body: str,
        topic_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"ok": False, "status": "disabled"}
        if not self.config.topic_target_url:
            return {"ok": False, "status": "missing_target"}
        if not self.write_enabled():
            return {"ok": False, "status": "missing_auth"}

        clean_topic = " ".join(str(topic or "").split()).strip()[:140]
        clean_summary = " ".join(str(summary or "").split()).strip()[:600]
        if not clean_topic or not clean_summary:
            return {"ok": False, "status": "empty_commons_update"}

        resolved_tags = _topic_tags(
            task_class="agent_commons",
            text=f"{clean_topic} {clean_summary}",
            extra=["agent_commons", "commons", "brainstorm", str(topic_kind or "").strip().lower(), *list(topic_tags or [])],
        )
        related_topic = self._find_agent_commons_topic(
            topic=clean_topic,
            topic_kind=topic_kind,
            topic_tags=resolved_tags,
        )
        body = _commons_post_body(topic=clean_topic, summary=clean_summary, public_body=public_body)
        if text_privacy_risks(body):
            return {"ok": False, "status": "privacy_blocked_post"}

        if related_topic:
            post_result = self._post_topic_update(
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

        title = _commons_topic_title(clean_topic)
        topic_summary = _commons_topic_summary(topic=clean_topic, summary=clean_summary)
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
        topic_result = self._post_json(
            str(self.config.topic_target_url),
            "/v1/hive/topics",
            topic_request.model_dump(mode="json"),
        )
        topic_id = str(topic_result.get("topic_id") or "")
        if not topic_id:
            return {"ok": False, "status": "topic_failed", "result": topic_result}
        post_result = self._post_topic_update(
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

    def _presence_request(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str,
        transport_mode: str,
    ) -> PresenceUpsertRequest:
        return PresenceUpsertRequest(
            agent_id=get_local_peer_id(),
            agent_name=str(agent_name or "").strip()[:64] or None,
            status=_normalize_presence_status(status),
            capabilities=[str(item).strip()[:64] for item in capabilities if str(item).strip()][:32],
            home_region=str(self.config.home_region or "global")[:64] or "global",
            current_region=str(self.config.home_region or "global")[:64] or "global",
            transport_mode=str(transport_mode or "nulla_agent")[:64] or "nulla_agent",
            trust_score=0.5,
            timestamp=datetime.now(timezone.utc),
            lease_seconds=300,
        )

    def _post_many(
        self,
        route: str,
        *,
        payload: dict[str, Any],
        base_urls: tuple[str, ...],
    ) -> dict[str, Any]:
        posted_to: list[str] = []
        errors: list[str] = []
        for base_url in base_urls:
            try:
                self._post_json(base_url, route, payload)
                posted_to.append(base_url.rstrip("/"))
            except Exception as exc:
                errors.append(f"{base_url.rstrip('/')}: {exc}")
        return {"ok": bool(posted_to), "status": "posted" if posted_to else "failed", "posted_to": posted_to, "errors": errors}

    def _get_json(self, base_url: str, route: str) -> Any:
        target_path = route if str(route).startswith("/") else f"/{route}"
        url = f"{str(base_url).rstrip('/')}{target_path}"
        request = urllib.request.Request(url, method="GET")
        request.add_header("Content-Type", "application/json")
        auth_token = self._auth_token_for_url(base_url)
        if auth_token:
            request.add_header("X-Nulla-Meet-Token", auth_token)
        context = self._ssl_context_for_url(url)
        with self._urlopen(request, timeout=self.config.request_timeout_seconds, context=context) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise ValueError(str(payload.get("error") or f"Meet read failed for {url}"))
        return payload.get("result")

    def _post_json(self, base_url: str, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        target_path = route.rstrip("/") or "/"
        signed_payload = dict(payload or {})
        write_grant = self._write_grant_for_request(base_url, target_path)
        if isinstance(write_grant, dict) and write_grant:
            signed_payload["write_grant"] = write_grant
        envelope = build_signed_write_envelope(target_path=target_path, payload=signed_payload)
        raw = json.dumps(envelope, sort_keys=True).encode("utf-8")
        url = f"{str(base_url).rstrip('/')}{target_path}"
        request = urllib.request.Request(url, data=raw, method="POST")
        request.add_header("Content-Type", "application/json")
        auth_token = self._auth_token_for_url(base_url)
        if auth_token:
            request.add_header("X-Nulla-Meet-Token", auth_token)
        context = self._ssl_context_for_url(url)
        try:
            with self._urlopen(request, timeout=self.config.request_timeout_seconds, context=context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ValueError(_http_error_detail(exc, fallback=f"Meet write failed for {url}")) from exc
        if not payload.get("ok"):
            raise ValueError(str(payload.get("error") or f"Meet write failed for {url}"))
        return dict(payload.get("result") or {})

    def _find_related_topic(
        self,
        *,
        task_summary: str,
        task_class: str,
        topic_tags: list[str],
    ) -> dict[str, Any] | None:
        best_topic: dict[str, Any] | None = None
        best_score = 0
        local_peer_id = get_local_peer_id()
        for topic in self.list_public_topics(limit=24):
            if str(topic.get("created_by_agent_id") or "") == local_peer_id:
                continue
            score = _topic_match_score(
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

    def _find_agent_commons_topic(
        self,
        *,
        topic: str,
        topic_kind: str,
        topic_tags: list[str],
    ) -> dict[str, Any] | None:
        best_topic: dict[str, Any] | None = None
        best_score = 0
        wanted_tokens = set(_content_tokens(topic))
        wanted_kind = str(topic_kind or "").strip().lower()
        for candidate in self.list_public_topics(limit=48, statuses=("open", "researching", "disputed", "solved")):
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
            candidate_tokens = set(_content_tokens(title) + _content_tokens(summary))
            score += min(4, len(wanted_tokens & candidate_tokens))
            if title.lower() == _commons_topic_title(topic).lower():
                score += 3
            if score > best_score:
                best_score = score
                best_topic = candidate
        if best_score >= 3:
            return best_topic
        return None

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
        post = HivePostCreateRequest(
            topic_id=str(topic_id or "").strip(),
            author_agent_id=get_local_peer_id(),
            post_kind=str(post_kind or "analysis").strip() or "analysis",
            stance=str(stance or "support").strip() or "support",
            body=str(body or "").strip(),
            evidence_refs=[dict(item) for item in list(evidence_refs or []) if isinstance(item, dict)],
            idempotency_key=str(idempotency_key or "").strip()[:128] or None,
        )
        return self._post_json(
            str(self.config.topic_target_url),
            "/v1/hive/posts",
            post.model_dump(mode="json"),
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
        request = HiveTopicStatusUpdateRequest(
            topic_id=str(topic_id or "").strip(),
            updated_by_agent_id=get_local_peer_id(),
            status=str(status or "researching").strip() or "researching",
            note=" ".join(str(note or "").split()).strip()[:512] or None,
            claim_id=str(claim_id or "").strip() or None,
            idempotency_key=str(idempotency_key or "").strip()[:128] or None,
        )
        return self._post_json(
            str(self.config.topic_target_url),
            "/v1/hive/topic-status",
            request.model_dump(mode="json"),
        )

    def _auth_token_for_url(self, url: str) -> str | None:
        normalized = _normalize_base_url(url)
        token = self.config.auth_tokens_by_base_url.get(normalized)
        if token:
            return token
        return self.config.auth_token

    def _write_grant_for_request(self, base_url: str, route: str) -> dict[str, Any] | None:
        normalized = _normalize_base_url(base_url)
        scoped_grants = dict(self.config.write_grants_by_base_url.get(normalized) or {})
        grant = scoped_grants.get(route.rstrip("/") or "/")
        return dict(grant) if isinstance(grant, dict) else None

    def _ssl_context_for_url(self, url: str) -> ssl.SSLContext | None:
        if not str(url).lower().startswith("https://"):
            return None
        if self.config.tls_insecure_skip_verify:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        if self.config.tls_ca_file:
            return ssl.create_default_context(cafile=self.config.tls_ca_file)
        return ssl.create_default_context()


def load_public_hive_bridge_config() -> PublicHiveBridgeConfig:
    ensure_public_hive_agent_bootstrap()
    raw = _load_agent_bootstrap()
    discovered = _discover_local_cluster_bootstrap()
    env_urls = _split_csv(os.environ.get("NULLA_MEET_SEED_URLS", ""))
    raw_seed_urls = [str(url).strip() for url in list(raw.get("meet_seed_urls") or []) if str(url).strip()]
    seed_urls = tuple(env_urls or raw_seed_urls or list(discovered.get("meet_seed_urls") or []))
    auth_tokens_by_base_url = _merge_auth_tokens_by_base_url(raw)
    if not auth_tokens_by_base_url:
        auth_tokens_by_base_url = dict(discovered.get("auth_tokens_by_base_url") or {})
    write_grants_by_base_url = _merge_write_grants_by_base_url(raw)
    if not write_grants_by_base_url:
        write_grants_by_base_url = dict(discovered.get("write_grants_by_base_url") or {})
    env_auth_token = _clean_token(str(os.environ.get("NULLA_MEET_AUTH_TOKEN", "")).strip())
    raw_auth_token = _clean_token(str(raw.get("auth_token") or "").strip()) or _clean_token(
        str(discovered.get("auth_token") or "").strip()
    )
    enabled_raw = str(os.environ.get("NULLA_PUBLIC_HIVE_ENABLED", "1")).strip().lower()
    enabled = enabled_raw not in {"0", "false", "no", "off"} and bool(seed_urls)
    topic_target_url = seed_urls[0] if seed_urls else None
    return PublicHiveBridgeConfig(
        enabled=enabled,
        meet_seed_urls=seed_urls,
        topic_target_url=topic_target_url,
        home_region=str(
            os.environ.get("NULLA_HOME_REGION")
            or raw.get("home_region")
            or discovered.get("home_region")
            or "global"
        ).strip()
        or "global",
        request_timeout_seconds=max(3, int(float(os.environ.get("NULLA_MEET_TIMEOUT_SECONDS") or raw.get("request_timeout_seconds") or 8))),
        auth_token=env_auth_token or raw_auth_token,
        auth_tokens_by_base_url=auth_tokens_by_base_url,
        write_grants_by_base_url=write_grants_by_base_url,
        tls_ca_file=str(raw.get("tls_ca_file") or discovered.get("tls_ca_file") or "").strip() or None,
        tls_insecure_skip_verify=bool(raw.get("tls_insecure_skip_verify", discovered.get("tls_insecure_skip_verify", False))),
    )


def ensure_public_hive_agent_bootstrap() -> Path | None:
    target_path = (CONFIG_HOME_DIR / "agent-bootstrap.json").resolve()
    if target_path.exists():
        return target_path

    seed_urls = _split_csv(os.environ.get("NULLA_MEET_SEED_URLS", ""))
    auth_token = _clean_token(str(os.environ.get("NULLA_MEET_AUTH_TOKEN", "")).strip())
    auth_tokens_by_base_url = _json_env_object(os.environ.get("NULLA_MEET_AUTH_TOKENS_JSON", ""))
    write_grants_by_base_url = _json_env_write_grants(os.environ.get("NULLA_MEET_WRITE_GRANTS_JSON", ""))
    raw = _load_agent_bootstrap(include_runtime=False)
    discovered = _discover_local_cluster_bootstrap()
    raw_seed_urls = [str(url).strip() for url in list(raw.get("meet_seed_urls") or []) if str(url).strip()]
    resolved_seed_urls = seed_urls or raw_seed_urls or list(discovered.get("meet_seed_urls") or [])
    if not resolved_seed_urls:
        return None

    payload: dict[str, Any] = {
        "home_region": str(
            os.environ.get("NULLA_HOME_REGION")
            or raw.get("home_region")
            or discovered.get("home_region")
            or "global"
        ).strip()
        or "global",
        "meet_seed_urls": resolved_seed_urls,
        "prefer_home_region_first": bool(raw.get("prefer_home_region_first", True)),
        "cross_region_summary_only": bool(raw.get("cross_region_summary_only", True)),
        "allow_local_fallback": bool(raw.get("allow_local_fallback", True)),
        "keep_local_cache": bool(raw.get("keep_local_cache", True)),
    }
    resolved_tls_ca_file = str(
        os.environ.get("NULLA_MEET_TLS_CA_FILE")
        or raw.get("tls_ca_file")
        or discovered.get("tls_ca_file")
        or ""
    ).strip()
    if resolved_tls_ca_file:
        payload["tls_ca_file"] = resolved_tls_ca_file
    resolved_tls_insecure = str(
        os.environ.get("NULLA_MEET_TLS_INSECURE_SKIP_VERIFY")
        or raw.get("tls_insecure_skip_verify")
        or discovered.get("tls_insecure_skip_verify")
        or ""
    ).strip().lower()
    if resolved_tls_insecure in {"1", "true", "yes", "on"}:
        payload["tls_insecure_skip_verify"] = True
    resolved_auth_token = auth_token or _clean_token(str(discovered.get("auth_token") or "").strip())
    if resolved_auth_token:
        payload["auth_token"] = resolved_auth_token
    merged_auth_tokens = _merge_auth_tokens_by_base_url(raw)
    merged_auth_tokens.update(dict(discovered.get("auth_tokens_by_base_url") or {}))
    merged_auth_tokens.update(auth_tokens_by_base_url)
    if merged_auth_tokens:
        payload["auth_tokens_by_base_url"] = merged_auth_tokens
    merged_write_grants = _merge_write_grants_by_base_url(raw)
    merged_write_grants.update(dict(discovered.get("write_grants_by_base_url") or {}))
    merged_write_grants.update(write_grants_by_base_url)
    if merged_write_grants:
        payload["write_grants_by_base_url"] = merged_write_grants
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return target_path
    except Exception:
        return None


def _load_agent_bootstrap(*, include_runtime: bool = True) -> dict[str, Any]:
    candidate_paths = _agent_bootstrap_paths(include_runtime=include_runtime)
    for path in candidate_paths:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def _agent_bootstrap_paths(*, include_runtime: bool) -> tuple[Path, ...]:
    paths: list[Path] = []
    if include_runtime:
        paths.append(config_path("agent-bootstrap.json"))
    paths.extend(
        [
            config_path("meet_clusters/do_ip_first_4node/agent-bootstrap.sample.json"),
            config_path("meet_clusters/separated_watch_4node/agent-bootstrap.sample.json"),
            config_path("meet_clusters/global_3node/agent-bootstrap.sample.json"),
        ]
    )
    return tuple(paths)


def write_public_hive_agent_bootstrap(
    *,
    target_path: Path | None = None,
    project_root: str | Path | None = None,
    meet_seed_urls: list[str] | tuple[str, ...] | None = None,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    write_grants_by_base_url: dict[str, dict[str, dict[str, Any]]] | None = None,
    home_region: str | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool | None = None,
) -> Path | None:
    destination = (target_path or config_path("agent-bootstrap.json")).resolve()
    root = Path(project_root).expanduser().resolve() if project_root else PROJECT_ROOT.resolve()
    existing = _load_json_file(destination) if destination.exists() else _load_agent_bootstrap(include_runtime=False)
    discovered = _discover_local_cluster_bootstrap()
    payload: dict[str, Any] = dict(existing or {})

    resolved_urls = [
        str(url).strip()
        for url in list(meet_seed_urls or payload.get("meet_seed_urls") or discovered.get("meet_seed_urls") or [])
        if str(url).strip()
    ]
    if not resolved_urls:
        return None
    payload["meet_seed_urls"] = resolved_urls
    payload["home_region"] = str(home_region or payload.get("home_region") or discovered.get("home_region") or "global").strip() or "global"
    payload["prefer_home_region_first"] = bool(payload.get("prefer_home_region_first", True))
    payload["cross_region_summary_only"] = bool(payload.get("cross_region_summary_only", True))
    payload["allow_local_fallback"] = bool(payload.get("allow_local_fallback", True))
    payload["keep_local_cache"] = bool(payload.get("keep_local_cache", True))
    resolved_tls_ca_file = _resolve_local_tls_ca_file(
        str(tls_ca_file or payload.get("tls_ca_file") or discovered.get("tls_ca_file") or "").strip() or None,
        project_root=root,
    )
    if resolved_tls_ca_file:
        try:
            resolved_tls_path = Path(resolved_tls_ca_file).resolve()
            if destination.is_relative_to(root) and resolved_tls_path.is_relative_to(root):
                payload["tls_ca_file"] = resolved_tls_path.relative_to(root).as_posix()
            else:
                payload["tls_ca_file"] = str(resolved_tls_path)
        except Exception:
            payload["tls_ca_file"] = resolved_tls_ca_file
    else:
        payload.pop("tls_ca_file", None)
    if tls_insecure_skip_verify is None:
        resolved_tls_insecure = bool(payload.get("tls_insecure_skip_verify", discovered.get("tls_insecure_skip_verify", False)))
    else:
        resolved_tls_insecure = bool(tls_insecure_skip_verify)
    if resolved_tls_insecure:
        payload["tls_insecure_skip_verify"] = True
    else:
        payload.pop("tls_insecure_skip_verify", None)

    merged_tokens = {
        _normalize_base_url(base): str(token).strip()
        for base, token in dict(payload.get("auth_tokens_by_base_url") or {}).items()
        if str(base).strip() and _clean_token(str(token or "").strip())
    }
    merged_tokens.update(dict(discovered.get("auth_tokens_by_base_url") or {}))
    for base, token in dict(auth_tokens_by_base_url or {}).items():
        normalized = _normalize_base_url(str(base or "").strip())
        clean_token = _clean_token(str(token or "").strip())
        if normalized and clean_token:
            merged_tokens[normalized] = clean_token
    if merged_tokens:
        payload["auth_tokens_by_base_url"] = merged_tokens
    elif "auth_tokens_by_base_url" in payload:
        payload.pop("auth_tokens_by_base_url", None)

    merged_write_grants = _merge_write_grants_by_base_url(payload)
    merged_write_grants.update(dict(discovered.get("write_grants_by_base_url") or {}))
    for base_url, routes in dict(write_grants_by_base_url or {}).items():
        normalized_base = _normalize_base_url(str(base_url or "").strip())
        if not normalized_base or not isinstance(routes, dict):
            continue
        normalized_routes = {
            (str(route or "").rstrip("/") or "/"): dict(grant)
            for route, grant in routes.items()
            if str(route or "").strip() and isinstance(grant, dict)
        }
        if normalized_routes:
            merged_write_grants[normalized_base] = normalized_routes
    if merged_write_grants:
        payload["write_grants_by_base_url"] = merged_write_grants
    elif "write_grants_by_base_url" in payload:
        payload.pop("write_grants_by_base_url", None)

    clean_auth_token = _clean_token(str(auth_token or "").strip()) or _clean_token(str(payload.get("auth_token") or "").strip())
    if clean_auth_token:
        payload["auth_token"] = clean_auth_token
    else:
        payload.pop("auth_token", None)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return destination
    except Exception:
        return None


def sync_public_hive_auth_from_ssh(
    *,
    ssh_key_path: str,
    project_root: str | Path | None = None,
    watch_host: str = "161.35.145.74",
    watch_user: str = "root",
    remote_config_path: str = "/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/watch-edge-1.json",
    target_path: Path | None = None,
    runner: Any | None = None,
) -> dict[str, Any]:
    key_path = Path(str(ssh_key_path or "").strip()).expanduser().resolve()
    if not key_path.exists():
        raise FileNotFoundError(f"SSH key not found: {key_path}")

    remote_path = str(remote_config_path or "").strip()
    if not remote_path:
        raise ValueError("Remote config path is required.")

    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-i",
        str(key_path),
        "-o",
        "StrictHostKeyChecking=accept-new",
        f"{str(watch_user or 'root').strip() or 'root'}@{str(watch_host or '').strip()}",
        (
            "python3 -c "
            + shlex.quote(
                "import json, pathlib; "
                f"print(json.dumps(json.loads(pathlib.Path({remote_path!r}).read_text(encoding='utf-8'))))"
            )
        ),
    ]
    completed = (runner or subprocess.run)(
        command,
        capture_output=True,
        check=True,
        text=True,
        timeout=12,
    )
    remote_payload = json.loads(str(completed.stdout or "").strip() or "{}")
    auth_token = _clean_token(str(remote_payload.get("auth_token") or "").strip())
    if not auth_token:
        raise ValueError("Remote watch config does not contain a valid auth token.")

    seed_urls = [
        str(url).strip()
        for url in list(remote_payload.get("upstream_base_urls") or remote_payload.get("meet_seed_urls") or [])
        if str(url).strip()
    ]
    written = write_public_hive_agent_bootstrap(
        target_path=target_path,
        project_root=project_root,
        meet_seed_urls=seed_urls,
        auth_token=auth_token,
        write_grants_by_base_url=dict(remote_payload.get("write_grants_by_base_url") or {}),
        tls_ca_file=str(remote_payload.get("tls_ca_file") or "").strip() or None,
        tls_insecure_skip_verify=bool(remote_payload.get("tls_insecure_skip_verify", False)),
    )
    if written is None:
        raise RuntimeError("Failed to write runtime agent-bootstrap.json")
    return {
        "path": str(written),
        "watch_host": str(watch_host or "").strip(),
        "seed_count": len(seed_urls),
        "auth_loaded": True,
    }


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def public_hive_has_auth(config: PublicHiveBridgeConfig | None = None, *, payload: dict[str, Any] | None = None) -> bool:
    if config is not None:
        if _clean_token(str(config.auth_token or "").strip()):
            return True
        return any(_clean_token(str(token or "").strip()) for token in dict(config.auth_tokens_by_base_url or {}).values())
    raw = dict(payload or {})
    if _clean_token(str(raw.get("auth_token") or "").strip()):
        return True
    return any(_clean_token(str(token or "").strip()) for token in dict(raw.get("auth_tokens_by_base_url") or {}).values())


def public_hive_write_requires_auth(
    config: PublicHiveBridgeConfig | None = None,
    *,
    seed_urls: list[str] | tuple[str, ...] | None = None,
    topic_target_url: str | None = None,
) -> bool:
    urls = [str(url).strip() for url in list(seed_urls or []) if str(url).strip()]
    if config is not None:
        urls.extend(str(url).strip() for url in list(config.meet_seed_urls or ()) if str(url).strip())
        if str(config.topic_target_url or "").strip():
            urls.append(str(config.topic_target_url or "").strip())
    if str(topic_target_url or "").strip():
        urls.append(str(topic_target_url or "").strip())
    return any(_url_requires_auth(url) for url in urls)


def public_hive_write_enabled(config: PublicHiveBridgeConfig | None = None) -> bool:
    cfg = config or load_public_hive_bridge_config()
    if not cfg.enabled or not cfg.meet_seed_urls:
        return False
    if not public_hive_write_requires_auth(cfg):
        return True
    return public_hive_has_auth(cfg)


def _resolve_local_tls_ca_file(tls_ca_file: str | None, *, project_root: str | Path | None = None) -> str | None:
    raw = str(tls_ca_file or "").strip()
    if not raw:
        return None

    root = Path(project_root).expanduser().resolve() if project_root else PROJECT_ROOT.resolve()
    candidate = Path(raw).expanduser()
    if candidate.is_absolute() and candidate.is_file():
        return str(candidate.resolve())
    if not candidate.is_absolute():
        rooted_candidate = (root / candidate).resolve()
        if rooted_candidate.is_file():
            return str(rooted_candidate)

    normalized = raw.replace("\\", "/")
    relative_from_config = ""
    marker = "/config/"
    if marker in normalized:
        relative_from_config = "config/" + normalized.split(marker, 1)[1].lstrip("/")

    local_candidates: list[Path] = []
    if relative_from_config:
        local_candidates.append(root / relative_from_config)
    if candidate.name:
        local_candidates.extend(
            [
                root / "config" / "meet_clusters" / "do_ip_first_4node" / "tls" / candidate.name,
                root / "config" / "meet_clusters" / "separated_watch_4node" / "tls" / candidate.name,
                root / "config" / "tls" / candidate.name,
            ]
        )
    for local_path in local_candidates:
        if local_path.is_file():
            return str(local_path.resolve())
    return raw


def find_public_hive_ssh_key(project_root: str | Path | None = None) -> Path | None:
    root = Path(project_root).expanduser().resolve() if project_root else PROJECT_ROOT.resolve()
    seen: set[Path] = set()
    env_path = str(os.environ.get("NULLA_PUBLIC_HIVE_SSH_KEY_PATH") or "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(
        [
            root / "ssh" / "nulla-ssh" / "nulla_do_ed25519_v2",
            root.parent / "ssh" / "nulla-ssh" / "nulla_do_ed25519_v2",
            Path.home() / ".ssh" / "nulla_do_ed25519_v2",
            Path.home() / ".ssh" / "nulla_do_ed25519",
            Path.home() / "Desktop" / "ssh" / "nulla-ssh" / "nulla_do_ed25519_v2",
        ]
    )
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def ensure_public_hive_auth(
    *,
    project_root: str | Path | None = None,
    target_path: Path | None = None,
    watch_host: str | None = None,
    watch_user: str = "root",
    remote_config_path: str = "/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/watch-edge-1.json",
    require_auth: bool = False,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve() if project_root else PROJECT_ROOT.resolve()
    destination = (target_path or (CONFIG_HOME_DIR / "agent-bootstrap.json")).expanduser().resolve()
    existing = _load_json_file(destination) if destination.exists() else {}
    bundled = _load_json_file(root / "config" / "agent-bootstrap.json")
    discovered = _discover_local_cluster_bootstrap()
    sample = _load_agent_bootstrap(include_runtime=False)

    seed_urls = [
        str(url).strip()
        for url in list(
            existing.get("meet_seed_urls")
            or bundled.get("meet_seed_urls")
            or discovered.get("meet_seed_urls")
            or sample.get("meet_seed_urls")
            or []
        )
        if str(url).strip()
    ]
    if not seed_urls:
        return {"ok": True, "status": "disabled", "seed_count": 0, "target_path": str(destination)}

    merged_auth_tokens: dict[str, str] = {}
    for payload in (sample, discovered, bundled, existing):
        for base, token in dict(payload.get("auth_tokens_by_base_url") or {}).items():
            normalized = _normalize_base_url(str(base or "").strip())
            clean_token = _clean_token(str(token or "").strip())
            if normalized and clean_token:
                merged_auth_tokens[normalized] = clean_token
    merged_write_grants: dict[str, dict[str, dict[str, Any]]] = {}
    for payload in (sample, discovered, bundled, existing):
        for base_url, routes in dict(payload.get("write_grants_by_base_url") or {}).items():
            normalized_base = _normalize_base_url(str(base_url or "").strip())
            if not normalized_base or not isinstance(routes, dict):
                continue
            normalized_routes = {
                (str(route or "").rstrip("/") or "/"): dict(grant)
                for route, grant in routes.items()
                if str(route or "").strip() and isinstance(grant, dict)
            }
            if normalized_routes:
                merged_write_grants[normalized_base] = normalized_routes
    auth_token = (
        _clean_token(str(existing.get("auth_token") or "").strip())
        or _clean_token(str(bundled.get("auth_token") or "").strip())
        or _clean_token(str(discovered.get("auth_token") or "").strip())
        or _clean_token(str(sample.get("auth_token") or "").strip())
    )
    home_region = (
        str(existing.get("home_region") or "").strip()
        or str(bundled.get("home_region") or "").strip()
        or str(discovered.get("home_region") or "").strip()
        or str(sample.get("home_region") or "").strip()
        or "global"
    )
    tls_ca_file = (
        str(existing.get("tls_ca_file") or "").strip()
        or str(bundled.get("tls_ca_file") or "").strip()
        or str(discovered.get("tls_ca_file") or "").strip()
        or str(sample.get("tls_ca_file") or "").strip()
        or None
    )
    tls_insecure_skip_verify = bool(
        existing.get("tls_insecure_skip_verify")
        or bundled.get("tls_insecure_skip_verify")
        or discovered.get("tls_insecure_skip_verify")
        or sample.get("tls_insecure_skip_verify")
    )

    if auth_token or merged_auth_tokens:
        written = write_public_hive_agent_bootstrap(
            target_path=destination,
            project_root=root,
            meet_seed_urls=seed_urls,
            auth_token=auth_token,
            auth_tokens_by_base_url=merged_auth_tokens,
            write_grants_by_base_url=merged_write_grants,
            home_region=home_region,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
        return {
            "ok": written is not None,
            "status": "already_configured" if public_hive_has_auth(payload=existing) else "hydrated_from_bundle",
            "seed_count": len(seed_urls),
            "target_path": str(written or destination),
            "auth_loaded": True,
        }

    requires_auth = public_hive_write_requires_auth(seed_urls=seed_urls)
    if not requires_auth:
        written = write_public_hive_agent_bootstrap(
            target_path=destination,
            project_root=root,
            meet_seed_urls=seed_urls,
            auth_tokens_by_base_url=merged_auth_tokens,
            write_grants_by_base_url=merged_write_grants,
            home_region=home_region,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
        return {
            "ok": written is not None,
            "status": "no_auth_required",
            "seed_count": len(seed_urls),
            "target_path": str(written or destination),
            "auth_loaded": False,
        }

    ssh_key = find_public_hive_ssh_key(root)
    if ssh_key is None:
        return {
            "ok": False,
            "status": "missing_ssh_key" if not require_auth else "auth_required",
            "seed_count": len(seed_urls),
            "target_path": str(destination),
            "requires_auth": True,
        }

    sync_result = sync_public_hive_auth_from_ssh(
        ssh_key_path=str(ssh_key),
        project_root=root,
        watch_host=str(watch_host or os.environ.get("NULLA_PUBLIC_HIVE_WATCH_HOST") or "161.35.145.74").strip(),
        watch_user=str(watch_user or "root").strip() or "root",
        remote_config_path=str(remote_config_path or "").strip() or "/opt/Decentralized_NULLA/config/meet_clusters/do_ip_first_4node/watch-edge-1.json",
        target_path=destination,
    )
    sync_result["ok"] = True
    sync_result["status"] = "synced_from_ssh"
    sync_result["ssh_key_path"] = str(ssh_key)
    return sync_result


def _discover_local_cluster_bootstrap() -> dict[str, Any]:
    watch_paths = [
        config_path("meet_clusters/do_ip_first_4node/watch-edge-1.json"),
        config_path("meet_clusters/separated_watch_4node/watch-edge-1.json"),
    ]
    for path in watch_paths:
        raw = _load_json_file(path)
        auth_token = _clean_token(str(raw.get("auth_token") or "").strip())
        upstream = [str(url).strip() for url in list(raw.get("upstream_base_urls") or []) if str(url).strip()]
        tls_ca_file = str(raw.get("tls_ca_file") or "").strip()
        tls_insecure_skip_verify = bool(raw.get("tls_insecure_skip_verify", False))
        if auth_token or upstream:
            payload: dict[str, Any] = {}
            if auth_token:
                payload["auth_token"] = auth_token
            if upstream:
                payload["meet_seed_urls"] = upstream
            if tls_ca_file:
                payload["tls_ca_file"] = tls_ca_file
            if tls_insecure_skip_verify:
                payload["tls_insecure_skip_verify"] = True
            return payload

    region_map = {
        "seed-eu-1.json": "eu",
        "seed-us-1.json": "us",
        "seed-apac-1.json": "apac",
    }
    discovered_urls: list[str] = []
    discovered_tokens: dict[str, str] = {}
    discovered_home_region = ""
    discovered_tls_ca_file = ""
    discovered_tls_insecure_skip_verify = False
    for filename, region in region_map.items():
        raw = _load_json_file(config_path("meet_clusters/do_ip_first_4node", filename))
        public_base_url = str(raw.get("public_base_url") or "").strip()
        auth_token = _clean_token(str(raw.get("auth_token") or "").strip())
        tls_ca_file = str(raw.get("tls_ca_file") or "").strip()
        tls_insecure_skip_verify = bool(raw.get("tls_insecure_skip_verify", False))
        if public_base_url:
            discovered_urls.append(public_base_url)
        if public_base_url and auth_token:
            discovered_tokens[_normalize_base_url(public_base_url)] = auth_token
            if not discovered_home_region:
                discovered_home_region = region
        if tls_ca_file and not discovered_tls_ca_file:
            discovered_tls_ca_file = tls_ca_file
        if tls_insecure_skip_verify:
            discovered_tls_insecure_skip_verify = True
    payload = {}
    if discovered_urls:
        payload["meet_seed_urls"] = discovered_urls
    if discovered_tokens:
        payload["auth_tokens_by_base_url"] = discovered_tokens
        if len(set(discovered_tokens.values())) == 1:
            payload["auth_token"] = next(iter(discovered_tokens.values()))
    if discovered_home_region:
        payload["home_region"] = discovered_home_region
    if discovered_tls_ca_file:
        payload["tls_ca_file"] = discovered_tls_ca_file
    if discovered_tls_insecure_skip_verify:
        payload["tls_insecure_skip_verify"] = True
    return payload


def _json_env_object(value: str) -> dict[str, str]:
    try:
        raw = json.loads(str(value or "").strip() or "{}")
    except Exception:
        raw = {}
    out: dict[str, str] = {}
    for base, token in dict(raw or {}).items():
        clean_token = _clean_token(str(token or "").strip())
        clean_base = _normalize_base_url(str(base or "").strip())
        if clean_base and clean_token:
            out[clean_base] = clean_token
    return out


def _json_env_write_grants(value: str) -> dict[str, dict[str, dict[str, Any]]]:
    try:
        raw = json.loads(str(value or "").strip() or "{}")
    except Exception:
        raw = {}
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for base_url, routes in dict(raw or {}).items():
        normalized_base = _normalize_base_url(str(base_url or "").strip())
        if not normalized_base or not isinstance(routes, dict):
            continue
        normalized_routes: dict[str, dict[str, Any]] = {}
        for route, grant in dict(routes).items():
            clean_route = str(route or "").rstrip("/") or "/"
            if not clean_route or not isinstance(grant, dict):
                continue
            normalized_routes[clean_route] = dict(grant)
        if normalized_routes:
            out[normalized_base] = normalized_routes
    return out


def _merge_auth_tokens_by_base_url(raw: dict[str, Any]) -> dict[str, str]:
    merged = {
        _normalize_base_url(base): str(token).strip()
        for base, token in dict(raw.get("auth_tokens_by_base_url") or {}).items()
        if str(base).strip() and _clean_token(str(token or "").strip())
    }
    merged.update(_json_env_object(os.environ.get("NULLA_MEET_AUTH_TOKENS_JSON", "")))
    return merged


def _merge_write_grants_by_base_url(raw: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    merged: dict[str, dict[str, dict[str, Any]]] = {}
    raw_value = dict(raw.get("write_grants_by_base_url") or {})
    for base_url, routes in raw_value.items():
        normalized_base = _normalize_base_url(str(base_url or "").strip())
        if not normalized_base or not isinstance(routes, dict):
            continue
        merged[normalized_base] = {
            (str(route or "").rstrip("/") or "/"): dict(grant)
            for route, grant in routes.items()
            if str(route or "").strip() and isinstance(grant, dict)
        }
    env_value = _json_env_write_grants(os.environ.get("NULLA_MEET_WRITE_GRANTS_JSON", ""))
    for base_url, routes in env_value.items():
        merged[base_url] = dict(routes)
    return merged


def _clean_token(value: str) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned or _PLACEHOLDER_TOKEN_RE.search(cleaned):
        return None
    return cleaned


def _url_requires_auth(url: str) -> bool:
    parsed = urlsplit(str(url or "").strip())
    host = str(parsed.hostname or "").strip().lower()
    if host in {"", "localhost", "127.0.0.1", "::1"}:
        return False
    return bool(host)


def _normalize_base_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url or "").rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", "")).rstrip("/")


def _normalize_presence_status(value: str) -> str:
    lowered = str(value or "idle").strip().lower()
    if lowered in {"idle", "busy", "offline", "limited"}:
        return lowered
    return "idle"


def _task_title(task_summary: str) -> str:
    trimmed = str(task_summary or "").strip()
    if len(trimmed) > 112:
        trimmed = trimmed[:109].rstrip() + "..."
    return f"Task: {trimmed}"[:180]


def _topic_tags(*, task_class: str, text: str, extra: list[str] | None = None) -> list[str]:
    tokens: list[str] = []
    for item in [str(task_class or "").strip().lower(), *[str(v).strip().lower() for v in list(extra or [])]]:
        if item and item not in tokens:
            tokens.append(item[:32])
    for raw in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", str(text or "").lower()):
        if raw not in tokens:
            tokens.append(raw[:32])
        if len(tokens) >= 8:
            break
    return tokens[:8]


def _public_post_body(response: str) -> str:
    text = str(response or "").strip()
    if not text:
        return ""
    if text.lower().startswith("workflow:\n"):
        parts = text.split("\n\n", 1)
        text = parts[1].strip() if len(parts) == 2 else text
    text = " ".join(text.split())
    if len(text) > 1600:
        text = text[:1597].rstrip() + "..."
    return text


def _fallback_public_post_body(*, task_summary: str, task_class: str) -> str:
    text = (
        f"Public-safe update from NULLA: working on {str(task_class or 'research').strip() or 'research'} "
        f"for '{str(task_summary or '').strip()}'."
    ).strip()
    return text[:1600]


def _commons_topic_title(topic: str) -> str:
    trimmed = " ".join(str(topic or "").split()).strip()
    if len(trimmed) > 132:
        trimmed = trimmed[:129].rstrip() + "..."
    return f"Agent Commons: {trimmed}"[:180]


def _commons_topic_summary(*, topic: str, summary: str) -> str:
    text = (
        "Idle agent commons thread for bounded brainstorming and curiosity. "
        f"Current focus: {str(topic or '').strip()}. "
        f"Working note: {str(summary or '').strip()}"
    ).strip()
    return text[:3000]


def _commons_post_body(*, topic: str, summary: str, public_body: str) -> str:
    base = _public_post_body(public_body) or str(summary or "").strip()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    text = (
        f"Commons update [{stamp}] on {str(topic or '').strip()}: "
        f"{base or str(summary or '').strip()}"
    ).strip()
    return text[:1600]


def _topic_match_score(
    *,
    task_summary: str,
    task_class: str,
    topic_tags: list[str],
    topic: dict[str, Any],
) -> int:
    score = 0
    wanted_tags = {str(item or "").strip().lower() for item in topic_tags if str(item or "").strip()}
    existing_tags = {
        str(item or "").strip().lower()
        for item in list(topic.get("topic_tags") or [])
        if str(item or "").strip()
    }
    tag_overlap = wanted_tags & existing_tags
    score += min(3, len(tag_overlap))
    if str(task_class or "").strip().lower() in existing_tags:
        score += 1
    task_tokens = set(_content_tokens(task_summary))
    topic_tokens = set(
        _content_tokens(str(topic.get("title") or ""))
        + _content_tokens(str(topic.get("summary") or ""))
    )
    score += min(4, len(task_tokens & topic_tokens))
    return score


def _content_tokens(text: str) -> list[str]:
    return [token[:32] for token in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", str(text or "").lower())]


def _http_error_detail(exc: urllib.error.HTTPError, *, fallback: str) -> str:
    try:
        raw = exc.read().decode("utf-8", "replace").strip()
    except Exception:
        raw = ""
    if raw:
        try:
            payload = json.loads(raw)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            detail = str(payload.get("error") or "").strip()
            if detail:
                return detail
        return raw[:800]
    code = int(getattr(exc, "code", 0) or 0)
    return f"{fallback} (HTTP {code})" if code else fallback


def _route_missing(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return int(getattr(exc, "code", 0) or 0) == 404
    return False
