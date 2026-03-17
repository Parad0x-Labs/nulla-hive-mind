"""Ollama-compatible HTTP API that routes through NULLA's full agent pipeline.

OpenClaw (or any Ollama client) connects here instead of raw Ollama.
Every message goes through: dialogue memory -> classification -> tiered context
-> memory-first routing -> mesh queries -> plan -> response -> learning shards.

Also starts the mesh daemon in-process so presence/knowledge/task exchange is live.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("nulla.api")

import contextlib

from apps.nulla_agent import NullaAgent
from apps.nulla_daemon import DaemonConfig, NullaDaemon
from core import policy_engine
from core.adaptation_autopilot import get_adaptation_autopilot_status, schedule_adaptation_autopilot_tick
from core.backend_manager import BackendManager
from core.compute_mode import ComputeModeDaemon
from core.control_plane_workspace import collect_control_plane_status
from core.credit_ledger import ensure_starter_credits
from core.hardware_tier import probe_machine, select_qwen_tier
from core.identity_manager import load_active_persona
from core.local_worker_pool import resolve_local_worker_capacity
from core.logging_config import setup_logging
from core.model_registry import ModelRegistry
from core.nulla_workstation_ui import NULLA_WORKSTATION_DEPLOYMENT_VERSION
from core.onboarding import (
    ensure_bootstrap_identity,
    ensure_openclaw_registration,
    get_agent_display_name,
    is_first_boot,
)
from core.public_hive_bridge import ensure_public_hive_auth
from core.release_channel import release_manifest_snapshot
from core.runtime_task_events import (
    list_runtime_session_events,
    list_runtime_sessions,
    new_runtime_event_stream_id,
    register_runtime_event_sink,
    unregister_runtime_event_sink,
)
from core.runtime_task_rail import render_runtime_task_rail_html
from network.signer import get_local_peer_id
from storage.adaptation_store import (
    list_adaptation_eval_runs,
    list_adaptation_job_events,
    list_adaptation_jobs,
)
from storage.db import healthcheck
from storage.migrations import run_migrations

NULLA_API_PORT = 11435
MODEL_NAME = "nulla"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

_agent: NullaAgent | None = None
_daemon: NullaDaemon | None = None
_display_name: str = "NULLA"
_runtime_model_tag: str = "qwen2.5:7b"
_runtime_parameter_size: str = "7B"
_runtime_started_at: str = ""
_runtime_version_stamp: dict[str, Any] = {}
_OPENCLAW_SENDER_WRAPPER_RE = re.compile(
    r"^Sender \(untrusted metadata\):\s*```json\s*\{.*?\}\s*```\s*\[[^\]]+\]\s*(.*)$",
    re.DOTALL,
)


def _git_output(*args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception:
        return ""
    return str(completed.stdout or "").strip()


def _build_runtime_version_stamp() -> dict[str, Any]:
    release = dict(release_manifest_snapshot())
    branch = _git_output("branch", "--show-current")
    commit = _git_output("rev-parse", "--short=12", "HEAD")
    dirty = bool(_git_output("status", "--short"))
    release_version = str(release.get("release_version") or "").strip() or "unknown-release"
    build_parts = [release_version]
    if commit:
        build_parts.append(commit)
    build_id = "+".join(build_parts)
    if dirty:
        build_id = f"{build_id}.dirty"
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {
        "release_version": release_version,
        "minimum_compatible_release": str(release.get("minimum_compatible_release") or "").strip(),
        "protocol_version": int(release.get("protocol_version") or 0),
        "rollout_stage": str(release.get("rollout_stage") or "").strip(),
        "channel_name": str(release.get("channel_name") or "").strip(),
        "branch": branch,
        "commit": commit,
        "dirty": dirty,
        "build_id": build_id,
        "started_at": started_at,
        "pid": os.getpid(),
        "workstation_version": NULLA_WORKSTATION_DEPLOYMENT_VERSION,
        "model_tag": _runtime_model_tag,
    }


def _parameter_size_for_model(model_tag: str) -> str:
    model_name = str(model_tag or "").strip().split("/", 1)[-1]
    if ":" not in model_name:
        return "7B"
    _, size = model_name.split(":", 1)
    return size.upper()


def _parameter_count_for_model(model_tag: str) -> int:
    label = _parameter_size_for_model(model_tag).rstrip("B")
    try:
        return int(float(label) * 1_000_000_000)
    except ValueError:
        return 7_000_000_000


def _ensure_ollama_model(model_tag: str = "qwen2.5:7b") -> None:
    """Pull the Ollama model if it's missing. Survives crashes/power cuts."""
    import subprocess
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if model_tag in result.stdout:
            return
    except Exception:
        pass
    logger.info("Ollama model '%s' missing — pulling now (this may take a few minutes on first run)...", model_tag)
    try:
        subprocess.run(
            ["ollama", "pull", model_tag],
            timeout=1200,
            capture_output=True,
        )
        logger.info("Ollama model '%s' pulled successfully.", model_tag)
    except Exception as exc:
        logger.warning("Failed to pull Ollama model '%s': %s — LLM responses will fall back to planning mode.", model_tag, exc)


