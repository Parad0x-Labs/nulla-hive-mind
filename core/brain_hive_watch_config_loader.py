from __future__ import annotations

import json
from pathlib import Path

from apps.brain_hive_watch_server import BrainHiveWatchServerConfig


def load_brain_hive_watch_config(path: str | Path) -> BrainHiveWatchServerConfig:
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    host = raw.get("host", raw.get("bind_host", BrainHiveWatchServerConfig.host))
    port = int(raw.get("port", raw.get("bind_port", BrainHiveWatchServerConfig.port)))
    upstreams = tuple(raw.get("upstream_base_urls", ()))
    timeout_seconds = int(
        raw.get(
            "request_timeout_seconds",
            BrainHiveWatchServerConfig.request_timeout_seconds,
        )
    )
    auth_token = str(raw.get("auth_token") or "").strip() or None
    auth_tokens_by_base_url = {
        str(base).strip(): str(token).strip()
        for base, token in dict(raw.get("auth_tokens_by_base_url") or {}).items()
        if str(base).strip() and str(token).strip()
    }
    tls_certfile = str(raw.get("tls_certfile") or "").strip() or None
    tls_keyfile = str(raw.get("tls_keyfile") or "").strip() or None
    tls_ca_file = str(raw.get("tls_ca_file") or "").strip() or None
    tls_insecure_skip_verify = bool(raw.get("tls_insecure_skip_verify", False))
    return BrainHiveWatchServerConfig(
        host=str(host),
        port=port,
        upstream_base_urls=upstreams,
        request_timeout_seconds=timeout_seconds,
        auth_token=auth_token,
        auth_tokens_by_base_url=auth_tokens_by_base_url,
        tls_certfile=tls_certfile,
        tls_keyfile=tls_keyfile,
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
    )
