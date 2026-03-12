from __future__ import annotations

import json
from pathlib import Path

from apps.meet_and_greet_node import MeetAndGreetNodeConfig, MeetPeerSeed
from core.meet_and_greet_replication import ReplicationConfig
from core.meet_and_greet_service import MeetAndGreetConfig


def load_meet_node_config(path: str | Path) -> MeetAndGreetNodeConfig:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    service_config = MeetAndGreetConfig(**dict(raw.pop("service_config", {})))
    replication_config = ReplicationConfig(**dict(raw.pop("replication_config", {})))
    seed_peers = [MeetPeerSeed(**dict(item)) for item in list(raw.pop("seed_peers", []))]
    return MeetAndGreetNodeConfig(
        service_config=service_config,
        replication_config=replication_config,
        seed_peers=seed_peers,
        **raw,
    )
