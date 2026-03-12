from __future__ import annotations

import platform
import threading
import time
from dataclasses import dataclass, field

from apps.meet_and_greet_server import MeetAndGreetServerConfig, build_server
from core import audit_logger
from core.logging_config import setup_logging
from core import policy_engine
from core.meet_and_greet_models import MeetNodeRegisterRequest
from core.meet_and_greet_replication import MeetAndGreetReplicator, ReplicationConfig
from core.meet_and_greet_service import MeetAndGreetConfig, MeetAndGreetService
from core.runtime_guard import enforce_meet_public_deployment
from storage.migrations import run_migrations


@dataclass
class MeetPeerSeed:
    node_id: str
    base_url: str
    region: str = "global"
    role: str = "seed"
    platform_hint: str = "unknown"
    priority: int = 100


@dataclass
class MeetAndGreetNodeConfig:
    node_id: str
    public_base_url: str
    region: str = "global"
    role: str = "seed"
    priority: int = 100
    bind_host: str = "127.0.0.1"
    bind_port: int = 8766
    auth_token: str | None = None
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    tls_ca_file: str | None = None
    tls_require_client_cert: bool = False
    allow_insecure_public_http: bool = False
    sync_interval_seconds: int = 15
    service_config: MeetAndGreetConfig = field(default_factory=MeetAndGreetConfig)
    replication_config: ReplicationConfig = field(default_factory=ReplicationConfig)
    seed_peers: list[MeetPeerSeed] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.service_config.local_region == "global" and self.region != "global":
            self.service_config.local_region = self.region
        if self.replication_config.local_region == "global" and self.region != "global":
            self.replication_config.local_region = self.region
        if not str(self.replication_config.auth_token or "").strip():
            self.replication_config.auth_token = str(self.auth_token or "").strip() or None


class MeetAndGreetNode:
    def __init__(self, config: MeetAndGreetNodeConfig) -> None:
        self.config = config
        self.service = MeetAndGreetService(config.service_config)
        self.replicator = MeetAndGreetReplicator(self.service, config=config.replication_config)
        self.server = None
        self._server_thread: threading.Thread | None = None
        self._sync_thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        setup_logging(
            level=str(policy_engine.get("observability.log_level", "INFO")),
            json_output=bool(policy_engine.get("observability.json_logs", True)),
        )
        run_migrations()
        enforce_meet_public_deployment(
            bind_host=self.config.bind_host,
            public_base_url=self.config.public_base_url,
            auth_token=self.config.auth_token,
            tls_certfile=self.config.tls_certfile,
            tls_keyfile=self.config.tls_keyfile,
            allow_insecure_public_http=self.config.allow_insecure_public_http,
        )
        self.server = build_server(
            MeetAndGreetServerConfig(
                host=self.config.bind_host,
                port=self.config.bind_port,
                auth_token=self.config.auth_token,
                tls_certfile=self.config.tls_certfile,
                tls_keyfile=self.config.tls_keyfile,
                tls_ca_file=self.config.tls_ca_file,
                tls_require_client_cert=self.config.tls_require_client_cert,
            ),
            service=self.service,
        )
        self._register_self()
        self._register_seeds()
        self._stop.clear()
        self._server_thread = threading.Thread(target=self.server.serve_forever, name="meet-and-greet-server", daemon=True)
        self._server_thread.start()
        self._sync_thread = threading.Thread(target=self._sync_loop, name="meet-and-greet-sync", daemon=True)
        self._sync_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=2.0)
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=2.0)

    def _register_self(self) -> None:
        self.service.register_meet_node(
            MeetNodeRegisterRequest(
                node_id=self.config.node_id,
                base_url=self.config.public_base_url,
                region=self.config.region,
                role=self.config.role,
                platform_hint=platform.system().lower(),
                priority=self.config.priority,
                status="active",
                metadata={"bind_host": self.config.bind_host, "bind_port": self.config.bind_port},
            )
        )

    def _register_seeds(self) -> None:
        for seed in self.config.seed_peers:
            if seed.node_id == self.config.node_id:
                continue
            self.service.register_meet_node(
                MeetNodeRegisterRequest(
                    node_id=seed.node_id,
                    base_url=seed.base_url,
                    region=seed.region,
                    role=seed.role,
                    platform_hint=seed.platform_hint,
                    priority=seed.priority,
                    status="active",
                    metadata={},
                )
            )

    def _sync_loop(self) -> None:
        while not self._stop.wait(self.config.sync_interval_seconds):
            try:
                self.replicator.sync_registered_nodes(exclude_node_id=self.config.node_id)
            except Exception as exc:
                audit_logger.log(
                    "meet_node_sync_error",
                    target_id=self.config.node_id,
                    target_type="meet_node",
                    details={"error": str(exc)},
                )
                time.sleep(1.0)
