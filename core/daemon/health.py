from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from network.signer import get_local_peer_id as local_peer_id

from .models import is_loopback_host


def start_health_server(daemon: Any) -> None:
    if int(daemon.config.health_bind_port) <= 0:
        return
    daemon_ref = daemon

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.rstrip("/") not in {"/healthz", "/v1/healthz"}:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":false,"error":"not_found"}')
                return
            require_auth = (not is_loopback_host(daemon_ref.config.health_bind_host)) or bool(
                str(daemon_ref.config.health_auth_token or "").strip()
            )
            if require_auth:
                header_token = str(self.headers.get("X-Nulla-Health-Token") or "").strip()
                expected = str(daemon_ref.config.health_auth_token or "").strip()
                if not expected or header_token != expected:
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":false,"error":"unauthorized"}')
                    return
            body = json.dumps({"ok": True, "result": daemon_ref._health_snapshot()}, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            del format, args
            return

    daemon._health_server = ThreadingHTTPServer(
        (daemon.config.health_bind_host, int(daemon.config.health_bind_port)),
        HealthHandler,
    )
    daemon._health_thread = threading.Thread(
        target=daemon._health_server.serve_forever,
        name="nulla-daemon-health",
        daemon=True,
    )
    daemon._health_thread.start()


def health_snapshot(daemon: Any) -> dict[str, Any]:
    runtime = daemon._runtime
    return {
        "peer_id": local_peer_id(),
        "running": True,
        "bind_host": daemon.config.bind_host,
        "bind_port": int(daemon.config.bind_port),
        "public_host": runtime.public_host if runtime else None,
        "public_port": int(runtime.public_port) if runtime else None,
        "active_assignments": int(daemon._active_assignment_count()),
        "capacity": int(daemon.config.capacity),
        "advertised_capacity": int(daemon._refresh_advertised_capacity()),
        "maintenance_running": bool(daemon.maintenance is not None),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
