from __future__ import annotations

import argparse
import json
import signal
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from core import audit_logger, policy_engine
from core.bootstrap_adapters import BootstrapMirrorAdapter
from core.capability_tokens import (
    expire_stale_capability_tokens,
    mark_capability_token_used,
    revoke_capability_tokens_for_task,
    verify_assignment_capability,
)
from core.discovery_index import (
    endpoint_for_peer,
    peer_trust,
    recent_peer_endpoints,
    register_capability_ad,
    register_peer_endpoint,
    same_host_group_suspect,
    upsert_peer_minimal,
)
from core.helper_scheduler import HelperScheduler, SchedulerConfig
from core.idle_assist_policy import IdleAssistConfig
from core.knowledge_advertiser import broadcast_hello, broadcast_local_knowledge_ads, broadcast_presence_heartbeat
from core.knowledge_registry import load_shareable_shard_payload, register_local_shard, sync_local_learning_shards
from core.liquefy_bridge import apply_local_execution_safety
from core.local_worker_pool import resolve_local_worker_capacity
from core.logging_config import setup_logging
from core.maintenance import MaintenanceConfig, MaintenanceLoop
from core.parent_orchestrator import continue_parent_orchestration
from core.result_reviewer import auto_review_task_result
from core.task_state_machine import current_state, transition
from core.timeout_policy import reap_stale_subtasks
from core.user_preferences import hive_task_intake_enabled
from network.assist_models import AssistFilters, CapabilityAd
from network.assist_router import (
    build_capability_ad_message,
    build_task_assign_message,
    build_task_progress_message,
    handle_incoming_assist_message,
    load_task_capsule_for_task,
    load_task_offer_payload,
    persist_task_assignment,
    pick_best_claim_for_task,
    prepare_task_assignment,
)
from network.knowledge_models import validate_knowledge_payload
from network.knowledge_router import handle_knowledge_message
from network.pow_hashcash import generate_pow, required_pow_difficulty
from network.presence_router import handle_presence_message
from network.protocol import Envelope, Protocol, encode_message, peek_message_type, validate_payload, verify_signature
from network.rate_limiter import allow as rate_allow
from network.signer import get_local_peer_id as local_peer_id
from network.transport import TransportRuntime, UDPTransportServer, send_message
from retrieval.swarm_query import request_specific_shard
from sandbox.helper_worker import run_task_capsule
from storage.db import get_connection
from storage.migrations import run_migrations


def _is_loopback_host(host: str) -> bool:
    candidate = str(host or "").strip().lower()
    return candidate in {"127.0.0.1", "localhost", "::1"}


@dataclass
class NodeRuntime:
    host: str
    port: int
    public_host: str
    public_port: int
    running: bool


@dataclass
class DaemonConfig:
    bind_host: str = "0.0.0.0"
    bind_port: int = 49152
    advertise_host: str = "127.0.0.1"
    capabilities: list[str] = field(default_factory=lambda: [
        "research",
        "classification",
        "ranking",
        "validation",
    ])
    capacity: int = 2
    assist_status: str = "idle"
    local_host_group_hint_hash: str | None = None
    bootstrap_topics: list[str] = field(default_factory=lambda: ["knowledge_presence", "safe_orchestration", "local_first"])
    bootstrap_adapter: BootstrapMirrorAdapter | None = None
    maintenance_tick_seconds: int = 30
    auto_request_shards_per_response: int = 2
    health_bind_host: str = "127.0.0.1"
    health_bind_port: int = 0
    health_auth_token: str | None = None
    # Phase 30: Hardware Benchmarking & CAS Model Hashes
    compute_class: str = "cpu_basic"
    supported_models: list[str] = field(default_factory=list)
    local_worker_threads: int | None = None

