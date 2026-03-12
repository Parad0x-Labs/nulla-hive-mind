from __future__ import annotations

import json
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib import request
from urllib.parse import unquote, urlparse, urlsplit, urlunsplit

from core.brain_hive_dashboard import render_dashboard_html, render_not_found_html, render_topic_detail_html


@dataclass
class BrainHiveWatchServerConfig:
    host: str = "127.0.0.1"
    port: int = 8788
    upstream_base_urls: tuple[str, ...] = ("http://127.0.0.1:8766",)
    request_timeout_seconds: int = 5
    auth_token: str | None = None
    auth_tokens_by_base_url: dict[str, str] = field(default_factory=dict)
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    tls_ca_file: str | None = None
    tls_insecure_skip_verify: bool = False


def _parse_dashboard_timestamp(value: object) -> float:
    if value in (None, "", 0):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _dashboard_trading_presence_ts(snapshot: dict) -> float:
    trading = dict(snapshot.get("trading_learning") or {})
    heartbeat = dict(trading.get("latest_heartbeat") or {})
    summary = dict(trading.get("latest_summary") or {})
    latest_ts = max(
        _parse_dashboard_timestamp(heartbeat.get("last_tick_ts")),
        _parse_dashboard_timestamp(heartbeat.get("post_created_at")),
        _parse_dashboard_timestamp(summary.get("post_created_at")),
    )
    for topic in list(trading.get("topics") or []):
        if not isinstance(topic, dict):
            continue
        latest_ts = max(
            latest_ts,
            _parse_dashboard_timestamp(topic.get("updated_at")),
            _parse_dashboard_timestamp(topic.get("created_at")),
        )
    return latest_ts


def _dashboard_freshness_key(snapshot: dict) -> tuple[float, float, int]:
    presence_ts = _dashboard_trading_presence_ts(snapshot)
    generated_ts = _parse_dashboard_timestamp(snapshot.get("generated_at"))
    active_agents = int(dict(snapshot.get("stats") or {}).get("active_agents", 0) or 0)
    return (presence_ts, generated_ts, active_agents)


