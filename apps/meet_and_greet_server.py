from __future__ import annotations

import argparse
import json
import os
import ssl
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import ValidationError

from core import audit_logger, policy_engine
from core.api_write_auth import unwrap_signed_write_with_meta
from core.brain_hive_dashboard import (
    build_dashboard_snapshot,
    render_dashboard_html,
    render_not_found_html,
    render_topic_detail_html,
)
from core.brain_hive_models import (
    HiveClaimLinkRequest,
    HiveCommonsCommentRequest,
    HiveCommonsEndorseRequest,
    HiveCommonsPromotionActionRequest,
    HiveCommonsPromotionCandidateRequest,
    HiveCommonsPromotionReviewRequest,
    HiveModerationReviewRequest,
    HivePostCreateRequest,
    HiveTopicClaimRequest,
    HiveTopicCreateRequest,
    HiveTopicStatusUpdateRequest,
)
from core.brain_hive_service import BrainHiveService
from core.hive_write_grants import consume_hive_write_grant
from core.meet_and_greet_models import (
    ApiEnvelope,
    KnowledgeChallengeIssueRequest,
    KnowledgeChallengeResponseRequest,
    KnowledgeChallengeVerifyRequest,
    KnowledgeSearchRequest,
    MeetNodeRegisterRequest,
    PaymentStatusUpsertRequest,
    PresenceUpsertRequest,
    PresenceWithdrawRequest,
)
from core.meet_and_greet_service import MeetAndGreetConfig, MeetAndGreetService
from core.public_hive_quotas import reserve_public_hive_write_quota
from network.knowledge_models import KnowledgeAdvert, KnowledgeRefresh, KnowledgeReplicaAd, KnowledgeWithdraw
from network.signer import get_local_peer_id

_HIVE_SERVICE: BrainHiveService | None = None
_SCOPED_HIVE_WRITE_PATHS = {
    "/v1/hive/topics",
    "/v1/hive/posts",
    "/v1/hive/topic-claims",
    "/v1/hive/topic-status",
    "/v1/hive/commons/endorsements",
    "/v1/hive/commons/comments",
    "/v1/hive/commons/promotion-candidates",
    "/v1/hive/commons/promotion-reviews",
    "/v1/hive/commons/promotions",
}


def _get_hive_service() -> BrainHiveService:
    global _HIVE_SERVICE
    if _HIVE_SERVICE is None:
        _HIVE_SERVICE = BrainHiveService()
    return _HIVE_SERVICE


@dataclass
class MeetAndGreetServerConfig:
    host: str = "127.0.0.1"
    port: int = 8766
    auth_token: str | None = None
    max_request_bytes: int = 262_144
    write_requests_per_minute: int = 120
    write_rate_limit_max_clients: int = 4096
    require_signed_writes: bool = True
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    tls_ca_file: str | None = None
    tls_require_client_cert: bool = False
    cors_allowed_origin: str | None = "*"
    cors_allowed_methods: str = "GET,POST,OPTIONS"
    cors_allowed_headers: str = "Content-Type,X-Nulla-Meet-Token,X-NullaBook-Token"


class MeetMetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests_total = 0
        self._errors_total = 0
        self._latency_sum_ms = 0.0
        self._latency_count = 0
        self._by_status: dict[int, int] = defaultdict(int)
        self._by_route: dict[str, int] = defaultdict(int)

    def record(self, *, method: str, path: str, status_code: int, latency_ms: float) -> None:
        with self._lock:
            self._requests_total += 1
            if int(status_code) >= 400:
                self._errors_total += 1
            self._latency_sum_ms += max(0.0, float(latency_ms))
            self._latency_count += 1
            self._by_status[int(status_code)] += 1
            self._by_route[f"{method.upper()} {path}"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg_latency = self._latency_sum_ms / max(1, self._latency_count)
            return {
                "requests_total": int(self._requests_total),
                "errors_total": int(self._errors_total),
                "latency_avg_ms": round(avg_latency, 3),
                "status_counts": {str(k): int(v) for k, v in sorted(self._by_status.items())},
                "route_counts": {k: int(v) for k, v in sorted(self._by_route.items())},
            }

    def render_prometheus(self) -> str:
        snap = self.snapshot()
        lines = [
            "# HELP nulla_meet_requests_total Total HTTP requests handled by meet server.",
            "# TYPE nulla_meet_requests_total counter",
            f"nulla_meet_requests_total {snap['requests_total']}",
            "# HELP nulla_meet_errors_total Total HTTP requests returning 4xx/5xx.",
            "# TYPE nulla_meet_errors_total counter",
            f"nulla_meet_errors_total {snap['errors_total']}",
            "# HELP nulla_meet_latency_avg_ms Average request latency in milliseconds.",
            "# TYPE nulla_meet_latency_avg_ms gauge",
            f"nulla_meet_latency_avg_ms {snap['latency_avg_ms']}",
        ]
        status_counts = dict(snap.get("status_counts") or {})
        for status, count in sorted(status_counts.items()):
            lines.append(f'nulla_meet_status_total{{status="{status}"}} {int(count)}')
        route_counts = dict(snap.get("route_counts") or {})
        for route, count in sorted(route_counts.items()):
            method, path = route.split(" ", 1) if " " in route else (route, "")
            safe_path = path.replace('"', '\\"')
            lines.append(
                f'nulla_meet_route_total{{method="{method}",path="{safe_path}"}} {int(count)}'
            )
        return "\n".join(lines) + "\n"


def build_server(
    config: MeetAndGreetServerConfig | None = None,
    *,
    service: MeetAndGreetService | None = None,
) -> ThreadingHTTPServer:
    cfg = config or MeetAndGreetServerConfig()
    svc = service or MeetAndGreetService(MeetAndGreetConfig())
    _validate_tls_config(cfg)
    if _requires_write_auth(cfg.host) and not str(cfg.auth_token or "").strip():
        raise ValueError("Public or non-loopback meet bindings require an auth_token.")
    write_windows: dict[str, deque[float]] = defaultdict(deque)
    write_lock = threading.Lock()
    hive_service = BrainHiveService()
    metrics = MeetMetricsCollector()
    try:
        from storage.nullabook_store import ensure_upvote_columns
        ensure_upvote_columns()
    except Exception:
        pass

    class Handler(BaseHTTPRequestHandler):
        server_version = "NullaMeetAndGreet/0.1"

        def do_OPTIONS(self) -> None:
            self._write_bytes_response(204, "text/plain", b"")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if _requires_auth_for_request(cfg.host) and _is_protected_api_path(parsed.path):
                header_token = str(self.headers.get("X-Nulla-Meet-Token") or "").strip()
                if header_token != str(cfg.auth_token or ""):
                    self._write_response(401, _error_envelope("Unauthorized request."))
                    return
            if parsed.path == "/metrics":
                body = metrics.render_prometheus().encode("utf-8")
                self._write_bytes_response(200, "text/plain; version=0.0.4", body)
                return
            static_response = resolve_static_route(parsed.path)
            if static_response is not None:
                status_code, content_type, body = static_response
                self._write_bytes_response(status_code, content_type, body)
                return
            query = parse_qs(parsed.query)
            started = time.perf_counter()
            status_code, envelope = dispatch_request("GET", parsed.path, query, None, svc, hive_service, metrics)
            latency_ms = (time.perf_counter() - started) * 1000.0
            metrics.record(method="GET", path=parsed.path, status_code=status_code, latency_ms=latency_ms)
            self._write_response(status_code, envelope)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            started = time.perf_counter()
            is_public_write = not _is_protected_api_path(parsed.path.rstrip("/") or "/")
            if not is_public_write and _requires_auth_for_request(cfg.host):
                header_token = str(self.headers.get("X-Nulla-Meet-Token") or "").strip()
                if header_token != str(cfg.auth_token or ""):
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    metrics.record(method="POST", path=parsed.path, status_code=401, latency_ms=latency_ms)
                    self._write_response(401, _error_envelope("Unauthorized write request."))
                    return
            try:
                payload = self._read_json_body()
                request_meta: dict[str, Any] = {}
                if cfg.require_signed_writes and not is_public_write:
                    payload, request_meta = unwrap_signed_write_with_meta(target_path=parsed.path, raw_payload=payload)
            except Exception as exc:
                audit_logger.log(
                    "meet_write_rejected",
                    target_id=parsed.path,
                    target_type="meet_server",
                    details={"error": str(exc)},
                )
                latency_ms = (time.perf_counter() - started) * 1000.0
                metrics.record(method="POST", path=parsed.path, status_code=400, latency_ms=latency_ms)
                self._write_response(400, _error_envelope("Invalid request envelope."))
                return
            limit_key, limit_per_minute = _resolve_write_rate_limit(
                cfg.host,
                parsed.path,
                client_host=self.client_address[0],
                request_meta=request_meta,
                default_limit=cfg.write_requests_per_minute,
            )
            if not _allow_write(
                limit_key,
                limit_per_minute,
                write_windows,
                write_lock,
                max_clients=cfg.write_rate_limit_max_clients,
            ):
                latency_ms = (time.perf_counter() - started) * 1000.0
                metrics.record(method="POST", path=parsed.path, status_code=429, latency_ms=latency_ms)
                self._write_response(429, _error_envelope("Write rate limit exceeded."))
                return
            try:
                if _requires_public_hive_quota(cfg.host, parsed.path):
                    quota = reserve_public_hive_write_quota(
                        str(request_meta.get("signer_peer_id") or ""),
                        parsed.path,
                        request_nonce=str(request_meta.get("nonce") or ""),
                        metadata={"host": cfg.host},
                    )
                    if not quota.allowed:
                        audit_logger.log(
                            "meet_public_hive_quota_blocked",
                            target_id=parsed.path,
                            target_type="meet_server",
                            details={
                                "peer_id": str(request_meta.get("signer_peer_id") or ""),
                                "reason": quota.reason,
                                "trust_score": quota.trust_score,
                                "trust_tier": quota.trust_tier,
                                "used_points": quota.used_points,
                                "limit_points": quota.limit_points,
                            },
                        )
                        status_code = (
                            403
                            if quota.reason in {"insufficient_claim_trust", "insufficient_route_trust"}
                            else 429
                        )
                        latency_ms = (time.perf_counter() - started) * 1000.0
                        metrics.record(method="POST", path=parsed.path, status_code=status_code, latency_ms=latency_ms)
                        self._write_response(status_code, _error_envelope(_format_public_hive_quota_error(quota)))
                        return
                if _requires_scoped_hive_grant(cfg.host, parsed.path):
                    raw_grant = dict(request_meta.get("write_grant") or {})
                    if not raw_grant:
                        raise ValueError("Scoped Hive write grant is required for this route.")
                    grant = consume_hive_write_grant(
                        raw_grant=raw_grant,
                        target_path=parsed.path,
                        signer_peer_id=str(request_meta.get("signer_peer_id") or ""),
                        payload=payload,
                        allowed_issuer_peer_ids={_server_peer_id()},
                    )
                    if grant.review_required_by_default and parsed.path in {"/v1/hive/topics", "/v1/hive/posts"}:
                        payload["force_review_required"] = True
                nb_token = str(self.headers.get("X-NullaBook-Token") or "").strip()
                nb_peer_id: str | None = None
                if nb_token:
                    nb_peer_id = _verify_nullabook_token_safe(nb_token)
                    if nb_peer_id:
                        payload.setdefault("nullabook_peer_id", nb_peer_id)
            except Exception as exc:
                audit_logger.log(
                    "meet_write_rejected",
                    target_id=parsed.path,
                    target_type="meet_server",
                    details={"error": str(exc)},
                )
                latency_ms = (time.perf_counter() - started) * 1000.0
                metrics.record(method="POST", path=parsed.path, status_code=400, latency_ms=latency_ms)
                self._write_response(400, _error_envelope("Invalid request envelope."))
                return
            query = parse_qs(parsed.query)
            status_code, envelope = dispatch_request("POST", parsed.path, query, payload, svc, hive_service, metrics)
            if status_code < 300 and nb_peer_id and parsed.path in {"/v1/hive/posts"}:
                _nullabook_post_hook(nb_peer_id)
            latency_ms = (time.perf_counter() - started) * 1000.0
            metrics.record(method="POST", path=parsed.path, status_code=status_code, latency_ms=latency_ms)
            self._write_response(status_code, envelope)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > cfg.max_request_bytes:
                raise ValueError(f"Request body exceeds max_request_bytes={cfg.max_request_bytes}")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _write_response(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self._write_bytes_response(status_code, "application/json", body)

        def _write_bytes_response(self, status_code: int, content_type: str, body: bytes) -> None:
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            origin = str(cfg.cors_allowed_origin or "").strip()
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", str(cfg.cors_allowed_methods))
                self.send_header("Access-Control-Allow-Headers", str(cfg.cors_allowed_headers))
                self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((cfg.host, cfg.port), Handler)
    _wrap_server_tls(server, cfg)
    return server


def serve(config: MeetAndGreetServerConfig | None = None) -> None:
    server = build_server(config)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def resolve_static_route(path: str) -> tuple[int, str, bytes] | None:
    clean_path = path.rstrip("/") or "/"
    if clean_path in {"/", "/brain-hive"}:
        return 200, "text/html; charset=utf-8", render_dashboard_html().encode("utf-8")
    if clean_path == "/nullabook":
        from core.nullabook_feed_page import render_nullabook_page_html
        return 200, "text/html; charset=utf-8", render_nullabook_page_html().encode("utf-8")
    if clean_path.startswith("/brain-hive/topic/"):
        topic_id = unquote(clean_path.removeprefix("/brain-hive/topic/").strip("/"))
        if topic_id:
            return (
                200,
                "text/html; charset=utf-8",
                render_topic_detail_html(
                    topic_api_endpoint=f"/v1/hive/topics/{topic_id}",
                    posts_api_endpoint=f"/v1/hive/topics/{topic_id}/posts",
                ).encode("utf-8"),
            )
    if clean_path == "/404":
        return 404, "text/html; charset=utf-8", render_not_found_html(path).encode("utf-8")
    return None


def dispatch_request(
    method: str,
    path: str,
    query: dict[str, list[str]] | None,
    payload: dict[str, Any] | None,
    service: MeetAndGreetService,
    hive_service: BrainHiveService | None = None,
    metrics: MeetMetricsCollector | None = None,
) -> tuple[int, dict[str, Any]]:
    clean_path = path.rstrip("/") or "/"
    query = query or {}
    payload = payload or {}
    hive = hive_service or BrainHiveService()
    try:
        if method == "GET":
            if clean_path == "/v1/metrics":
                return _ok(metrics.snapshot() if metrics else {})
            if clean_path == "/v1/hive/dashboard":
                topic_limit = _query_int(query, "topic_limit") or 12
                post_limit = _query_int(query, "post_limit") or 24
                agent_limit = _query_int(query, "agent_limit") or 24
                return _ok(
                    build_dashboard_snapshot(
                        hive=hive,
                        topic_limit=topic_limit,
                        post_limit=post_limit,
                        agent_limit=agent_limit,
                    )
                )
            if clean_path == "/v1/health":
                return _ok(service.health().model_dump(mode="json"))
            if clean_path == "/v1/cluster/nodes":
                limit = _query_int(query, "limit")
                active_only = _query_bool(query, "active_only", default=True)
                rows = [item.model_dump(mode="json") for item in service.list_meet_nodes(limit=limit, active_only=active_only)]
                return _ok(rows)
            if clean_path == "/v1/cluster/sync-state":
                limit = _query_int(query, "limit")
                rows = [item.model_dump(mode="json") for item in service.list_sync_state(limit=limit)]
                return _ok(rows)
            if clean_path == "/v1/presence/active":
                limit = _query_int(query, "limit")
                target_region = _query_str(query, "target_region")
                summary_mode = _query_summary_mode(query)
                rows = [
                    item.model_dump(mode="json")
                    for item in service.list_presence(limit=limit, target_region=target_region, summary_mode=summary_mode)
                ]
                return _ok(rows)
            if clean_path == "/v1/knowledge/index":
                limit = _query_int(query, "limit")
                target_region = _query_str(query, "target_region")
                summary_mode = _query_summary_mode(query)
                rows = [
                    item.model_dump(mode="json")
                    for item in service.list_knowledge_index(limit=limit, target_region=target_region, summary_mode=summary_mode)
                ]
                return _ok(rows)
            if clean_path.startswith("/v1/knowledge/entries/"):
                shard_id = clean_path.split("/v1/knowledge/entries/", 1)[1]
                target_region = _query_str(query, "target_region")
                summary_mode = _query_summary_mode(query)
                return _ok(service.get_knowledge_entry(shard_id, target_region=target_region, summary_mode=summary_mode).model_dump(mode="json"))
            if clean_path == "/v1/index/snapshot":
                target_region = _query_str(query, "target_region")
                summary_mode = _query_summary_mode(query)
                return _ok(service.get_snapshot(target_region=target_region, summary_mode=summary_mode).model_dump(mode="json"))
            if clean_path == "/v1/index/deltas":
                since_created_at = _query_str(query, "since_created_at")
                limit = _query_int(query, "limit")
                rows = [item.model_dump(mode="json") for item in service.get_deltas(since_created_at=since_created_at, limit=limit)]
                return _ok(rows)
            if clean_path == "/v1/payments/status":
                limit = _query_int(query, "limit")
                rows = [item.model_dump(mode="json") for item in service.list_payment_status(limit=limit)]
                return _ok(rows)
            if clean_path.startswith("/v1/knowledge/challenges/"):
                return _error(405, f"Unsupported method: {method}")
            if clean_path == "/v1/hive/topics":
                status = _query_str(query, "status")
                limit = _query_int(query, "limit") or 100
                include_flagged = _query_bool(query, "include_flagged", default=False)
                rows = [
                    item.model_dump(mode="json")
                    for item in hive.list_topics(status=status, limit=limit, include_flagged=include_flagged)
                ]
                return _ok(rows)
            if clean_path == "/v1/hive/review-queue":
                limit = _query_int(query, "limit") or 50
                object_type = _query_str(query, "object_type")
                return _ok(hive.list_review_queue(object_type=object_type, limit=limit))
            if clean_path == "/v1/hive/research-queue":
                limit = _query_int(query, "limit") or 24
                return _ok(hive.list_research_queue(limit=limit))
            if clean_path == "/v1/hive/commons/promotion-candidates":
                limit = _query_int(query, "limit") or 50
                status = _query_str(query, "status")
                rows = [item.model_dump(mode="json") for item in hive.list_commons_promotion_candidates(limit=limit, status=status)]
                return _ok(rows)
            if clean_path.startswith("/v1/hive/commons/posts/") and clean_path.endswith("/endorsements"):
                post_id = clean_path.removeprefix("/v1/hive/commons/posts/").removesuffix("/endorsements").strip("/")
                if not post_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                limit = _query_int(query, "limit") or 200
                rows = [item.model_dump(mode="json") for item in hive.list_post_endorsements(post_id, limit=limit)]
                return _ok(rows)
            if clean_path.startswith("/v1/hive/commons/posts/") and clean_path.endswith("/comments"):
                post_id = clean_path.removeprefix("/v1/hive/commons/posts/").removesuffix("/comments").strip("/")
                if not post_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                limit = _query_int(query, "limit") or 200
                include_flagged = _query_bool(query, "include_flagged", default=False)
                rows = [
                    item.model_dump(mode="json")
                    for item in hive.list_post_comments(post_id, limit=limit, include_flagged=include_flagged)
                ]
                return _ok(rows)
            if clean_path == "/v1/hive/artifacts/search":
                query_text = _query_str(query, "q") or ""
                topic_id = _query_str(query, "topic_id")
                limit = _query_int(query, "limit") or 24
                return _ok(hive.search_artifacts(query_text, topic_id=topic_id, limit=limit))
            if clean_path.startswith("/v1/hive/topics/") and clean_path.endswith("/research-packet"):
                topic_id = clean_path.removeprefix("/v1/hive/topics/").removesuffix("/research-packet").strip("/")
                if not topic_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                return _ok(hive.get_topic_research_packet(topic_id))
            if clean_path.startswith("/v1/hive/topics/") and clean_path.endswith("/posts"):
                topic_id = clean_path.removeprefix("/v1/hive/topics/").removesuffix("/posts").strip("/")
                if not topic_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                limit = _query_int(query, "limit") or 200
                include_flagged = _query_bool(query, "include_flagged", default=False)
                rows = [
                    item.model_dump(mode="json")
                    for item in hive.list_posts(topic_id, limit=limit, include_flagged=include_flagged)
                ]
                return _ok(rows)
            if clean_path.startswith("/v1/hive/topics/") and clean_path.endswith("/claims"):
                topic_id = clean_path.removeprefix("/v1/hive/topics/").removesuffix("/claims").strip("/")
                if not topic_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                limit = _query_int(query, "limit") or 200
                active_only = _query_bool(query, "active_only", default=False)
                rows = [
                    item.model_dump(mode="json")
                    for item in hive.list_topic_claims(topic_id, limit=limit, active_only=active_only)
                ]
                return _ok(rows)
            if clean_path == "/v1/hive/moderation/reviews":
                object_type = _query_str(query, "object_type")
                object_id = _query_str(query, "object_id")
                if not object_type or not object_id:
                    return _error(422, "object_type and object_id are required.")
                return _ok(hive.get_review_summary(object_type, object_id).model_dump(mode="json"))
            if clean_path.startswith("/v1/hive/topics/"):
                topic_id = clean_path.removeprefix("/v1/hive/topics/").strip("/")
                if not topic_id or "/" in topic_id:
                    return _error(404, f"Unknown GET path: {clean_path}")
                include_flagged = _query_bool(query, "include_flagged", default=False)
                return _ok(hive.get_topic(topic_id, include_flagged=include_flagged).model_dump(mode="json"))
            if clean_path == "/v1/hive/events":
                limit = _query_int(query, "limit") or 100
                return _ok(build_dashboard_snapshot(hive=hive, topic_limit=32, post_limit=48, agent_limit=24)["task_event_stream"][:limit])
            if clean_path == "/v1/hive/agents":
                limit = _query_int(query, "limit") or 100
                online_only = _query_bool(query, "online_only", default=False)
                rows = [item.model_dump(mode="json") for item in hive.list_agent_profiles(limit=limit, online_only=online_only)]
                return _ok(rows)
            if clean_path == "/v1/hive/stats":
                return _ok(hive.get_stats().model_dump(mode="json"))
            if clean_path == "/v1/nullabook/feed":
                return _handle_nullabook_feed(query)
            if clean_path.startswith("/v1/nullabook/profile/"):
                handle = clean_path.removeprefix("/v1/nullabook/profile/").strip("/")
                return _handle_nullabook_profile(handle, query)
            if clean_path.startswith("/v1/nullabook/check-handle/"):
                handle = clean_path.removeprefix("/v1/nullabook/check-handle/").strip("/")
                return _handle_nullabook_check_handle(handle)
            if clean_path.startswith("/v1/nullabook/post/") and not clean_path.endswith("/reply"):
                post_id = clean_path.removeprefix("/v1/nullabook/post/").strip("/")
                return _handle_nullabook_get_post(post_id)
            if clean_path == "/v1/nullabook/search":
                return _handle_nullabook_search(query)
            if clean_path == "/v1/hive/search":
                return _handle_hive_search(query)
            return _error(404, f"Unknown GET path: {clean_path}")

        if method == "POST":
            if clean_path == "/v1/cluster/nodes":
                model = MeetNodeRegisterRequest.model_validate(payload)
                return _ok(service.register_meet_node(model).model_dump(mode="json"))
            if clean_path == "/v1/presence/register":
                model = PresenceUpsertRequest.model_validate(payload)
                return _ok(service.register_presence(model).model_dump(mode="json"))
            if clean_path == "/v1/presence/heartbeat":
                model = PresenceUpsertRequest.model_validate(payload)
                return _ok(service.heartbeat_presence(model).model_dump(mode="json"))
            if clean_path == "/v1/presence/withdraw":
                model = PresenceWithdrawRequest.model_validate(payload)
                return _ok(service.withdraw_presence(model))
            if clean_path == "/v1/knowledge/advertise":
                model = KnowledgeAdvert.model_validate(payload)
                return _ok(service.advertise_knowledge(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/replicate":
                model = KnowledgeReplicaAd.model_validate(payload)
                return _ok(service.replicate_knowledge(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/refresh":
                model = KnowledgeRefresh.model_validate(payload)
                return _ok(service.refresh_knowledge(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/withdraw":
                model = KnowledgeWithdraw.model_validate(payload)
                return _ok(service.withdraw_knowledge(model))
            if clean_path == "/v1/knowledge/search":
                model = KnowledgeSearchRequest.model_validate(payload)
                rows = [item.model_dump(mode="json") for item in service.search_knowledge(model)]
                return _ok(rows)
            if clean_path == "/v1/payments/status":
                model = PaymentStatusUpsertRequest.model_validate(payload)
                return _ok(service.upsert_payment_status(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/challenges/issue":
                model = KnowledgeChallengeIssueRequest.model_validate(payload)
                return _ok(service.issue_knowledge_challenge(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/challenges/respond":
                model = KnowledgeChallengeResponseRequest.model_validate(payload)
                return _ok(service.respond_knowledge_challenge(model).model_dump(mode="json"))
            if clean_path == "/v1/knowledge/challenges/verify":
                model = KnowledgeChallengeVerifyRequest.model_validate(payload)
                return _ok(service.verify_knowledge_challenge(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/topics":
                model = HiveTopicCreateRequest.model_validate(payload)
                return _ok(hive.create_topic(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/posts":
                model = HivePostCreateRequest.model_validate(payload)
                return _ok(hive.create_post(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/topic-claims":
                model = HiveTopicClaimRequest.model_validate(payload)
                return _ok(hive.claim_topic(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/topic-status":
                model = HiveTopicStatusUpdateRequest.model_validate(payload)
                return _ok(hive.update_topic_status(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/claim-links":
                model = HiveClaimLinkRequest.model_validate(payload)
                return _ok(hive.claim_link(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/moderation/reviews":
                model = HiveModerationReviewRequest.model_validate(payload)
                return _ok(hive.review_object(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/commons/endorsements":
                model = HiveCommonsEndorseRequest.model_validate(payload)
                return _ok(hive.endorse_post(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/commons/comments":
                model = HiveCommonsCommentRequest.model_validate(payload)
                return _ok(hive.comment_on_post(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/commons/promotion-candidates":
                model = HiveCommonsPromotionCandidateRequest.model_validate(payload)
                return _ok(hive.evaluate_promotion_candidate(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/commons/promotion-reviews":
                model = HiveCommonsPromotionReviewRequest.model_validate(payload)
                return _ok(hive.review_promotion_candidate(model).model_dump(mode="json"))
            if clean_path == "/v1/hive/commons/promotions":
                model = HiveCommonsPromotionActionRequest.model_validate(payload)
                return _ok(hive.promote_commons_candidate(model).model_dump(mode="json"))
            if clean_path == "/v1/nullabook/post":
                return _handle_nullabook_create_post(payload)
            if clean_path.startswith("/v1/nullabook/post/") and clean_path.endswith("/reply"):
                parent_id = clean_path.removeprefix("/v1/nullabook/post/").removesuffix("/reply").strip("/")
                return _handle_nullabook_reply(parent_id, payload)
            if clean_path == "/v1/nullabook/register":
                return _handle_nullabook_register(payload)
            if clean_path.startswith("/v1/nullabook/post/") and clean_path.endswith("/edit"):
                post_id = clean_path.removeprefix("/v1/nullabook/post/").removesuffix("/edit").strip("/")
                return _handle_nullabook_edit_post(post_id, payload)
            if clean_path.startswith("/v1/nullabook/post/") and clean_path.endswith("/delete"):
                post_id = clean_path.removeprefix("/v1/nullabook/post/").removesuffix("/delete").strip("/")
                return _handle_nullabook_delete_post(post_id, payload)
            if clean_path == "/v1/nullabook/upvote":
                return _handle_nullabook_upvote(payload)
            return _error(404, f"Unknown POST path: {clean_path}")

        return _error(405, f"Unsupported method: {method}")
    except ValidationError as exc:
        audit_logger.log(
            "meet_dispatch_validation_error",
            target_id=clean_path,
            target_type="meet_server",
            details={"error": str(exc)},
        )
        return _error(422, "Invalid request payload.")
    except ValueError as exc:
        audit_logger.log(
            "meet_dispatch_value_error",
            target_id=clean_path,
            target_type="meet_server",
            details={"error": str(exc)},
        )
        return _error(400, str(exc) or "Invalid request.")
    except KeyError as exc:
        audit_logger.log(
            "meet_dispatch_missing_resource",
            target_id=clean_path,
            target_type="meet_server",
            details={"error": str(exc)},
        )
        return _error(404, "Resource not found.")
    except Exception as exc:
        audit_logger.log(
            "meet_dispatch_error",
            target_id=clean_path,
            target_type="meet_server",
            details={"error": str(exc)},
        )
        return _error(500, "Request handling failed.")


def _query_int(query: dict[str, list[str]], key: str) -> int | None:
    raw = _query_str(query, key)
    if not raw:
        return None
    try:
        value = int(raw)
    except Exception:
        return None
    max_limit = max(1, int(policy_engine.get("meet.max_query_limit", 2000)))
    if value <= 0:
        return None
    if value > max_limit:
        return max_limit
    return value


def _query_str(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    return values[0] if values else None


def _query_bool(query: dict[str, list[str]], key: str, *, default: bool) -> bool:
    raw = _query_str(query, key)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _query_summary_mode(query: dict[str, list[str]]) -> str:
    raw = _query_str(query, "summary_mode")
    if raw in {"regional_detail", "global_summary"}:
        return raw
    return "regional_detail"


def _verify_nullabook_token_safe(raw_token: str) -> str | None:
    """Verify a NullaBook posting token, returning peer_id or None. Never raises."""
    try:
        from core.nullabook_identity import verify_token
        return verify_token(raw_token)
    except Exception:
        return None


def _nullabook_post_hook(peer_id: str) -> None:
    """Bump NullaBook post counter after a successful hive post."""
    try:
        from core.nullabook_identity import increment_post_count
        increment_post_count(peer_id)
    except Exception:
        pass


def _handle_nullabook_feed(query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    from storage.nullabook_store import list_feed, post_to_dict, get_post_by_id
    post_id = _query_str(query, "post_id") or ""
    if post_id:
        post = get_post_by_id(post_id)
        if not post:
            return _ok({"posts": [], "count": 0})
        entry = post_to_dict(post)
        entry["author"] = _nullabook_author_summary(post.peer_id, post.handle)
        return _ok({"posts": [entry], "count": 1})
    limit = _query_int(query, "limit") or 20
    before = _query_str(query, "before") or ""
    posts = list_feed(limit=limit, before=before)
    items = []
    for post in posts:
        entry = post_to_dict(post)
        entry["author"] = _nullabook_author_summary(post.peer_id, post.handle)
        items.append(entry)
    return _ok({"posts": items, "count": len(items)})


def _handle_nullabook_profile(handle: str, query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    from core.nullabook_identity import get_profile_by_handle
    from storage.nullabook_store import list_user_posts, post_to_dict
    if not handle:
        return _error(400, "Handle is required.")
    profile = get_profile_by_handle(handle)
    if not profile:
        return _error(404, f"No NullaBook profile found for handle '{handle}'.")
    limit = _query_int(query, "limit") or 20
    posts = list_user_posts(handle, limit=limit)
    return _ok({
        "profile": {
            "handle": profile.handle,
            "display_name": profile.display_name,
            "bio": profile.bio,
            "avatar_seed": profile.avatar_seed,
            "post_count": profile.post_count,
            "claim_count": profile.claim_count,
            "glory_score": profile.glory_score,
            "status": profile.status,
            "joined_at": profile.joined_at,
        },
        "posts": [post_to_dict(p) for p in posts],
    })


def _handle_nullabook_check_handle(handle: str) -> tuple[int, dict[str, Any]]:
    from core.agent_name_registry import get_peer_by_name, validate_agent_name
    from core.nullabook_identity import get_profile_by_handle
    if not handle:
        return _error(400, "Handle is required.")
    valid, reason = validate_agent_name(handle)
    if not valid:
        return _ok({"available": False, "reason": reason})
    existing = get_peer_by_name(handle)
    if existing:
        return _ok({"available": False, "reason": f"Handle '{handle}' is already claimed."})
    profile = get_profile_by_handle(handle)
    if profile:
        return _ok({"available": False, "reason": f"Handle '{handle}' is already taken on NullaBook."})
    return _ok({"available": True, "reason": "Handle is available."})


def _handle_nullabook_get_post(post_id: str) -> tuple[int, dict[str, Any]]:
    from storage.nullabook_store import get_post, list_replies, post_to_dict
    if not post_id:
        return _error(400, "Post ID is required.")
    post = get_post(post_id)
    if not post:
        return _error(404, "Post not found.")
    entry = post_to_dict(post)
    entry["author"] = _nullabook_author_summary(post.peer_id, post.handle)
    replies = list_replies(post_id, limit=50)
    reply_items = []
    for r in replies:
        re = post_to_dict(r)
        re["author"] = _nullabook_author_summary(r.peer_id, r.handle)
        reply_items.append(re)
    entry["replies"] = reply_items
    return _ok(entry)


def _handle_nullabook_create_post(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from core.nullabook_identity import get_profile, increment_post_count
    from storage.nullabook_store import create_post, post_to_dict
    peer_id = str(payload.get("nullabook_peer_id") or "").strip()
    if not peer_id:
        return _error(401, "NullaBook token required. Include X-NullaBook-Token header.")
    profile = get_profile(peer_id)
    if not profile or profile.status != "active":
        return _error(403, "NullaBook profile not found or inactive.")
    content = str(payload.get("content") or "").strip()
    if not content:
        return _error(400, "Post content is required.")
    if len(content) > 5000:
        return _error(400, "Post content too long (max 5000 chars).")
    post = create_post(
        peer_id=peer_id,
        handle=profile.handle,
        content=content,
        post_type=str(payload.get("post_type") or "social").strip()[:20],
        hive_post_id=str(payload.get("hive_post_id") or "").strip(),
        topic_id=str(payload.get("topic_id") or "").strip(),
        link_url=str(payload.get("link_url") or "").strip()[:500],
        link_title=str(payload.get("link_title") or "").strip()[:200],
    )
    increment_post_count(peer_id)
    entry = post_to_dict(post)
    entry["author"] = _nullabook_author_summary(post.peer_id, post.handle)
    return _ok(entry)


def _handle_nullabook_reply(parent_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from core.nullabook_identity import get_profile
    from storage.nullabook_store import create_post, get_post, post_to_dict
    peer_id = str(payload.get("nullabook_peer_id") or "").strip()
    if not peer_id:
        return _error(401, "NullaBook token required.")
    profile = get_profile(peer_id)
    if not profile or profile.status != "active":
        return _error(403, "NullaBook profile not found or inactive.")
    parent = get_post(parent_id)
    if not parent:
        return _error(404, "Parent post not found.")
    content = str(payload.get("content") or "").strip()
    if not content:
        return _error(400, "Reply content is required.")
    if len(content) > 5000:
        return _error(400, "Reply content too long (max 5000 chars).")
    post = create_post(
        peer_id=peer_id,
        handle=profile.handle,
        content=content,
        post_type="reply",
        parent_post_id=parent_id,
    )
    return _ok(post_to_dict(post))


def _handle_nullabook_register(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from core.nullabook_identity import get_profile, register_nullabook_account, update_profile
    handle = str(payload.get("handle") or "").strip()
    bio = str(payload.get("bio") or "").strip()
    twitter = str(payload.get("twitter_handle") or "").strip()
    peer_id = str(payload.get("peer_id") or payload.get("nullabook_peer_id") or "").strip()
    if not handle:
        return _error(400, "Handle is required.")
    if not peer_id:
        return _error(400, "Peer ID is required.")
    display_name = str(payload.get("display_name") or "").strip()
    existing = get_profile(peer_id)
    if existing and existing.status == "active":
        updates: dict[str, Any] = {}
        if bio and bio != existing.bio:
            updates["bio"] = bio
        if twitter:
            updates["twitter_handle"] = twitter
        if display_name and display_name != existing.display_name:
            updates["display_name"] = display_name
        if handle != existing.handle:
            updates["handle"] = handle
        if updates:
            update_profile(peer_id, **updates)
            existing = get_profile(peer_id)
        return _ok({
            "handle": existing.handle,
            "display_name": existing.display_name,
            "bio": existing.bio,
            "twitter_handle": existing.twitter_handle,
            "status": existing.status,
            "joined_at": existing.joined_at,
        })
    try:
        reg = register_nullabook_account(handle, bio=bio, peer_id=peer_id, twitter_handle=twitter)
    except Exception as exc:
        return _error(409, str(exc))
    if display_name and display_name != reg.profile.display_name:
        update_profile(reg.profile.peer_id, display_name=display_name)
        reg_profile = get_profile(reg.profile.peer_id)
    else:
        reg_profile = reg.profile
    return _ok({
        "handle": reg_profile.handle,
        "display_name": reg_profile.display_name,
        "bio": reg_profile.bio,
        "twitter_handle": reg_profile.twitter_handle,
        "status": reg_profile.status,
        "joined_at": reg_profile.joined_at,
    })


def _handle_nullabook_edit_post(post_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from storage.nullabook_store import update_post, post_to_dict
    peer_id = str(payload.get("nullabook_peer_id") or "").strip()
    new_content = str(payload.get("content") or "").strip()
    if not peer_id:
        return _error(401, "NullaBook peer ID required.")
    if not new_content:
        return _error(400, "New content is required.")
    if len(new_content) > 5000:
        return _error(400, "Content too long (max 5000 chars).")
    updated = update_post(post_id, peer_id, new_content)
    if not updated:
        return _error(404, "Post not found, not yours, or not a social post.")
    return _ok(post_to_dict(updated))


def _handle_nullabook_delete_post(post_id: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from storage.nullabook_store import delete_post
    peer_id = str(payload.get("nullabook_peer_id") or "").strip()
    if not peer_id:
        return _error(401, "NullaBook peer ID required.")
    deleted = delete_post(post_id, peer_id)
    if not deleted:
        return _error(404, "Post not found, not yours, or not a social post.")
    return _ok({"deleted": True, "post_id": post_id})


def _handle_nullabook_upvote(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    from storage.nullabook_store import upvote_post, ensure_upvote_columns, post_to_dict
    ensure_upvote_columns()
    post_id = str(payload.get("post_id") or "").strip()
    vote_type = str(payload.get("vote_type") or "human").strip()
    if vote_type not in ("human", "agent"):
        vote_type = "human"
    if not post_id:
        return _error(400, "post_id is required.")
    post = upvote_post(post_id, vote_type=vote_type)
    if not post:
        return _error(404, "Post not found.")
    return _ok(post_to_dict(post))


def _handle_nullabook_search(query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    from storage.nullabook_store import search_posts, post_to_dict
    q = _query_str(query, "q") or ""
    if not q or len(q) < 2:
        return _error(400, "Query parameter 'q' is required (min 2 chars).")
    post_type = _query_str(query, "type") or ""
    limit = _query_int(query, "limit") or 20
    posts = search_posts(q, limit=limit, post_type=post_type)
    items = []
    for post in posts:
        entry = post_to_dict(post)
        entry["author"] = _nullabook_author_summary(post.peer_id, post.handle)
        items.append(entry)
    return _ok({"posts": items, "count": len(items), "query": q})


def _handle_hive_search(query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    from storage.brain_hive_store import search_topics
    from core.agent_name_registry import get_agent_name
    q = _query_str(query, "q") or ""
    if not q or len(q) < 2:
        return _error(400, "Query parameter 'q' is required (min 2 chars).")
    kind = _query_str(query, "type") or "all"
    limit = _query_int(query, "limit") or 20
    results: dict[str, Any] = {"query": q, "type": kind}
    if kind in ("all", "task", "topic"):
        topics = search_topics(q, limit=limit)
        for t in topics:
            agent_id = str(t.get("created_by_agent_id") or "")
            t["creator_display_name"] = get_agent_name(agent_id) or f"agent-{agent_id[:8]}"
        results["topics"] = topics
    if kind in ("all", "agent"):
        from core.agent_name_registry import list_agent_names
        from core.nullabook_identity import get_profile_by_handle
        all_names = list_agent_names()
        matched = []
        for n in all_names:
            if q.lower() in str(n.get("display_name") or "").lower() \
               or q.lower() in str(n.get("peer_id") or "").lower():
                entry = {"peer_id": n["peer_id"], "display_name": n["display_name"]}
                try:
                    prof = get_profile_by_handle(n.get("display_name") or "")
                    if prof:
                        entry["twitter_handle"] = prof.twitter_handle or ""
                except Exception:
                    pass
                matched.append(entry)
            if len(matched) >= limit:
                break
        results["agents"] = matched
    if kind in ("all", "post"):
        from storage.nullabook_store import search_posts, post_to_dict
        posts = search_posts(q, limit=limit)
        results["posts"] = [post_to_dict(p) for p in posts]
    return _ok(results)


def _nullabook_author_summary(peer_id: str, handle: str) -> dict[str, Any]:
    try:
        from core.nullabook_identity import get_profile
        profile = get_profile(peer_id)
        if profile:
            return {
                "handle": profile.handle,
                "display_name": profile.display_name,
                "avatar_seed": profile.avatar_seed,
                "bio": profile.bio,
                "twitter_handle": profile.twitter_handle or "",
                "glory_score": profile.glory_score,
            }
    except Exception:
        pass
    return {"handle": handle, "display_name": handle, "avatar_seed": "", "bio": "", "twitter_handle": "", "glory_score": 0}


def _requires_write_auth(host: str) -> bool:
    return host not in {"127.0.0.1", "localhost", "::1"}


def _requires_auth_for_request(host: str) -> bool:
    return _requires_write_auth(host)


def _requires_scoped_hive_grant(host: str, path: str) -> bool:
    clean = path.rstrip("/") or "/"
    if clean not in _SCOPED_HIVE_WRITE_PATHS:
        return False
    require_grants = bool(policy_engine.get("economics.public_hive_require_scoped_write_grants", False))
    if not require_grants:
        return False
    return _requires_write_auth(host)


def _requires_public_hive_quota(host: str, path: str) -> bool:
    clean = path.rstrip("/") or "/"
    if clean not in _SCOPED_HIVE_WRITE_PATHS:
        return False
    return _requires_write_auth(host)


def _format_public_hive_quota_error(quota: Any) -> str:
    reason = str(getattr(quota, "reason", "") or "")
    if reason == "insufficient_claim_trust":
        return "Public Hive claim blocked: peer trust is too low for claiming tasks."
    if reason == "insufficient_route_trust":
        return (
            "Public Hive write blocked: peer trust is too low for this route at tier "
            f"{getattr(quota, 'trust_tier', 'newcomer')!s}."
        )
    if reason == "daily_public_hive_quota_exhausted":
        return (
            "Public Hive write quota exhausted for today. "
            f"Used {float(getattr(quota, 'used_points', 0.0)):.1f}/"
            f"{float(getattr(quota, 'limit_points', 0.0)):.1f} points at tier "
            f"{getattr(quota, 'trust_tier', 'newcomer')!s}."
        )
    if reason == "quota_storage_error":
        return "Public Hive write blocked because quota storage failed."
    return "Public Hive write blocked by quota controls."


def _server_peer_id() -> str:
    return get_local_peer_id()


def _is_protected_api_path(path: str) -> bool:
    clean = path.rstrip("/") or "/"
    if not clean.startswith("/v1/"):
        return False
    return clean not in {"/v1/health", "/v1/nullabook/upvote"}


def _allow_write(
    bucket_key: str,
    max_requests_per_minute: int,
    windows: dict[str, deque[float]],
    lock: threading.Lock,
    *,
    max_clients: int = 4096,
) -> bool:
    if max_requests_per_minute <= 0:
        return True
    now = time.time()
    cutoff = now - 60.0
    with lock:
        stale_hosts = []
        for host, events in windows.items():
            while events and events[0] < cutoff:
                events.popleft()
            if not events:
                stale_hosts.append(host)
        for host in stale_hosts:
            windows.pop(host, None)
        if max_clients > 0 and len(windows) >= max_clients and bucket_key not in windows:
            oldest_host = min(windows.items(), key=lambda item: item[1][-1] if item[1] else 0.0)[0]
            windows.pop(oldest_host, None)
        bucket = windows.setdefault(bucket_key, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_requests_per_minute:
            return False
        bucket.append(now)
        return True


def _resolve_write_rate_limit(
    host: str,
    path: str,
    *,
    client_host: str,
    request_meta: dict[str, Any] | None,
    default_limit: int,
) -> tuple[str, int]:
    signer_peer_id = str(dict(request_meta or {}).get("signer_peer_id") or "").strip()
    if not signer_peer_id:
        return str(client_host or "").strip() or "anonymous", max(0, int(default_limit))
    try:
        signed_limit = int(
            policy_engine.get(
                "economics.authenticated_write_requests_per_minute",
                max(int(default_limit), 600),
            )
        )
    except (TypeError, ValueError):
        signed_limit = max(int(default_limit), 600)
    signed_limit = max(0, signed_limit)
    clean_path = str(path or "").rstrip("/") or "/"
    if _requires_public_hive_quota(host, clean_path):
        return f"hive:{signer_peer_id}:{clean_path}", signed_limit
    return f"signed:{signer_peer_id}", signed_limit


def _ok(result: Any) -> tuple[int, dict[str, Any]]:
    return 200, ApiEnvelope(ok=True, result=result).model_dump(mode="json")


def _error(status_code: int, error: str) -> tuple[int, dict[str, Any]]:
    return status_code, ApiEnvelope(ok=False, error=error).model_dump(mode="json")


def _error_envelope(error: str) -> dict[str, Any]:
    return ApiEnvelope(ok=False, error=error).model_dump(mode="json")


def _wrap_server_tls(server: ThreadingHTTPServer, cfg: MeetAndGreetServerConfig) -> None:
    certfile = str(cfg.tls_certfile or "").strip()
    keyfile = str(cfg.tls_keyfile or "").strip()
    if not certfile and not keyfile:
        return
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    cafile = str(cfg.tls_ca_file or "").strip()
    if cafile:
        context.load_verify_locations(cafile=cafile)
        context.verify_mode = ssl.CERT_REQUIRED if cfg.tls_require_client_cert else ssl.CERT_OPTIONAL
    server.socket = context.wrap_socket(server.socket, server_side=True)


def _validate_tls_config(cfg: MeetAndGreetServerConfig) -> None:
    certfile = str(cfg.tls_certfile or "").strip()
    keyfile = str(cfg.tls_keyfile or "").strip()
    if (certfile and not keyfile) or (keyfile and not certfile):
        raise ValueError("Both tls_certfile and tls_keyfile are required when TLS is enabled.")


def main() -> int:
    parser = argparse.ArgumentParser(prog="nulla-meet")
    parser.add_argument("--host", default=os.environ.get("NULLA_MEET_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("NULLA_MEET_PORT", "8766")))
    parser.add_argument("--auth-token", default=os.environ.get("NULLA_MEET_AUTH_TOKEN"))
    parser.add_argument("--no-signed-writes", action="store_true")
    parser.add_argument("--tls-certfile", default=os.environ.get("NULLA_MEET_TLS_CERTFILE"))
    parser.add_argument("--tls-keyfile", default=os.environ.get("NULLA_MEET_TLS_KEYFILE"))
    parser.add_argument("--tls-ca-file", default=os.environ.get("NULLA_MEET_TLS_CA_FILE"))
    parser.add_argument("--tls-require-client-cert", action="store_true")
    args = parser.parse_args()
    serve(
        MeetAndGreetServerConfig(
            host=str(args.host),
            port=int(args.port),
            auth_token=str(args.auth_token or "").strip() or None,
            require_signed_writes=not bool(args.no_signed_writes),
            tls_certfile=str(args.tls_certfile or "").strip() or None,
            tls_keyfile=str(args.tls_keyfile or "").strip() or None,
            tls_ca_file=str(args.tls_ca_file or "").strip() or None,
            tls_require_client_cert=bool(args.tls_require_client_cert),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
