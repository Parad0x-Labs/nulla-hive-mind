from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from core import audit_logger
from core.adaptation_autopilot import schedule_adaptation_autopilot_tick
from core.bootstrap_adapters import BootstrapMirrorAdapter
from core.bootstrap_sync import prune_expired_topic_files, publish_local_presence_snapshots, sync_from_bootstrap_topics
from core.control_plane_workspace import sync_control_plane_workspace
from core.discovery_index import prune_stale_capabilities
from core.hardware_challenge import initiate_random_hardware_challenge
from core.knowledge_freshness import iso_now
from core.reward_engine import finalize_confirmed_rewards, release_mature_pending_rewards
from core.timeout_policy import reap_stale_subtasks
from retrieval.swarm_query import broadcast_capability_ad
from storage.knowledge_index import prune_expired_presence
from storage.replica_table import prune_expired_holders


@dataclass
class MaintenanceConfig:
    tick_seconds: int = 30
    bootstrap_topics: list[str] | None = None
    bootstrap_adapter: BootstrapMirrorAdapter | None = None
    publish_ttl_minutes: int = 15
    stale_capability_hours: int = 24
    topic_file_max_age_minutes: int = 60
    stale_subtask_scan_limit: int = 200


class MaintenanceLoop:
    def __init__(
        self,
        *,
        config: MaintenanceConfig,
        capability_ad_builder: Callable[[], bytes] | None = None,
        presence_broadcast: Callable[[], int] | None = None,
        knowledge_broadcast: Callable[[], int] | None = None,
        local_bind_host: str = "127.0.0.1",
        local_bind_port: int = 49152,
    ) -> None:
        self.config = config
        self.capability_ad_builder = capability_ad_builder
        self.presence_broadcast = presence_broadcast
        self.knowledge_broadcast = knowledge_broadcast
        self.local_bind_host = local_bind_host
        self.local_bind_port = int(local_bind_port)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="nulla-maintenance-loop",
            daemon=False,  # keep process alive
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def run_tick(self) -> None:
        topics = self.config.bootstrap_topics

        # 1) publish signed bootstrap snapshot
        try:
            publish_local_presence_snapshots(
                topic_names=topics,
                ttl_minutes=self.config.publish_ttl_minutes,
                adapter=self.config.bootstrap_adapter,
            )
        except Exception as e:
            audit_logger.log(
                "maintenance_publish_error",
                target_id="bootstrap",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 2) sync mirrors back into local discovery index
        try:
            sync_from_bootstrap_topics(
                topic_names=topics,
                adapter=self.config.bootstrap_adapter,
            )
        except Exception as e:
            audit_logger.log(
                "maintenance_sync_error",
                target_id="bootstrap",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 2.5) Phase 23: Initiate DHT Discovery
        try:
            from network.assist_router import build_find_node_message
            from network.dht import get_routing_table
            from network.signer import get_local_peer_id
            from network.transport import send_message
            from storage.db import get_connection

            table = get_routing_table()
            table.prune_stale_nodes(max_age_seconds=float(self.config.stale_capability_hours) * 3600.0)
            if len(table.nodes) < 20:
                conn = get_connection()
                rows = conn.execute(
                    "SELECT peer_id, host, port FROM peer_endpoints WHERE peer_id != ? LIMIT 10",
                    (get_local_peer_id(),),
                ).fetchall()
                conn.close()

                if rows:
                    payload = build_find_node_message(get_local_peer_id())
                    sent = 0
                    for row in rows:
                        if send_message(row["host"], int(row["port"]), payload):
                            sent += 1
                    if sent > 0:
                        audit_logger.log("dht_discovery_initiated", target_id="dht", target_type="maintenance", details={"sent": sent})
        except Exception as e:
            audit_logger.log("dht_discovery_error", target_id="dht", target_type="maintenance", details={"error": str(e)})

        # 3) refresh peer ads outward
        if self.capability_ad_builder:
            try:
                raw = self.capability_ad_builder()
                broadcast_capability_ad(raw, limit=25)
            except Exception as e:
                audit_logger.log(
                    "maintenance_capability_broadcast_error",
                    target_id="assist",
                    target_type="maintenance",
                    details={"error": str(e)},
                )

        # 3.5) presence heartbeat
        if self.presence_broadcast:
            try:
                self.presence_broadcast()
            except Exception as e:
                audit_logger.log(
                    "maintenance_presence_broadcast_error",
                    target_id="presence",
                    target_type="maintenance",
                    details={"error": str(e)},
                )

        # 3.6) knowledge advertisements
        if self.knowledge_broadcast:
            try:
                self.knowledge_broadcast()
            except Exception as e:
                audit_logger.log(
                    "maintenance_knowledge_broadcast_error",
                    target_id="knowledge",
                    target_type="maintenance",
                    details={"error": str(e)},
                )

        # 4) prune stale capabilities
        try:
            pruned_caps = prune_stale_capabilities(max_age_hours=self.config.stale_capability_hours)
        except Exception as e:
            pruned_caps = 0
            audit_logger.log(
                "maintenance_prune_caps_error",
                target_id="discovery",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 4.5) prune expired presence leases and stale holder claims
        try:
            pruned_presence = prune_expired_presence(iso_now())
            pruned_holders = prune_expired_holders(iso_now())
        except Exception as e:
            pruned_presence = 0
            pruned_holders = 0
            audit_logger.log(
                "maintenance_knowledge_prune_error",
                target_id="knowledge",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 5) release mature pending rewards
        try:
            timed_out_subtasks = reap_stale_subtasks(limit=int(self.config.stale_subtask_scan_limit))
        except Exception as e:
            timed_out_subtasks = 0
            audit_logger.log(
                "maintenance_timeout_reaper_error",
                target_id="task_state",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 6) release mature pending rewards
        try:
            released = release_mature_pending_rewards(limit=100)
        except Exception as e:
            released = 0
            audit_logger.log(
                "maintenance_reward_release_error",
                target_id="rewards",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 6.5) advance confirmed rewards to finality or slash challenged work
        try:
            finalized = finalize_confirmed_rewards(limit=100)
        except Exception as e:
            finalized = 0
            audit_logger.log(
                "maintenance_reward_finality_error",
                target_id="rewards",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 7) prune expired bootstrap files
        try:
            removed = prune_expired_topic_files(max_age_minutes=self.config.topic_file_max_age_minutes)
        except Exception as e:
            removed = 0
            audit_logger.log(
                "maintenance_prune_topics_error",
                target_id="bootstrap",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 8) Hardware micro-benchmark spot checks (anti-cheat)
        try:
            initiate_random_hardware_challenge()
        except Exception as e:
            audit_logger.log(
                "maintenance_hardware_challenge_error",
                target_id="anti_cheat",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 9) Background adaptation autopilot tick
        try:
            schedule_adaptation_autopilot_tick()
        except Exception as e:
            audit_logger.log(
                "maintenance_adaptation_autopilot_error",
                target_id="adaptation",
                target_type="maintenance",
                details={"error": str(e)},
            )

        # 10) Operator-facing control-plane mirror
        try:
            control_sync = sync_control_plane_workspace()
        except Exception as e:
            control_sync = {"ok": False, "writes": 0}
            audit_logger.log(
                "maintenance_control_plane_sync_error",
                target_id="control_plane",
                target_type="maintenance",
                details={"error": str(e)},
            )

        audit_logger.log(
            "maintenance_tick_complete",
            target_id="maintenance",
            target_type="maintenance",
            details={
                "pruned_caps": pruned_caps,
                "pruned_presence": pruned_presence,
                "pruned_holders": pruned_holders,
                "timed_out_subtasks": timed_out_subtasks,
                "released_rewards": released,
                "finalized_rewards": finalized,
                "removed_topics": removed,
                "control_sync_ok": bool(control_sync.get("ok")),
                "control_sync_writes": int(control_sync.get("writes") or 0),
            },
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_tick()

            # responsive sleep
            deadline = time.time() + self.config.tick_seconds
            while time.time() < deadline and not self._stop.is_set():
                time.sleep(0.25)