def _http_get_json(
    url: str,
    *,
    timeout_seconds: int,
    auth_token: str | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
) -> dict:
    req = request.Request(url, method="GET")
    req.add_header("Content-Type", "application/json")
    token = str(auth_token or "").strip()
    if token:
        req.add_header("X-Nulla-Meet-Token", token)
    with request.urlopen(
        req,
        timeout=timeout_seconds,
        context=_ssl_context_for_url(
            url,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        ),
    ) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_dashboard_from_upstreams(
    upstream_base_urls: tuple[str, ...],
    *,
    timeout_seconds: int = 5,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
    fetch_json: Callable[[str, str | None], dict] | None = None,
) -> dict:
    tokens = {
        _normalize_base_url(base): str(token).strip()
        for base, token in dict(auth_tokens_by_base_url or {}).items()
        if str(base).strip() and str(token).strip()
    }
    fetch = fetch_json or (
        lambda url, token: _http_get_json(
            url,
            timeout_seconds=timeout_seconds,
            auth_token=token,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
    )
    errors: list[str] = []
    best_result: dict | None = None
    best_key: tuple[float, float, int] | None = None
    for base in upstream_base_urls:
        clean = str(base).rstrip("/")
        target = f"{clean}/v1/hive/dashboard"
        token = tokens.get(_normalize_base_url(clean)) or auth_token
        try:
            payload = fetch(target, token)
        except Exception as exc:  # pragma: no cover - network errors
            errors.append(f"{clean}: {exc}")
            continue
        if payload.get("ok"):
            result = payload.get("result") or {}
            result["source_meet_url"] = clean
            freshness = _dashboard_freshness_key(result)
            if best_result is None or freshness > (best_key or (0.0, 0.0, 0)):
                best_result = result
                best_key = freshness
            continue
        errors.append(f"{clean}: {payload.get('error') or 'upstream returned not ok'}")
    if best_result is not None:
        return best_result
    raise ValueError("All upstream meet nodes failed for dashboard fetch: " + "; ".join(errors))


def fetch_topic_from_upstreams(
    upstream_base_urls: tuple[str, ...],
    *,
    topic_id: str,
    timeout_seconds: int = 5,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
    fetch_json: Callable[[str, str | None], dict] | None = None,
) -> dict:
    tokens = {
        _normalize_base_url(base): str(token).strip()
        for base, token in dict(auth_tokens_by_base_url or {}).items()
        if str(base).strip() and str(token).strip()
    }
    fetch = fetch_json or (
        lambda url, token: _http_get_json(
            url,
            timeout_seconds=timeout_seconds,
            auth_token=token,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
    )
    normalized_topic_id = str(topic_id or "").strip()
    errors: list[str] = []
    for base in upstream_base_urls:
        clean = str(base).rstrip("/")
        target = f"{clean}/v1/hive/topics/{normalized_topic_id}"
        token = tokens.get(_normalize_base_url(clean)) or auth_token
        try:
            payload = fetch(target, token)
        except Exception as exc:  # pragma: no cover - network errors
            errors.append(f"{clean}: {exc}")
            continue
        if payload.get("ok"):
            result = dict(payload.get("result") or {})
            result["source_meet_url"] = clean
            return result
        errors.append(f"{clean}: {payload.get('error') or 'upstream returned not ok'}")
    raise ValueError("All upstream meet nodes failed for topic fetch: " + "; ".join(errors))


def fetch_topic_posts_from_upstreams(
    upstream_base_urls: tuple[str, ...],
    *,
    topic_id: str,
    limit: int = 120,
    timeout_seconds: int = 5,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
    fetch_json: Callable[[str, str | None], dict] | None = None,
) -> list[dict]:
    tokens = {
        _normalize_base_url(base): str(token).strip()
        for base, token in dict(auth_tokens_by_base_url or {}).items()
        if str(base).strip() and str(token).strip()
    }
    fetch = fetch_json or (
        lambda url, token: _http_get_json(
            url,
            timeout_seconds=timeout_seconds,
            auth_token=token,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
    )
    normalized_topic_id = str(topic_id or "").strip()
    safe_limit = max(1, min(int(limit), 200))
    errors: list[str] = []
    for base in upstream_base_urls:
        clean = str(base).rstrip("/")
        target = f"{clean}/v1/hive/topics/{normalized_topic_id}/posts?limit={safe_limit}"
        token = tokens.get(_normalize_base_url(clean)) or auth_token
        try:
            payload = fetch(target, token)
        except Exception as exc:  # pragma: no cover - network errors
            errors.append(f"{clean}: {exc}")
            continue
        if payload.get("ok"):
            return list(payload.get("result") or [])
        errors.append(f"{clean}: {payload.get('error') or 'upstream returned not ok'}")
    raise ValueError("All upstream meet nodes failed for topic post fetch: " + "; ".join(errors))


def build_server(config: BrainHiveWatchServerConfig | None = None) -> ThreadingHTTPServer:
    cfg = config or BrainHiveWatchServerConfig()
    _validate_tls_config(cfg)

    class Handler(BaseHTTPRequestHandler):
        server_version = "NullaBrainHiveWatch/0.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            clean_path = parsed.path.rstrip("/") or "/"
            if clean_path in {"/", "/brain-hive"}:
                html = render_dashboard_html(api_endpoint="/api/dashboard", topic_base_path="/brain-hive/topic")
                self._write_bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))
                return
            if clean_path.startswith("/brain-hive/topic/"):
                topic_id = unquote(clean_path.removeprefix("/brain-hive/topic/").strip("/"))
                if topic_id:
                    html = render_topic_detail_html(
                        topic_api_endpoint=f"/api/topic/{topic_id}",
                        posts_api_endpoint=f"/api/topic/{topic_id}/posts",
                    )
                    self._write_bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))
                    return
            if clean_path == "/api/dashboard":
                try:
                    snapshot = fetch_dashboard_from_upstreams(
                        cfg.upstream_base_urls,
                        timeout_seconds=cfg.request_timeout_seconds,
                        auth_token=cfg.auth_token,
                        auth_tokens_by_base_url=cfg.auth_tokens_by_base_url,
                        tls_ca_file=cfg.tls_ca_file,
                        tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                    )
                    self._write_json(200, {"ok": True, "result": snapshot, "error": None})
                except Exception as exc:
                    self._write_json(502, {"ok": False, "result": None, "error": str(exc)})
                return
            if clean_path.startswith("/api/topic/") and clean_path.endswith("/posts"):
                topic_id = unquote(clean_path.removeprefix("/api/topic/").removesuffix("/posts").strip("/"))
                if topic_id:
                    try:
                        posts = fetch_topic_posts_from_upstreams(
                            cfg.upstream_base_urls,
                            topic_id=topic_id,
                            timeout_seconds=cfg.request_timeout_seconds,
                            auth_token=cfg.auth_token,
                            auth_tokens_by_base_url=cfg.auth_tokens_by_base_url,
                            tls_ca_file=cfg.tls_ca_file,
                            tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                        )
                        self._write_json(200, {"ok": True, "result": posts, "error": None})
                    except Exception as exc:
                        self._write_json(502, {"ok": False, "result": None, "error": str(exc)})
                    return
            if clean_path.startswith("/api/topic/"):
                topic_id = unquote(clean_path.removeprefix("/api/topic/").strip("/"))
                if topic_id and "/" not in topic_id:
                    try:
                        topic = fetch_topic_from_upstreams(
                            cfg.upstream_base_urls,
                            topic_id=topic_id,
                            timeout_seconds=cfg.request_timeout_seconds,
                            auth_token=cfg.auth_token,
                            auth_tokens_by_base_url=cfg.auth_tokens_by_base_url,
                            tls_ca_file=cfg.tls_ca_file,
                            tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                        )
                        self._write_json(200, {"ok": True, "result": topic, "error": None})
                    except Exception as exc:
                        self._write_json(502, {"ok": False, "result": None, "error": str(exc)})
                    return
            if clean_path == "/health":
                self._write_json(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "service": "brain_hive_watch",
                            "upstream_count": len(cfg.upstream_base_urls),
                        },
                        "error": None,
                    },
                )
                return
            self._write_bytes(404, "text/html; charset=utf-8", render_not_found_html(parsed.path).encode("utf-8"))

        def log_message(self, format: str, *args):  # noqa: A003
            return

        def _write_json(self, status_code: int, payload: dict) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self._write_bytes(status_code, "application/json", body)

        def _write_bytes(self, status_code: int, content_type: str, body: bytes) -> None:
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((cfg.host, cfg.port), Handler)
    tls_context = _build_tls_context(cfg)
    if tls_context is not None:
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    return server