class NullaDaemon:
    def __init__(self, config: DaemonConfig | None = None):
        self.config = config or DaemonConfig()
        self.transport: UDPTransportServer | None = None
        self.maintenance: MaintenanceLoop | None = None
        self.local_capability_ad: CapabilityAd | None = None
        self._order_book_running = False
        self._order_book_thread: threading.Thread | None = None
        self._health_server: ThreadingHTTPServer | None = None
        self._health_thread: threading.Thread | None = None
        self._runtime: TransportRuntime | None = None
        initial_workers = int(self.config.local_worker_threads or max(2, int(self.config.capacity) * 2))
        self._local_worker_limit = max(1, initial_workers)
        self._local_worker_sem = threading.BoundedSemaphore(self._local_worker_limit)
        self._helper_scheduler = HelperScheduler(
            SchedulerConfig(
                max_concurrent_mesh_tasks=max(1, int(self.config.capacity)),
                reserve_capacity_for_local_user=True,
            )
        )

    def start(self) -> NodeRuntime:
        setup_logging(
            level=str(policy_engine.get("observability.log_level", "INFO")),
            json_output=bool(policy_engine.get("observability.json_logs", True)),
        )
        run_migrations()
        policy_engine.load(force_reload=True)
        configured_worker_limit = int(
            self.config.local_worker_threads
            if self.config.local_worker_threads is not None
            else policy_engine.get("runtime.max_local_worker_threads", max(2, int(self.config.capacity) * 2))
        )
        self._local_worker_limit = max(1, configured_worker_limit)
        self._local_worker_sem = threading.BoundedSemaphore(self._local_worker_limit)
        self._helper_scheduler = HelperScheduler(
            SchedulerConfig(
                max_concurrent_mesh_tasks=max(1, int(self.config.capacity)),
                reserve_capacity_for_local_user=bool(
                    policy_engine.get("assist_mesh.reserve_capacity_for_local_user", True)
                ),
            )
        )
        if not str(self.config.health_auth_token or "").strip():
            token = str(policy_engine.get("runtime.health_auth_token", "") or "").strip()
            self.config.health_auth_token = token or None
        if int(self.config.health_bind_port) > 0 and not _is_loopback_host(self.config.health_bind_host):
            if not str(self.config.health_auth_token or "").strip():
                raise ValueError("Non-loopback daemon health endpoint requires a health_auth_token.")

        # Phase 30: Generate Sybil Resistance Proof-of-Work
        audit_logger.log("daemon_genesis_pow", target_id=local_peer_id(), target_type="daemon", details={"status": "calculating"})
        pow_difficulty = required_pow_difficulty(default=4)
        genesis_nonce = generate_pow(local_peer_id(), target_difficulty=pow_difficulty)
        self._refresh_assist_status()

        self.local_capability_ad = CapabilityAd(
            agent_id=local_peer_id(),
            status=self.config.assist_status,
            capabilities=self.config.capabilities,
            compute_class=self.config.compute_class,
            supported_models=self.config.supported_models,
            capacity=self.config.capacity,
            trust_score=0.50,
            assist_filters=AssistFilters(
                allow_research=True,
                allow_code_reasoning=False,
                allow_validation=True,
                min_reward_points=0,
                trusted_peers_only=False,
                host_group_hint_hash=self.config.local_host_group_hint_hash,
            ),
            pow_difficulty=pow_difficulty,
            genesis_nonce=genesis_nonce,
            timestamp=datetime.now(timezone.utc),
        )
        register_capability_ad(self.local_capability_ad)
        sync_local_learning_shards(limit=500)

        self.transport = UDPTransportServer(
            host=self.config.bind_host,
            port=self.config.bind_port,
            on_message=self._on_message,
        )
        runtime = self.transport.start()
        self._runtime = runtime
        from network.bootstrap_node import upsert_bootstrap_peer
        from network.nat_probe import classify_nat, detect_local_host
        from network.relay_fallback import choose_relay_mode

        nat_probe = classify_nat(detect_local_host(), runtime.public_host, runtime.public_port)
        relay_mode = choose_relay_mode(
            direct_reachable=nat_probe.mode in {"wan_direct", "wan_mapped", "lan_only"},
            relay_available=nat_probe.mode != "wan_direct",
            peer_id=local_peer_id(),
        )

        # Phase 30: NAT Traversal - Update DHT with discovered/public endpoint.
        configured_advertise = str(self.config.advertise_host or "").strip()
        if configured_advertise and configured_advertise not in {"127.0.0.1", "localhost", "0.0.0.0"}:
            advertised_host = configured_advertise
            advertised_port = int(self.config.bind_port)
        else:
            advertised_host = runtime.public_host
            advertised_port = runtime.public_port
        register_peer_endpoint(
            local_peer_id(),
            advertised_host,
            advertised_port,
            source="self",
        )
        upsert_bootstrap_peer(local_peer_id(), advertised_host, advertised_port, relay_mode.mode)

        self.maintenance = MaintenanceLoop(
            config=MaintenanceConfig(
                tick_seconds=self.config.maintenance_tick_seconds,
                bootstrap_topics=self.config.bootstrap_topics,
                bootstrap_adapter=self.config.bootstrap_adapter,
            ),
            capability_ad_builder=self._build_capability_ad_message,
            presence_broadcast=lambda: broadcast_presence_heartbeat(
                capabilities=self.config.capabilities,
                status=self.config.assist_status,
                transport_mode=relay_mode.mode,
            ),
            knowledge_broadcast=lambda: broadcast_local_knowledge_ads(),
            local_bind_host=advertised_host,
            local_bind_port=advertised_port,
        )
        self.maintenance.start()
        broadcast_hello(
            agent_name=None,
            capabilities=self.config.capabilities,
            status=self.config.assist_status,
            transport_mode=relay_mode.mode,
        )
        broadcast_local_knowledge_ads()

        # Phase 27: Start Order Book worker
        self._order_book_running = True
        self._order_book_thread = threading.Thread(target=self._run_order_book_loop, daemon=True)
        self._order_book_thread.start()
        self._start_health_server()

        audit_logger.log(
            "daemon_started",
            target_id=f"{runtime.public_host}:{runtime.public_port}",
            target_type="daemon",
            details={
                "peer_id": local_peer_id(),
                "local_bind": f"{self.config.bind_host}:{self.config.bind_port}",
                "stun_public": f"{runtime.public_host}:{runtime.public_port}",
                "nat_mode": nat_probe.mode,
                "connectivity_mode": relay_mode.mode,
            },
        )

        return NodeRuntime(
            host=runtime.host,
            port=runtime.port,
            public_host=runtime.public_host,
            public_port=runtime.public_port,
            running=True,
        )

    def stop(self) -> None:
        if self.maintenance:
            self.maintenance.stop()
        if self.transport:
            self.transport.stop()

        self._order_book_running = False
        if self._order_book_thread:
            self._order_book_thread.join(timeout=2.0)
        if self._health_server:
            self._health_server.shutdown()
            self._health_server.server_close()
        if self._health_thread and self._health_thread.is_alive():
            self._health_thread.join(timeout=2.0)

        audit_logger.log(
            "daemon_stopped",
            target_id=local_peer_id(),
            target_type="daemon",
            details={},
        )

    def _start_health_server(self) -> None:
        if int(self.config.health_bind_port) <= 0:
            return
        daemon = self

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.rstrip("/") not in {"/healthz", "/v1/healthz"}:
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":false,"error":"not_found"}')
                    return
                require_auth = (not _is_loopback_host(daemon.config.health_bind_host)) or bool(
                    str(daemon.config.health_auth_token or "").strip()
                )
                if require_auth:
                    header_token = str(self.headers.get("X-Nulla-Health-Token") or "").strip()
                    expected = str(daemon.config.health_auth_token or "").strip()
                    if not expected or header_token != expected:
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"ok":false,"error":"unauthorized"}')
                        return
                body = json.dumps({"ok": True, "result": daemon._health_snapshot()}, sort_keys=True).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                del format, args
                return

        self._health_server = ThreadingHTTPServer((self.config.health_bind_host, int(self.config.health_bind_port)), HealthHandler)
        self._health_thread = threading.Thread(target=self._health_server.serve_forever, name="nulla-daemon-health", daemon=True)
        self._health_thread.start()

    def _health_snapshot(self) -> dict[str, Any]:
        runtime = self._runtime
        return {
            "peer_id": local_peer_id(),
            "running": True,
            "bind_host": self.config.bind_host,
            "bind_port": int(self.config.bind_port),
            "public_host": runtime.public_host if runtime else None,
            "public_port": int(runtime.public_port) if runtime else None,
            "active_assignments": int(self._active_assignment_count()),
            "capacity": int(self.config.capacity),
            "advertised_capacity": int(self._refresh_advertised_capacity()),
            "maintenance_running": bool(self.maintenance is not None),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _run_order_book_loop(self) -> None:
        from core.credit_dex import check_and_generate_credit_offer
        from core.liquefy_bridge import stream_telemetry_event
        from core.order_book import global_order_book
        from network.assist_router import build_task_claim_message
        from retrieval.swarm_query import broadcast_credit_offer

        last_credit_check_time = 0.0
        last_reconcile_time = 0.0

        while self._order_book_running:
            time.sleep(1.0)
            try:
                accepts_hive_tasks = self._refresh_assist_status()
                advertised_capacity = self._refresh_advertised_capacity()

                # Phase 29: Periodically check if we have excess credits to auto-sell
                now_time = time.time()
                if now_time - last_credit_check_time > 30.0:
                    last_credit_check_time = now_time
                    offer_dict = check_and_generate_credit_offer()
                    if offer_dict:
                        broadcast_credit_offer(offer_dict)
                reconcile_interval = max(5.0, float(policy_engine.get("assist_mesh.reconcile_interval_seconds", 10)))
                if now_time - last_reconcile_time >= reconcile_interval:
                    last_reconcile_time = now_time
                    self._reconcile_mesh_state()

                if not self.local_capability_ad:
                    continue

                current_assignments = self._active_assignment_count()
                if current_assignments >= advertised_capacity:
                    continue

                if not accepts_hive_tasks:
                    continue

                if not self._helper_scheduler.can_accept_mesh_task():
                    continue

                available_slots = max(0, int(advertised_capacity) - int(current_assignments))
                if available_slots <= 0:
                    continue

                for _ in range(available_slots):
                    best_offer = global_order_book.pop_best_offer()
                    if not best_offer:
                        break

                    task_id = best_offer.offer_dict.get("task_id")
                    parent_peer_id = best_offer.offer_dict.get("parent_agent_id", "")

                    if not task_id or not parent_peer_id:
                        continue

                    claim_msg = build_task_claim_message(
                        task_id=task_id,
                        declared_capabilities=self.local_capability_ad.capabilities,
                        current_load=current_assignments,
                        host_group_hint_hash=self.local_capability_ad.assist_filters.host_group_hint_hash,
                    )

                    endpoint = endpoint_for_peer(parent_peer_id)
                    host, port = endpoint if endpoint else best_offer.source_addr
                    if self._send_or_log(
                        host,
                        int(port),
                        claim_msg,
                        message_type="TASK_CLAIM",
                        target_id=str(task_id),
                    ):
                        current_assignments += 1
                        stream_telemetry_event("ORDER_BOOK_CLAIM", task_id, {"bid_price": best_offer.bid_price})
            except Exception as exc:
                audit_logger.log(
                    "order_book_loop_error",
                    target_id=local_peer_id(),
                    target_type="daemon",
                    details={"error": str(exc)},
                )
                time.sleep(0.2)

    def _build_capability_ad_message(self) -> bytes:
        self._refresh_assist_status()
        advertised_capacity = self._refresh_advertised_capacity()
        return build_capability_ad_message(
            status=self.config.assist_status,
            capabilities=self.config.capabilities,
            compute_class=self.config.compute_class,
            supported_models=self.config.supported_models,
            capacity=advertised_capacity,
            trust_score=peer_trust(local_peer_id()),
            assist_filters={
                "allow_research": True,
                "allow_code_reasoning": False,
                "allow_validation": True,
                "min_reward_points": 0,
                "trusted_peers_only": False,
                "host_group_hint_hash": self.config.local_host_group_hint_hash,
            },
            pow_difficulty=self.local_capability_ad.pow_difficulty if self.local_capability_ad else required_pow_difficulty(default=4),
            genesis_nonce=self.local_capability_ad.genesis_nonce if self.local_capability_ad else "",
        )

    def _on_message(self, raw: bytes, addr: tuple[str, int]) -> None:
        msg_type = peek_message_type(raw)
        if not msg_type:
            audit_logger.log(
                "incoming_message_rejected",
                target_id=f"{addr[0]}:{addr[1]}",
                target_type="network",
                details={"error": "unable_to_peek_msg_type"},
            )
            return

        assist_types = {
            "CAPABILITY_AD",
            "TASK_OFFER",
            "TASK_CLAIM",
            "TASK_ASSIGN",
            "TASK_PROGRESS",
            "TASK_RESULT",
            "TASK_REVIEW",
            "TASK_REWARD",
            "TASK_CANCEL",
            "FIND_NODE",
            "NODE_FOUND",
            "FIND_BLOCK",
            "BLOCK_FOUND",
            "REQUEST_BLOCK",
            "BLOCK_PAYLOAD",
            "CREDIT_OFFER",
            "CREDIT_TRANSFER",
        }

        # Assist path: let assist router decode once.
        if msg_type in assist_types:
            self._refresh_assist_status()
            result = handle_incoming_assist_message(
                raw_bytes=raw,
                source_addr=addr,
                local_capability_ad=self.local_capability_ad,
                idle_assist_config=self._idle_assist_config(),
                local_current_assignments=self._active_assignment_count(),
                parent_trust_lookup=peer_trust,
                same_host_group_lookup=lambda remote_peer_id: same_host_group_suspect(
                    self.config.local_host_group_hint_hash,
                    remote_peer_id,
                ),
            )

            for msg in result.generated_messages:
                self._send_or_log(addr[0], int(addr[1]), msg, message_type="ASSIST_REPLY", target_id=f"{addr[0]}:{addr[1]}")

            if not result.ok:
                return

            # If this was an assignment for us, execute it locally.
            if msg_type == "TASK_ASSIGN":
                self._spawn_limited_worker(
                    target=self._maybe_execute_local_assignment_from_raw,
                    args=(raw, addr),
                    name="nulla-local-assignment",
                    target_id=f"{addr[0]}:{addr[1]}",
                )

            # If this was a result sent to us as the parent, auto-review locally and reply.
            if msg_type == "TASK_RESULT":
                self._spawn_limited_worker(
                    target=self._maybe_auto_review_result_from_raw,
                    args=(raw, addr),
                    name="nulla-local-review",
                    target_id=f"{addr[0]}:{addr[1]}",
                )

            return

        # Non-assist path: decode exactly once here.
        try:
            envelope = Protocol.decode_and_validate(raw)
        except Exception as e:
            audit_logger.log(
                "incoming_message_rejected",
                target_id=f"{addr[0]}:{addr[1]}",
                target_type="network",
                details={"error": str(e)},
            )
            return

        sender = str(envelope["sender_peer_id"])
        if not rate_allow(sender):
            audit_logger.log(
                "incoming_non_assist_rate_limited",
                target_id=sender,
                target_type="peer",
                details={"msg_type": str(envelope.get("msg_type") or "")},
            )
            return
        upsert_peer_minimal(sender)
        register_peer_endpoint(sender, addr[0], int(addr[1]), source="observed")

        msg_type = str(envelope["msg_type"])
        payload = envelope.get("payload") or {}

        if msg_type == "PING":
            self._reply_basic("HEARTBEAT", {}, addr)
            return

        if msg_type == "HEARTBEAT":
            return

        if msg_type in {"HELLO_AD", "PRESENCE_HEARTBEAT"}:
            payload_model = validate_knowledge_payload(msg_type, payload)
            handle_presence_message(msg_type, payload_model)
            return

        if msg_type in {
            "KNOWLEDGE_AD",
            "KNOWLEDGE_WITHDRAW",
            "KNOWLEDGE_FETCH_REQUEST",
            "KNOWLEDGE_FETCH_OFFER",
            "KNOWLEDGE_REPLICA_AD",
            "KNOWLEDGE_REFRESH",
            "KNOWLEDGE_TOMBSTONE",
        }:
            payload_model = validate_knowledge_payload(msg_type, payload)
            result = handle_knowledge_message(msg_type, payload_model)
            for msg in result.generated_messages:
                self._send_or_log(addr[0], int(addr[1]), msg, message_type=msg_type, target_id=f"{addr[0]}:{addr[1]}")
            return

        if msg_type == "QUERY_SHARD":
            self._handle_query_shard(payload, addr)
            return

        if msg_type == "SHARD_CANDIDATES":
            self._handle_shard_candidates(payload, sender)
            return

        if msg_type == "REQUEST_SHARD":
            self._handle_request_shard(payload, addr)
            return

        if msg_type == "SHARD_PAYLOAD":
            self._handle_shard_payload(payload, sender)
            return

        if msg_type == "REPORT_ABUSE":
            self._handle_report_abuse(payload, sender, addr)
            return

    def _reply_basic(self, msg_type: str, payload: dict[str, Any], addr: tuple[str, int]) -> None:
        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type=msg_type,
            sender_peer_id=local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload=payload,
        )
        self._send_or_log(addr[0], int(addr[1]), raw, message_type=msg_type, target_id=f"{addr[0]}:{addr[1]}")

    def _handle_report_abuse(self, payload: dict[str, Any], sender: str, addr: tuple[str, int]) -> None:
        from core.fraud_engine import record_signal
        from storage.abuse_gossip_store import allow_reporter_report, mark_report_seen

        report_id = str(payload.get("report_id") or "").strip()
        if not report_id:
            return
        if not mark_report_seen(report_id):
            return
        per_minute_limit = int(policy_engine.get("network.report_abuse_max_reports_per_minute", 8))
        if not allow_reporter_report(sender, per_minute_limit=per_minute_limit):
            audit_logger.log(
                "report_abuse_rate_limited",
                target_id=report_id,
                target_type="anti_abuse",
                details={"reporter_peer_id": sender},
            )
            return

        min_reporter_trust = float(policy_engine.get("network.report_abuse_min_reporter_trust", 0.25))
        reporter_trust = float(peer_trust(sender))
        if reporter_trust < min_reporter_trust:
            audit_logger.log(
                "report_abuse_rejected_low_trust",
                target_id=report_id,
                target_type="anti_abuse",
                details={"reporter_peer_id": sender, "reporter_trust": reporter_trust},
            )
            return

        accused_peer_id = str(payload.get("accused_peer_id") or "").strip() or None
        signal_type = str(payload.get("signal_type") or "reported_abuse").strip()
        severity = float(payload.get("severity") or 0.0)
        task_id = str(payload.get("task_id") or "").strip() or None
        if task_id and not self._reporter_related_to_task(sender, task_id):
            audit_logger.log(
                "report_abuse_rejected_unrelated_reporter",
                target_id=report_id,
                target_type="anti_abuse",
                details={"reporter_peer_id": sender, "task_id": task_id},
            )
            return

        details = dict(payload.get("details") or {})
        details["report_id"] = report_id
        details["reporter_peer_id"] = sender
        details["source_addr"] = f"{addr[0]}:{addr[1]}"

        record_signal(
            peer_id=accused_peer_id,
            related_peer_id=sender,
            task_id=task_id,
            signal_type=f"gossip_{signal_type}",
            severity=severity,
            details=details,
        )

        ttl = int(payload.get("ttl") or policy_engine.get("network.report_abuse_gossip_ttl", 2))
        if ttl <= 0:
            return
        next_ttl = max(0, ttl - 1)
        fanout = int(policy_engine.get("network.report_abuse_gossip_fanout", 8))
        forward_payload = dict(payload)
        forward_payload["ttl"] = next_ttl
        forward_raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="REPORT_ABUSE",
            sender_peer_id=local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload=forward_payload,
        )
        forwarded = 0
        for peer_id, host, port in recent_peer_endpoints(exclude_peer_id=local_peer_id(), limit=max(16, fanout * 2)):
            if peer_id == sender:
                continue
            if self._send_or_log(host, int(port), forward_raw, message_type="REPORT_ABUSE", target_id=report_id):
                forwarded += 1
            if forwarded >= fanout:
                break

        audit_logger.log(
            "report_abuse_gossiped",
            target_id=report_id,
            target_type="anti_abuse",
            details={
                "forwarded": forwarded,
                "next_ttl": next_ttl,
                "accused_peer_id": accused_peer_id,
                "signal_type": signal_type,
            },
        )

    def _reporter_related_to_task(self, reporter_peer_id: str, task_id: str) -> bool:
        reporter = str(reporter_peer_id or "").strip()
        task = str(task_id or "").strip()
        if not reporter or not task:
            return False
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT 1
                FROM (
                    SELECT parent_peer_id AS peer_id FROM task_offers WHERE task_id = ?
                    UNION ALL
                    SELECT helper_peer_id AS peer_id FROM task_claims WHERE task_id = ?
                    UNION ALL
                    SELECT helper_peer_id AS peer_id FROM task_assignments WHERE task_id = ?
                    UNION ALL
                    SELECT helper_peer_id AS peer_id FROM task_results WHERE task_id = ?
                    UNION ALL
                    SELECT reviewer_peer_id AS peer_id FROM task_reviews WHERE task_id = ?
                ) participants
                WHERE peer_id = ?
                LIMIT 1
                """,
                (task, task, task, task, task, reporter),
            ).fetchone()
            return bool(row)
        finally:
            conn.close()

    def _active_assignment_count(self) -> int:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM task_assignments
                WHERE helper_peer_id = ?
                  AND status = 'active'
                """,
                (local_peer_id(),),
            ).fetchone()
            return int(row["cnt"]) if row else 0
        finally:
            conn.close()

    def _accepts_hive_tasks(self) -> bool:
        try:
            return bool(hive_task_intake_enabled())
        except Exception:
            return True

    def _effective_assist_status(self) -> str:
        current_assignments = self._active_assignment_count()
        if current_assignments > 0:
            return "busy"
        if not self._accepts_hive_tasks():
            return "limited"
        return "idle"

    def _refresh_assist_status(self) -> bool:
        accepts_hive_tasks = self._accepts_hive_tasks()
        status = self._effective_assist_status()
        self.config.assist_status = status
        if self.local_capability_ad is not None:
            self.local_capability_ad.status = status
        return accepts_hive_tasks

    def _idle_assist_config(self) -> IdleAssistConfig:
        accepts_hive_tasks = self._accepts_hive_tasks()
        return IdleAssistConfig(
            mode="passive" if accepts_hive_tasks else "off",
            max_concurrent_tasks=self.config.capacity,
            trusted_peers_only=False,
            min_reward_points=0,
            allow_research=True,
            allow_code_reasoning=False,
            allow_validation=True,
            strict_privacy_only=True,
            require_idle_status=False,
        )

    def _refresh_advertised_capacity(self) -> int:
        advertised = max(0, int(self._helper_scheduler.adjust_advertised_capacity(int(self.config.capacity))))
        if self.local_capability_ad is not None:
            self.local_capability_ad.capacity = advertised
        return advertised

    def _transition_requeued_subtask(self, task_id: str) -> None:
        state = current_state("subtask", task_id)
        if state in {"claimed", "assigned", "running"}:
            transition(
                entity_type="subtask",
                entity_id=task_id,
                to_state="timed_out",
                trace_id=task_id,
                details={"reason": "mesh_requeue"},
            )
            state = "timed_out"
        if state == "timed_out":
            transition(
                entity_type="subtask",
                entity_id=task_id,
                to_state="offered",
                trace_id=task_id,
                details={"reason": "mesh_requeue"},
            )

    def _requeue_stale_parent_assignments(self, *, limit: int = 20) -> list[str]:
        blocked_grace_seconds = max(5, int(policy_engine.get("assist_mesh.blocked_requeue_seconds", 15)))
        now_dt = datetime.now(timezone.utc)
        reopened: list[str] = []
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT assignment_id, task_id, claim_id, helper_peer_id, status, updated_at, lease_expires_at
                FROM task_assignments
                WHERE parent_peer_id = ?
                  AND status IN ('active', 'blocked')
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (local_peer_id(), max(1, int(limit))),
            ).fetchall()

            for row in rows:
                status = str(row["status"] or "")
                updated_at = str(row["updated_at"] or "")
                lease_expires_at = str(row["lease_expires_at"] or "")
                due = False
                if lease_expires_at:
                    try:
                        due = datetime.fromisoformat(lease_expires_at.replace("Z", "+00:00")) <= now_dt
                    except Exception:
                        due = False
                if not due and status == "blocked" and updated_at:
                    try:
                        updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        due = (now_dt - updated_dt).total_seconds() >= float(blocked_grace_seconds)
                    except Exception:
                        due = False
                if not due:
                    continue

                conn.execute(
                    """
                    UPDATE task_assignments
                    SET status = 'timed_out',
                        updated_at = ?,
                        completed_at = COALESCE(completed_at, ?)
                    WHERE assignment_id = ?
                    """,
                    (now_dt.isoformat(), now_dt.isoformat(), row["assignment_id"]),
                )
                conn.execute(
                    """
                    UPDATE task_claims
                    SET status = 'timed_out',
                        updated_at = ?
                    WHERE claim_id = ?
                    """,
                    (now_dt.isoformat(), row["claim_id"]),
                )
                conn.execute(
                    """
                    UPDATE task_offers
                    SET status = 'open',
                        updated_at = ?
                    WHERE task_id = ?
                      AND status != 'completed'
                    """,
                    (now_dt.isoformat(), row["task_id"]),
                )
                reopened.append(str(row["task_id"]))
                revoke_capability_tokens_for_task(
                    str(row["task_id"]),
                    helper_peer_id=str(row["helper_peer_id"]),
                    reason="mesh_requeue",
                )
            conn.commit()
        finally:
            conn.close()

        for task_id in reopened:
            try:
                self._transition_requeued_subtask(task_id)
            except Exception:
                continue
        return sorted(set(reopened))

    def _assign_pending_claims_for_open_offers(self, *, limit: int = 20) -> int:
        lease_seconds = max(60, int(policy_engine.get("assist_mesh.assignment_lease_seconds", 900)))
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT task_id, parent_peer_id, max_helpers
                FROM task_offers
                WHERE parent_peer_id = ?
                  AND status = 'open'
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (local_peer_id(), max(1, int(limit))),
            ).fetchall()
        finally:
            conn.close()

        assigned = 0
        for row in rows:
            task_id = str(row["task_id"] or "")
            if not task_id:
                continue
            conn = get_connection()
            try:
                active_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM task_assignments WHERE task_id = ? AND status = 'active'",
                    (task_id,),
                ).fetchone()
                active_count = int(active_row["cnt"]) if active_row else 0
            finally:
                conn.close()
            if active_count >= int(row["max_helpers"]):
                continue
            best = pick_best_claim_for_task(task_id, str(row["parent_peer_id"]))
            if not best:
                continue
            claim_id, helper_peer_id = best
            endpoint = endpoint_for_peer(helper_peer_id)
            if not endpoint:
                continue
            assign = prepare_task_assignment(
                task_id=task_id,
                claim_id=claim_id,
                parent_agent_id=str(row["parent_peer_id"]),
                helper_agent_id=helper_peer_id,
                assignment_mode="verification" if active_count > 0 else "single",
                lease_seconds=lease_seconds,
            )
            if not assign:
                continue
            persist_task_assignment(assign)
            if self._send_or_log(
                endpoint[0],
                int(endpoint[1]),
                build_task_assign_message(assign),
                message_type="TASK_ASSIGN",
                target_id=task_id,
            ):
                assigned += 1
        return assigned

    def _rebroadcast_parent_offers(self, task_ids: list[str]) -> int:
        from retrieval.swarm_query import broadcast_task_offer

        sent = 0
        for task_id in task_ids:
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT status FROM task_offers WHERE task_id = ? LIMIT 1",
                    (task_id,),
                ).fetchone()
            finally:
                conn.close()
            if not row or str(row["status"] or "") != "open":
                continue
            payload_bundle = load_task_offer_payload(task_id)
            if not payload_bundle:
                continue
            payload, required_capabilities = payload_bundle
            sent += broadcast_task_offer(
                offer_payload=payload,
                required_capabilities=required_capabilities,
                exclude_host_group_hint_hash=self.config.local_host_group_hint_hash,
                limit=max(4, int(policy_engine.get("assist_mesh.rebroadcast_helper_limit", 8))),
            )
        return sent

    def _resume_incomplete_parent_tasks(self, *, limit: int = 20) -> int:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT tc.parent_task_ref
                FROM task_capsules tc
                LEFT JOIN finalized_responses fr ON fr.parent_task_id = tc.parent_task_ref
                WHERE tc.parent_task_ref IS NOT NULL
                  AND tc.parent_task_ref != ''
                  AND fr.parent_task_id IS NULL
                ORDER BY tc.updated_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        finally:
            conn.close()

        resumed = 0
        for row in rows:
            parent_task_id = str(row["parent_task_ref"] or "")
            if not parent_task_id:
                continue
            result = continue_parent_orchestration(parent_task_id)
            if result.action != "no_action":
                resumed += 1
        return resumed

    def _reconcile_mesh_state(self) -> None:
        timed_out = reap_stale_subtasks(limit=max(10, int(policy_engine.get("assist_mesh.reconcile_subtask_limit", 50))))
        expired_tokens = expire_stale_capability_tokens(limit=max(20, int(policy_engine.get("assist_mesh.reconcile_token_limit", 100))))
        reopened = self._requeue_stale_parent_assignments(
            limit=max(10, int(policy_engine.get("assist_mesh.reconcile_assignment_limit", 25)))
        )
        reassigned = self._assign_pending_claims_for_open_offers(
            limit=max(10, int(policy_engine.get("assist_mesh.reconcile_open_offer_limit", 25)))
        )
        rebroadcast = self._rebroadcast_parent_offers(reopened) if reopened else 0
        resumed = self._resume_incomplete_parent_tasks(
            limit=max(10, int(policy_engine.get("assist_mesh.reconcile_parent_limit", 25)))
        )
        if any((timed_out, expired_tokens, reopened, reassigned, rebroadcast, resumed)):
            audit_logger.log(
                "mesh_reconcile_cycle",
                target_id=local_peer_id(),
                target_type="daemon",
                details={
                    "timed_out_subtasks": int(timed_out),
                    "expired_tokens": int(expired_tokens),
                    "reopened_assignments": len(reopened),
                    "reassigned_offers": int(reassigned),
                    "rebroadcasts": int(rebroadcast),
                    "resumed_parents": int(resumed),
                },
            )

    def _handle_query_shard(self, payload: dict[str, Any], addr: tuple[str, int]) -> None:
        problem_class = str(payload.get("problem_class", "unknown"))
        query_id = str(payload.get("query_id", ""))
        max_candidates = int(payload.get("max_candidates", 3))
        max_candidates = max(1, min(5, max_candidates))

        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM learning_shards
                WHERE problem_class = ?
                  AND quarantine_status = 'active'
                ORDER BY trust_score DESC, quality_score DESC, updated_at DESC
                LIMIT ?
                """,
                (problem_class, max_candidates),
            ).fetchall()
        finally:
            conn.close()

        candidates: list[dict[str, Any]] = []
        for row in rows:
            candidates.append(
                {
                    "shard_id": row["shard_id"],
                    "problem_class": row["problem_class"],
                    "summary": row["summary"][:512],
                    "trust_score": float(row["trust_score"]),
                    "quality_score": float(row["quality_score"]),
                    "freshness_ts": row["freshness_ts"],
                    "risk_flags": json.loads(row["risk_flags_json"]),
                }
            )

        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="SHARD_CANDIDATES",
            sender_peer_id=local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={
                "query_id": query_id,
                "candidates": candidates,
            },
        )
        self._send_or_log(addr[0], int(addr[1]), raw, message_type="SHARD_CANDIDATES", target_id=query_id or "unknown")

    def _handle_shard_candidates(self, payload: dict[str, Any], sender_peer_id: str) -> None:
        query_id = str(payload.get("query_id", ""))
        raw_candidates = payload.get("candidates") or []
        if not isinstance(raw_candidates, list):
            return

        safe = []
        blocked = set(policy_engine.get("shards.quarantine_if_risk_flags_include", []))
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            risk_flags = set(item.get("risk_flags") or [])
            if any(flag in blocked for flag in risk_flags):
                continue
            safe.append(item)

        safe.sort(
            key=lambda c: (
                float(c.get("trust_score", 0.0)),
                float(c.get("quality_score", 0.0)),
            ),
            reverse=True,
        )

        for item in safe[: max(1, min(self.config.auto_request_shards_per_response, 3))]:
            shard_id = item.get("shard_id")
            if not shard_id:
                continue
            request_specific_shard(
                peer_id=sender_peer_id,
                query_id=query_id,
                shard_id=str(shard_id),
            )

    def _handle_request_shard(self, payload: dict[str, Any], addr: tuple[str, int]) -> None:
        query_id = str(payload.get("query_id", ""))
        shard_id = str(payload.get("shard_id", ""))

        if not shard_id:
            return

        shard = load_shareable_shard_payload(shard_id)
        if not shard:
            return

        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="SHARD_PAYLOAD",
            sender_peer_id=local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={
                "query_id": query_id,
                "shard": shard,
            },
        )
        self._send_or_log(addr[0], int(addr[1]), raw, message_type="SHARD_PAYLOAD", target_id=shard_id)

    def _handle_shard_payload(self, payload: dict[str, Any], sender_peer_id: str) -> None:
        shard = payload.get("shard")
        if not isinstance(shard, dict):
            return

        required = {
            "shard_id",
            "schema_version",
            "problem_class",
            "problem_signature",
            "summary",
            "resolution_pattern",
            "environment_tags",
            "quality_score",
            "trust_score",
            "risk_flags",
            "freshness_ts",
            "signature",
        }
        if not required.issubset(set(shard.keys())):
            return

        risk_flags = shard.get("risk_flags") or []
        blocked = set(policy_engine.get("shards.quarantine_if_risk_flags_include", []))
        if any(flag in blocked for flag in risk_flags):
            return

        conn = get_connection()
        try:
            incoming_trust_cap = float(policy_engine.get("shards.max_incoming_trust_score", 0.60))
            conn.execute(
                """
                INSERT OR REPLACE INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, origin_task_id, origin_session_id, share_scope,
                    restricted_terms_json, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, 'peer_received', ?, ?, ?, 0, 0,
                    'active', ?, ?, ?, ?, '', '', 'public_knowledge', '[]',
                    COALESCE((SELECT created_at FROM learning_shards WHERE shard_id = ?), CURRENT_TIMESTAMP),
                    CURRENT_TIMESTAMP
                )
                """,
                (
                    shard["shard_id"],
                    int(shard["schema_version"]),
                    shard["problem_class"],
                    shard["problem_signature"],
                    shard["summary"],
                    json.dumps(shard["resolution_pattern"], sort_keys=True),
                    json.dumps(shard["environment_tags"], sort_keys=True),
                    sender_peer_id,
                    max(0.0, min(1.0, float(shard["quality_score"]))),
                    min(max(0.0, incoming_trust_cap), max(0.0, float(shard["trust_score"]))),
                    json.dumps(risk_flags, sort_keys=True),
                    shard["freshness_ts"],
                    shard.get("expires_ts"),
                    shard["signature"],
                    shard["shard_id"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

        audit_logger.log(
            "peer_shard_cached",
            target_id=shard["shard_id"],
            target_type="shard",
            details={"source_peer": sender_peer_id},
        )
        manifest = register_local_shard(str(shard["shard_id"]))
        if not manifest:
            audit_logger.log(
                "peer_shard_kept_candidate_only",
                target_id=shard["shard_id"],
                target_type="shard",
                details={"source_peer": sender_peer_id, "reason": "shareability_gate_blocked"},
            )

    def _maybe_auto_review_result_from_raw(self, raw: bytes, fallback_addr: tuple[str, int]) -> None:
        envelope = self._decode_verified_assist_envelope(raw, expected_msg_type="TASK_RESULT")
        if not envelope:
            return
        payload = envelope.get("payload") or {}
        if not isinstance(payload, dict):
            return
        sender_peer_id = str(envelope.get("sender_peer_id") or "")
        helper_peer_id = str(payload.get("helper_agent_id", ""))
        if sender_peer_id and helper_peer_id and sender_peer_id != helper_peer_id:
            audit_logger.log(
                "task_result_rejected_sender_mismatch",
                target_id=str(payload.get("task_id", "")),
                target_type="task",
                details={"sender_peer_id": sender_peer_id, "helper_peer_id": helper_peer_id},
            )
            return

        artifacts = auto_review_task_result(
            payload,
            reviewer_peer_id=local_peer_id(),
            emit_reward_notice=True,
        )
        if not artifacts:
            return

        endpoint = endpoint_for_peer(helper_peer_id)
        host, port = endpoint if endpoint else fallback_addr

        for msg in artifacts.outbound_messages:
            self._send_or_log(host, int(port), msg, message_type="TASK_REVIEW", target_id=str(payload.get("task_id", "")))

        audit_logger.log(
            "task_result_review_reply_sent",
            target_id=str(payload.get("task_id", "")),
            target_type="task",
            details={
                "helper_peer_id": helper_peer_id,
                "review_outcome": artifacts.outcome,
                "points_awarded": artifacts.points_awarded,
                "wnull_pending": artifacts.wnull_pending,
            },
        )

    def _maybe_execute_local_assignment_from_raw(self, raw: bytes, fallback_addr: tuple[str, int]) -> None:
        envelope = self._decode_verified_assist_envelope(raw, expected_msg_type="TASK_ASSIGN")
        if not envelope:
            return
        payload = envelope.get("payload") or {}
        if not isinstance(payload, dict):
            return
        sender_peer_id = str(envelope.get("sender_peer_id") or "")
        parent_peer_id = str(payload.get("parent_agent_id", ""))
        if sender_peer_id and parent_peer_id and sender_peer_id != parent_peer_id:
            audit_logger.log(
                "task_assign_rejected_sender_mismatch",
                target_id=str(payload.get("task_id", "")),
                target_type="task",
                details={"sender_peer_id": sender_peer_id, "parent_peer_id": parent_peer_id},
            )
            return

        if payload.get("helper_agent_id") != local_peer_id():
            return

        task_id = str(payload.get("task_id", ""))
        assignment_id = str(payload.get("assignment_id", ""))
        if not task_id or not parent_peer_id:
            return

        capsule = load_task_capsule_for_task(task_id)
        if not capsule:
            audit_logger.log(
                "assignment_execution_skipped",
                target_id=task_id,
                target_type="task",
                details={"reason": "capsule_not_found"},
            )
            return

        if not assignment_id:
            audit_logger.log(
                "assignment_execution_skipped",
                target_id=task_id,
                target_type="task",
                details={"reason": "missing_assignment_id"},
            )
            return

        parent_trust = float(peer_trust(parent_peer_id))
        min_parent_trust = max(0.0, min(1.0, float(policy_engine.get("assist_mesh.min_parent_trust_for_assignment_execution", 0.25))))
        if parent_trust < min_parent_trust:
            audit_logger.log(
                "assignment_execution_skipped",
                target_id=task_id,
                target_type="task",
                details={"reason": "parent_trust_too_low", "parent_trust": parent_trust, "threshold": min_parent_trust},
            )
            return

        capability_token = payload.get("capability_token")
        if bool(policy_engine.get("assist_mesh.require_assignment_capability_token", True)):
            capability_decision = verify_assignment_capability(
                capability_token if isinstance(capability_token, dict) else None,
                task_id=task_id,
                parent_peer_id=parent_peer_id,
                helper_peer_id=local_peer_id(),
                capsule=capsule,
            )
            if not capability_decision.ok:
                audit_logger.log(
                    "assignment_execution_skipped",
                    target_id=task_id,
                    target_type="task",
                    details={"reason": capability_decision.reason},
                )
                return

            capability_token_id = str((capability_token or {}).get("token_id") or "").strip()
            if capability_token_id:
                mark_capability_token_used(capability_token_id)

        if not apply_local_execution_safety(
            {"task_id": task_id, "assignment_id": assignment_id},
            {"assignment": payload, "capability_token": capability_token or {}},
        ):
            audit_logger.log(
                "assignment_execution_skipped",
                target_id=task_id,
                target_type="task",
                details={"reason": "liquefy_execution_safety_rejected"},
            )
            return

        try:
            parent_endpoint = endpoint_for_peer(parent_peer_id)
            host, port = parent_endpoint if parent_endpoint else fallback_addr
            started_progress = build_task_progress_message(
                assignment_id=assignment_id,
                task_id=task_id,
                helper_agent_id=local_peer_id(),
                progress_state="started",
                progress_note="lease verified; helper execution started",
            )
            self._send_or_log(host, int(port), started_progress, message_type="TASK_PROGRESS", target_id=task_id)

            worker_outcome = run_task_capsule(capsule, helper_agent_id=local_peer_id())
        except Exception as e:
            parent_endpoint = endpoint_for_peer(parent_peer_id)
            host, port = parent_endpoint if parent_endpoint else fallback_addr
            blocked_progress = build_task_progress_message(
                assignment_id=assignment_id,
                task_id=task_id,
                helper_agent_id=local_peer_id(),
                progress_state="blocked",
                progress_note=f"helper execution failed: {str(e)[:180]}",
            )
            self._send_or_log(host, int(port), blocked_progress, message_type="TASK_PROGRESS", target_id=task_id)
            audit_logger.log(
                "assignment_execution_failed",
                target_id=task_id,
                target_type="task",
                details={"error": str(e)},
            )
            return

        parent_endpoint = endpoint_for_peer(parent_peer_id)
        host, port = parent_endpoint if parent_endpoint else fallback_addr

        done_progress = build_task_progress_message(
            assignment_id=assignment_id,
            task_id=task_id,
            helper_agent_id=local_peer_id(),
            progress_state="done",
            progress_note="helper execution finished; sending result",
        )
        self._send_or_log(host, int(port), done_progress, message_type="TASK_PROGRESS", target_id=task_id)

        raw_result = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="TASK_RESULT",
            sender_peer_id=local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload=worker_outcome.result.model_dump(mode="json"),
        )
        self._send_or_log(host, int(port), raw_result, message_type="TASK_RESULT", target_id=task_id)

        audit_logger.log(
            "assignment_executed_locally",
            target_id=task_id,
            target_type="task",
            details={
                "sent_to_parent": True,
                "parent_peer_id": parent_peer_id,
            },
        )

    def _decode_verified_assist_envelope(self, raw: bytes, *, expected_msg_type: str) -> dict[str, Any] | None:
        try:
            envelope = Protocol.decode_and_validate(raw)
        except Exception as exc:
            error_text = str(exc)
            # Assist routing already validates and stores nonce once on the hot path.
            # Local follow-up handlers may decode the same envelope again; in that case
            # replay rejection is expected, so we re-validate signature + payload without
            # mutating nonce state a second time.
            if "Replay detected" not in error_text:
                audit_logger.log(
                    "assist_envelope_rejected",
                    target_id=expected_msg_type,
                    target_type="network",
                    details={"error": error_text},
                )
                return None
            try:
                envelope_model = Envelope.model_validate(json.loads(raw.decode("utf-8")))
                if not verify_signature(envelope_model):
                    raise ValueError("Invalid signature.")
                validate_payload(envelope_model)
                envelope = envelope_model.model_dump()
                audit_logger.log(
                    "assist_envelope_replay_revalidated",
                    target_id=expected_msg_type,
                    target_type="network",
                    details={},
                )
            except Exception as replay_exc:
                audit_logger.log(
                    "assist_envelope_rejected",
                    target_id=expected_msg_type,
                    target_type="network",
                    details={"error": f"{error_text}; replay_revalidation={replay_exc}"},
                )
                return None
        msg_type = str(envelope.get("msg_type") or "")
        if msg_type != expected_msg_type:
            audit_logger.log(
                "assist_envelope_type_mismatch",
                target_id=expected_msg_type,
                target_type="network",
                details={"actual_msg_type": msg_type},
            )
            return None
        payload = envelope.get("payload") or {}
        if not isinstance(payload, dict):
            return None
        return envelope

    def _spawn_limited_worker(
        self,
        *,
        target: Any,
        args: tuple[Any, ...],
        name: str,
        target_id: str,
    ) -> bool:
        if not self._local_worker_sem.acquire(blocking=False):
            audit_logger.log(
                "local_worker_capacity_exhausted",
                target_id=target_id,
                target_type="daemon",
                details={"worker_limit": int(self._local_worker_limit), "worker_name": name},
            )
            return False

        def _runner() -> None:
            try:
                target(*args)
            finally:
                self._local_worker_sem.release()

        threading.Thread(target=_runner, name=name, daemon=True).start()
        return True

    def _send_or_log(self, host: str, port: int, payload: bytes, *, message_type: str, target_id: str) -> bool:
        critical_types = {"TASK_ASSIGN", "TASK_RESULT", "TASK_REVIEW", "TASK_REWARD", "TASK_CLAIM"}
        retries = int(policy_engine.get("network.critical_send_retries", 2)) if message_type in critical_types else 0
        retries = max(0, retries)
        attempts = 1 + retries

        for attempt in range(1, attempts + 1):
            ok = send_message(host, int(port), payload)
            if ok:
                return True
            if attempt < attempts:
                time.sleep(min(0.25, 0.05 * attempt))

        audit_logger.log(
            "outbound_send_failed",
            target_id=target_id,
            target_type="network",
            details={
                "host": host,
                "port": int(port),
                "message_type": message_type,
                "attempts": attempts,
            },
        )
        return False


def main() -> int:
    parser = argparse.ArgumentParser(prog="nulla-daemon")
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--bind-port", type=int, default=49152)
    parser.add_argument("--advertise-host", default="127.0.0.1")
    parser.add_argument("--capacity", default="auto")
    parser.add_argument("--health-host", default="127.0.0.1")
    parser.add_argument("--health-port", type=int, default=0)
    parser.add_argument("--health-token", default="")
    args = parser.parse_args()

    pool_hard_cap = max(1, int(policy_engine.get("orchestration.local_worker_pool_max", 10)))
    requested_capacity: int | None = None
    if str(args.capacity).strip().lower() != "auto":
        requested_capacity = max(1, int(args.capacity))
    daemon_capacity, recommended_capacity = resolve_local_worker_capacity(
        requested=requested_capacity,
        hard_cap=pool_hard_cap,
    )
    if daemon_capacity > recommended_capacity:
        print(
            "WARNING: daemon capacity override is above recommended "
            f"({daemon_capacity} > {recommended_capacity})."
        )

    daemon = NullaDaemon(
        DaemonConfig(
            bind_host=str(args.bind_host),
            bind_port=int(args.bind_port),
            advertise_host=str(args.advertise_host),
            capacity=max(1, int(daemon_capacity)),
            local_worker_threads=max(2, int(daemon_capacity) * 2),
            health_bind_host=str(args.health_host),
            health_bind_port=max(0, int(args.health_port)),
            health_auth_token=str(args.health_token or "").strip() or None,
        )
    )
    daemon.start()
    stop_event = threading.Event()

    def _request_stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    try:
        while not stop_event.wait(1.0):
            continue
    finally:
        daemon.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