def _ensure_default_provider(registry: ModelRegistry, model_tag: str) -> None:
    """Register the local Ollama Qwen provider if not already present."""
    from storage.model_provider_manifest import ModelProviderManifest, get_provider_manifest
    existing = get_provider_manifest("ollama-local", model_tag)
    existing_caps = {str(item).strip().lower() for item in list(getattr(existing, "capabilities", []) or [])}
    has_license = bool(
        str(getattr(existing, "license_name", None) or "").strip()
        and str(getattr(existing, "resolved_license_reference", None) or "").strip()
    )
    if existing and existing.enabled and "tool_intent" in existing_caps and has_license:
        return
    parameter_size = _parameter_size_for_model(model_tag)
    manifest = ModelProviderManifest(
        provider_name="ollama-local",
        model_name=model_tag,
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/main/LICENSE",
        license_url_or_reference="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/main/LICENSE",
        weight_location="external",
        runtime_dependency="ollama",
        notes=f"Local Qwen via Ollama ({parameter_size}) — auto-registered by NULLA API server",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "structured_json", "tool_intent"],
        runtime_config={
            "base_url": "http://127.0.0.1:11434",
            "api_path": "/v1/chat/completions",
            "health_path": "/v1/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.7,
            "supports_json_mode": False,
        },
        metadata={
            "runtime_family": "ollama",
            "confidence_baseline": 0.65,
            "parameter_count": parameter_size,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    logger.info("Auto-registered default provider: %s", manifest.provider_id)


def _bootstrap() -> None:
    global _agent, _daemon, _display_name, _runtime_model_tag, _runtime_parameter_size, _runtime_started_at, _runtime_version_stamp

    run_migrations()
    if not healthcheck():
        raise RuntimeError("Database healthcheck failed.")

    setup_logging(
        level=str(policy_engine.get("observability.log_level", "INFO")),
        json_output=bool(policy_engine.get("observability.json_logs", True)),
    )
    policy_engine.load(force_reload=True)

    if is_first_boot():
        ensure_bootstrap_identity(
            default_agent_name="NULLA",
            privacy_pact="Store memory locally by default. Never share secrets or personal identity without explicit approval.",
        )
    peer_id = get_local_peer_id()
    if ensure_starter_credits(peer_id):
        logger.info("Starter credits seeded for peer %s...", peer_id[:24])

    auth_result = ensure_public_hive_auth(project_root=Path(__file__).resolve().parents[1])
    if not auth_result.get("ok"):
        logger.warning("Public Hive auth is not wired for writes: %s", auth_result.get("status") or "unknown")

    probe = probe_machine()
    tier = select_qwen_tier(probe)
    _runtime_model_tag = tier.ollama_tag
    _runtime_parameter_size = _parameter_size_for_model(_runtime_model_tag)
    _ensure_ollama_model(_runtime_model_tag)
    logger.info("Hardware: %s | GPU: %s | Model tier: %s", probe.accelerator, probe.gpu_name or "none", tier.ollama_tag)
    _runtime_version_stamp = _build_runtime_version_stamp()
    _runtime_started_at = str(_runtime_version_stamp.get("started_at") or "")
    logger.info(
        "Runtime build: %s | branch=%s | commit=%s | dirty=%s",
        _runtime_version_stamp.get("build_id") or "unknown",
        _runtime_version_stamp.get("branch") or "unknown",
        _runtime_version_stamp.get("commit") or "unknown",
        _runtime_version_stamp.get("dirty"),
    )

    compute_daemon = ComputeModeDaemon(has_gpu=probe.accelerator != "cpu")
    compute_daemon.start()

    model_registry = ModelRegistry()
    _ensure_default_provider(model_registry, _runtime_model_tag)
    for w in model_registry.startup_warnings():
        logger.warning("Model warning: %s", w)

    manager = BackendManager()
    hw = manager.detect_hardware()
    selection = manager.select_backend(hw)
    if not manager.healthcheck(selection):
        allow_remote_only = policy_engine.allow_remote_only_without_backend()
        if not allow_remote_only:
            raise RuntimeError("No supported backend found.")
        logger.warning("No local backend found. Continuing in remote-only mode.")
        selection = type("Selection", (), {"backend_name": "remote_only", "device": "cpu"})()

    persona = load_active_persona("default")
    _display_name = get_agent_display_name()
    if ensure_openclaw_registration(display_name=_display_name, model_tag=_runtime_model_tag):
        logger.info("OpenClaw registration ensured for agent '%s'.", _display_name)
    else:
        logger.warning("OpenClaw registration could not be refreshed automatically.")

    _agent = NullaAgent(
        backend_name=selection.backend_name,
        device=selection.device,
        persona_id=persona.persona_id,
    )
    _agent.start()

    pool_cap = max(1, int(policy_engine.get("orchestration.local_worker_pool_max", 10)))
    daemon_capacity, _ = resolve_local_worker_capacity(requested=None, hard_cap=pool_cap)

    _daemon = NullaDaemon(DaemonConfig(
        capacity=int(daemon_capacity),
        local_worker_threads=max(2, int(daemon_capacity) * 2),
    ))
    _daemon.start()

    logger.info("%s API server ready.", _display_name)
    logger.info("Peer ID: %s...", peer_id[:24])
    logger.info("Backend: %s | Device: %s", selection.backend_name, selection.device)
    logger.info("Mesh daemon: active on UDP %s", _daemon.config.bind_port)


def _extract_user_message(messages: list[dict[str, Any]]) -> str:
    """Pull the last user message from an OpenAI/Ollama messages array."""
    for msg in reversed(_normalize_chat_history(messages)):
        if msg.get("role") == "user":
            return str(msg.get("content") or "").strip()
    return ""


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return _strip_openclaw_sender_wrapper(" ".join(content.split()).strip())
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = str(part.get("type") or "").strip().lower()
            if kind == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    parts.append(text)
        return _strip_openclaw_sender_wrapper(" ".join(parts).strip())
    return _strip_openclaw_sender_wrapper(str(content or "").strip())


def _strip_openclaw_sender_wrapper(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("Sender (untrusted metadata):"):
        return stripped
    match = _OPENCLAW_SENDER_WRAPPER_RE.match(stripped)
    if not match:
        return stripped
    return match.group(1).strip() or stripped


def _normalize_chat_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            continue
        content = _message_text(message.get("content", ""))
        if not content:
            continue
        history.append({"role": role, "content": content})
    return history


def _stable_openclaw_session_id(
    *,
    body: dict[str, Any],
    history: list[dict[str, str]],
    headers: Any,
) -> str:
    for key in (
        "session_id",
        "sessionId",
        "session",
        "conversation_id",
        "conversationId",
        "chat_id",
        "chatId",
        "thread_id",
        "threadId",
    ):
        value = str(body.get(key) or "").strip()
        if value:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
            return f"openclaw:{digest}"

    for header_name in ("X-Session-Id", "X-Conversation-Id", "X-Thread-Id", "X-OpenClaw-Session"):
        value = str(headers.get(header_name) or "").strip()
        if value:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
            return f"openclaw:{digest}"

    seed = {
        "model": str(body.get("model") or MODEL_NAME),
        "history": history[:4],
    }
    digest = hashlib.sha256(
        json.dumps(seed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return f"openclaw:{digest}"


def _default_workspace_root() -> str:
    override = str(
        os.environ.get("NULLA_WORKSPACE_ROOT")
        or os.environ.get("NULLA_PROJECT_ROOT")
        or ""
    ).strip()
    if override:
        return str(Path(override).expanduser().resolve())
    try:
        return str(Path.cwd().resolve())
    except FileNotFoundError:
        return str(PROJECT_ROOT)


def _run_agent(
    user_text: str,
    *,
    session_id: str | None = None,
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _agent or not user_text:
        return {"response": "", "confidence": 0.0}
    base_context = {
        "surface": "channel",
        "platform": "openclaw",
        "allow_remote_fetch": policy_engine.allow_web_fallback(),
        "allow_cold_context": True,
    }
    if source_context:
        base_context.update(source_context)
    default_workspace = _default_workspace_root()
    base_context.setdefault("workspace", default_workspace)
    base_context.setdefault("workspace_root", default_workspace)
    if session_id:
        base_context["runtime_session_id"] = session_id
    return _agent.run_once(
        user_text,
        session_id_override=session_id,
        source_context={
            **base_context,
        },
    )


def _openai_chat_response(result: dict[str, Any], model: str) -> dict[str, Any]:
    """OpenAI-compatible /v1/chat/completions format."""
    import time as _time

    response_text = str(result.get("response") or "").strip()
    return {
        "id": f"chatcmpl-{hashlib.sha256(response_text.encode()).hexdigest()[:12]}",
        "object": "chat.completion",
        "created": int(_time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(response_text.split()),
            "total_tokens": len(response_text.split()),
        },
    }


def _ollama_chat_response(result: dict[str, Any], model: str) -> dict[str, Any]:
    response_text = str(result.get("response") or "").strip()
    return {
        "model": model,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "message": {
            "role": "assistant",
            "content": response_text,
        },
        "done": True,
        "done_reason": "stop",
        "total_duration": 0,
        "load_duration": 0,
        "prompt_eval_count": 0,
        "prompt_eval_duration": 0,
        "eval_count": len(response_text.split()),
        "eval_duration": 0,
    }


def _ollama_stream_chunk(
    *,
    model: str,
    content: str,
    created_at: str,
    done: bool,
    eval_count: int = 0,
) -> bytes:
    payload: dict[str, Any] = {
        "model": model,
        "created_at": created_at,
        "message": {"role": "assistant", "content": content},
        "done": done,
    }
    if done:
        payload.update(
            {
                "done_reason": "stop",
                "total_duration": 0,
                "load_duration": 0,
                "prompt_eval_count": 0,
                "prompt_eval_duration": 0,
                "eval_count": eval_count,
                "eval_duration": 0,
            }
        )
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def _ollama_stream_chunks(result: dict[str, Any], model: str) -> list[bytes]:
    """Build NDJSON stream chunks in Ollama streaming format."""
    full_text = str(result.get("response") or "").strip()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    chunks: list[bytes] = []
    words = full_text.split(" ") if full_text else []
    for i, word in enumerate(words):
        token = word if i == 0 else " " + word
        chunks.append(
            _ollama_stream_chunk(
                model=model,
                content=token,
                created_at=now,
                done=False,
            )
        )
    chunks.append(
        _ollama_stream_chunk(
            model=model,
            content="",
            created_at=now,
            done=True,
            eval_count=len(words),
        )
    )
    return chunks


def _format_runtime_event_text(event: dict[str, Any]) -> str:
    message = str(event.get("message") or "").strip()
    return message + "\n" if message else ""


def _stream_agent_with_events(
    user_text: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    model: str,
    include_runtime_events: bool = False,
) -> Iterator[bytes]:
    event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
    stream_context = dict(source_context or {})
    stream_id = ""
    if include_runtime_events:
        stream_id = new_runtime_event_stream_id()
        stream_context["runtime_event_stream_id"] = stream_id

    def sink(event: dict[str, Any]) -> None:
        event_queue.put(("event", dict(event)))

    def worker() -> None:
        try:
            result = _run_agent(
                user_text,
                session_id=session_id,
                source_context=stream_context,
            )
            event_queue.put(("result", result))
        except Exception as exc:
            event_queue.put(("error", str(exc)))

    if include_runtime_events and stream_id:
        register_runtime_event_sink(stream_id, sink)
    thread = threading.Thread(target=worker, name="nulla-openclaw-stream", daemon=True)
    thread.start()

    try:
        while True:
            kind, payload = event_queue.get()
            if kind == "event":
                content = _format_runtime_event_text(dict(payload or {}))
                if content:
                    yield _ollama_stream_chunk(
                        model=model,
                        content=content,
                        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        done=False,
                    )
                continue
            if kind == "error":
                for chunk in _ollama_stream_chunks({"response": f"Runtime error: {payload}"}, model):
                    yield chunk
                break
            if kind == "result":
                for chunk in _ollama_stream_chunks(dict(payload or {}), model):
                    yield chunk
                break
    finally:
        if include_runtime_events and stream_id:
            unregister_runtime_event_sink(stream_id)
        thread.join(timeout=0.1)


class NullaAPIHandler(BaseHTTPRequestHandler):
    def _send_runtime_headers(self) -> None:
        stamp = dict(_runtime_version_stamp or {})
        self.send_header("X-Nulla-Runtime-Version", str(stamp.get("release_version") or "unknown"))
        self.send_header("X-Nulla-Runtime-Build", str(stamp.get("build_id") or "unknown"))
        self.send_header("X-Nulla-Runtime-Started-At", str(stamp.get("started_at") or ""))
        self.send_header("X-Nulla-Runtime-Commit", str(stamp.get("commit") or ""))
        self.send_header("X-Nulla-Runtime-Dirty", "1" if bool(stamp.get("dirty")) else "0")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in {"/task-rail", "/trace"}:
            body = render_runtime_task_rail_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._send_runtime_headers()
            self.send_header("X-Nulla-Workstation-Version", NULLA_WORKSTATION_DEPLOYMENT_VERSION)
            self.send_header("X-Nulla-Workstation-Surface", "trace-rail")
            self.end_headers()
            self.wfile.write(body)
            return

        # Ollama health check
        if path in {"", "/"}:
            self._json_response(200, "Ollama is running")
            return

        # List models
        if path in {"/api/tags", "/v1/models"}:
            body = {
                "models": [{
                    "name": MODEL_NAME,
                    "model": MODEL_NAME,
                    "modified_at": datetime.now(timezone.utc).isoformat(),
                    "size": 0,
                    "digest": "nulla-runtime",
                    "details": {
                        "parent_model": "",
                        "format": "nulla",
                        "family": "qwen",
                        "parameter_size": _runtime_parameter_size,
                        "quantization_level": "runtime",
                    },
                }],
            }
            self._json_response(200, body)
            return

        # Health
        if path in {"/healthz", "/v1/healthz"}:
            self._json_response(
                200,
                {
                    "ok": True,
                    "agent": _display_name,
                    "daemon": _daemon is not None,
                    "runtime": dict(_runtime_version_stamp or {}),
                },
            )
            return

        if path in {"/api/runtime/version", "/v1/runtime/version"}:
            self._json_response(200, dict(_runtime_version_stamp or {}))
            return

        if path == "/api/runtime/sessions":
            self._json_response(200, {"sessions": list_runtime_sessions(limit=24)})
            return

        if path == "/api/runtime/events":
            query = parse_qs(parsed.query)
            session_id = str((query.get("session") or [""])[0] or "").strip()
            after_seq = int(str((query.get("after") or ["0"])[0] or "0"))
            limit = int(str((query.get("limit") or ["120"])[0] or "120"))
            events = list_runtime_session_events(session_id, after_seq=after_seq, limit=limit)
            next_after = after_seq
            if events:
                next_after = max(int(item.get("seq") or 0) for item in events)
            self._json_response(
                200,
                {
                    "session_id": session_id,
                    "events": events,
                    "next_after": next_after,
                },
            )
            return

        if path == "/api/runtime/control-plane/status":
            self._json_response(200, collect_control_plane_status())
            return

        if path == "/api/adaptation/status":
            self._json_response(200, get_adaptation_autopilot_status())
            return

        if path == "/api/adaptation/jobs":
            query = parse_qs(parsed.query)
            limit = int(str((query.get("limit") or ["24"])[0] or "24"))
            self._json_response(200, {"jobs": list_adaptation_jobs(limit=max(1, min(limit, 200)))})
            return

        if path == "/api/adaptation/job-events":
            query = parse_qs(parsed.query)
            job_id = str((query.get("job") or [""])[0] or "").strip()
            limit = int(str((query.get("limit") or ["120"])[0] or "120"))
            self._json_response(
                200,
                {
                    "job_id": job_id,
                    "events": list_adaptation_job_events(job_id, limit=max(1, min(limit, 500))),
                },
            )
            return

        if path == "/api/adaptation/evals":
            query = parse_qs(parsed.query)
            job_id = str((query.get("job") or [""])[0] or "").strip()
            limit = int(str((query.get("limit") or ["120"])[0] or "120"))
            self._json_response(
                200,
                {
                    "job_id": job_id,
                    "evals": list_adaptation_eval_runs(job_id=job_id or None, limit=max(1, min(limit, 500))),
                },
            )
            return

        if path == "/api/adaptation/loop":
            self._json_response(200, get_adaptation_autopilot_status())
            return

        self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.rstrip("/")

        if path in {"/api/chat", "/v1/chat/completions"}:
            self._handle_chat(openai_compat=path.startswith("/v1/"))
            return

        if path == "/api/generate":
            self._handle_generate()
            return

        if path == "/api/show":
            self._handle_show()
            return

        if path == "/api/adaptation/loop/tick":
            self._json_response(200, schedule_adaptation_autopilot_tick(force=True, wait=True))
            return

        self._json_response(404, {"error": "not found"})

    def _handle_chat(self, *, openai_compat: bool = False) -> None:
        body = self._read_json_body()
        if body is None:
            return

        messages = list(body.get("messages", []) or [])
        history = _normalize_chat_history(messages)
        user_text = _extract_user_message(messages)
        if not user_text:
            self._json_response(400, {"error": "no user message found"})
            return

        model = body.get("model", MODEL_NAME)
        stream = body.get("stream", False)
        include_runtime_events = bool(body.get("stream_runtime_events") or body.get("include_runtime_events"))
        session_id = _stable_openclaw_session_id(body=body, history=history, headers=self.headers)
        requested_workspace = str(
            body.get("workspace") or body.get("workspace_root") or body.get("cwd") or body.get("projectRoot") or ""
        ).strip()
        default_workspace = _default_workspace_root()
        source_context = {
            "client_conversation_history": history,
            "client_history_message_count": len(history),
            "conversation_history": history,
            "history_message_count": len(history),
            "workspace": requested_workspace or default_workspace,
            "workspace_root": requested_workspace or default_workspace,
        }

        if stream:
            chunk_iter = _stream_agent_with_events(
                user_text,
                session_id=session_id,
                source_context=source_context,
                model=model,
                include_runtime_events=include_runtime_events,
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            try:
                for chunk in chunk_iter:
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except BrokenPipeError:
                        break
            finally:
                chunk_iter.close()
            return

        try:
            result = _run_agent(
                user_text,
                session_id=session_id,
                source_context=source_context,
            )
        except Exception as exc:
            self._json_response(500, {"error": str(exc)})
            return

        if openai_compat:
            response = _openai_chat_response(result, model)
        else:
            response = _ollama_chat_response(result, model)
        self._json_response(200, response)

    def _handle_generate(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        prompt = str(body.get("prompt", "")).strip()
        if not prompt:
            self._json_response(400, {"error": "no prompt"})
            return

        model = body.get("model", MODEL_NAME)
        try:
            result = _run_agent(prompt)
        except Exception as exc:
            self._json_response(500, {"error": str(exc)})
            return

        response_text = str(result.get("response") or "").strip()
        resp = {
            "model": model,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "response": response_text,
            "done": True,
        }
        self._json_response(200, resp)

    def _handle_show(self) -> None:
        body = self._read_json_body()
        if body is None:
            return
        name = str(body.get("name") or body.get("model") or "").strip()
        if name and name not in {MODEL_NAME, f"{MODEL_NAME}:latest"}:
            self._json_response(404, {"error": f"model '{name}' not found"})
            return
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self._json_response(200, {
            "modelfile": f"# NULLA runtime model\nFROM {MODEL_NAME}",
            "parameters": "stop <|im_end|>",
            "template": "{{ .Prompt }}",
            "details": {
                "parent_model": "",
                "format": "nulla",
                "family": "qwen",
                "families": ["qwen"],
                "parameter_size": _runtime_parameter_size,
                "quantization_level": "runtime",
            },
            "model_info": {
                "general.architecture": "qwen2",
                "general.parameter_count": _parameter_count_for_model(_runtime_model_tag),
                "general.file_type": 0,
            },
            "modified_at": now,
        })

    def _read_json_body(self) -> dict[str, Any] | None:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            self._json_response(400, {"error": "empty body"})
            return None
        raw = self.rfile.read(content_length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            self._json_response(400, {"error": "invalid JSON"})
            return None

    def _json_response(self, status: int, body: Any) -> None:
        if isinstance(body, str):
            payload = body.encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        else:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            content_type = "application/json"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self._send_runtime_headers()
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress default request logging; audit_logger handles it
        return


def main() -> int:
    parser = argparse.ArgumentParser(prog="nulla-api-server")
    parser.add_argument("--port", type=int, default=NULLA_API_PORT)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logger.info("Bootstrapping NULLA runtime...")
    _bootstrap()

    server = ThreadingHTTPServer((args.bind, args.port), NullaAPIHandler)
    logger.info("NULLA API listening on http://%s:%s", args.bind, args.port)
    logger.info("OpenClaw can connect to this as an Ollama provider.")

    stop = threading.Event()

    def _on_signal(_sig, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        while not stop.wait(1.0):
            pass
    finally:
        logger.info("Shutting down...")
        server.shutdown()
        if _daemon:
            _daemon.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