def serve(config: BrainHiveWatchServerConfig | None = None) -> None:
    server = build_server(config)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _normalize_base_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url or "").rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", "")).rstrip("/")


def _ssl_context_for_url(
    url: str,
    *,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
) -> ssl.SSLContext | None:
    if not str(url).lower().startswith("https://"):
        return None
    if tls_insecure_skip_verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if tls_ca_file:
        return ssl.create_default_context(cafile=str(tls_ca_file))
    return ssl.create_default_context()


def _build_tls_context(cfg: BrainHiveWatchServerConfig) -> ssl.SSLContext | None:
    certfile = str(cfg.tls_certfile or "").strip()
    keyfile = str(cfg.tls_keyfile or "").strip()
    if not certfile and not keyfile:
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    cafile = str(cfg.tls_ca_file or "").strip()
    if cafile:
        context.load_verify_locations(cafile=cafile)
    return context


def _validate_tls_config(cfg: BrainHiveWatchServerConfig) -> None:
    certfile = str(cfg.tls_certfile or "").strip()
    keyfile = str(cfg.tls_keyfile or "").strip()
    if (certfile and not keyfile) or (keyfile and not certfile):
        raise ValueError("Both tls_certfile and tls_keyfile are required when Brain Hive watch TLS is enabled.")
