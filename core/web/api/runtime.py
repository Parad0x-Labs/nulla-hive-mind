from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import subprocess
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apps.nulla_agent import NullaAgent
from apps.nulla_daemon import DaemonConfig, NullaDaemon
from core import policy_engine
from core.compute_mode import ComputeModeDaemon
from core.hardware_tier import probe_machine, select_qwen_tier
from core.identity_manager import load_active_persona
from core.local_worker_pool import resolve_local_worker_capacity
from core.model_registry import ModelRegistry
from core.onboarding import (
    ensure_bootstrap_identity,
    ensure_openclaw_registration,
    get_agent_display_name,
    is_first_boot,
)
from core.public_hive_bridge import ensure_public_hive_auth
from core.release_channel import release_manifest_snapshot
from core.runtime_bootstrap import bootstrap_runtime_mode
from core.runtime_paths import resolve_workspace_root
from core.runtime_task_events import (
    new_runtime_event_stream_id,
    register_runtime_event_sink,
    unregister_runtime_event_sink,
)
from network.signer import get_local_peer_id

logger = logging.getLogger("nulla.api")

MODEL_NAME = "nulla"
_OPENCLAW_SENDER_WRAPPER_RE = re.compile(
    r"^Sender \(untrusted metadata\):\s*```json\s*\{.*?\}\s*```\s*\[[^\]]+\]\s*(.*)$",
    re.DOTALL,
)


@dataclass
class RuntimeServices:
    agent: NullaAgent | None = None
    daemon: NullaDaemon | None = None
    display_name: str = "NULLA"
    runtime_model_tag: str = "qwen2.5:7b"
    runtime_parameter_size: str = "7B"
    runtime_started_at: str = ""
    runtime_version_stamp: dict[str, Any] = field(default_factory=dict)

    def shutdown(self) -> None:
        if self.daemon:
            self.daemon.stop()


def git_output(project_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception:
        return ""
    return str(completed.stdout or "").strip()


def env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring invalid integer env override %s=%r", name, raw)
        return int(default)


def env_text(name: str, default: str) -> str:
    return str(os.environ.get(name, default) or default).strip() or str(default)


def daemon_runtime_config(*, capacity: int, local_worker_threads: int) -> DaemonConfig:
    return DaemonConfig(
        bind_host=env_text("NULLA_DAEMON_BIND_HOST", "0.0.0.0"),
        bind_port=env_int("NULLA_DAEMON_BIND_PORT", 49152),
        advertise_host=env_text("NULLA_DAEMON_ADVERTISE_HOST", "127.0.0.1"),
        health_bind_host=env_text("NULLA_DAEMON_HEALTH_BIND_HOST", "127.0.0.1"),
        health_bind_port=max(0, env_int("NULLA_DAEMON_HEALTH_PORT", 0)),
        capacity=int(capacity),
        local_worker_threads=max(2, int(local_worker_threads)),
    )


def parameter_size_for_model(model_tag: str) -> str:
    model_name = str(model_tag or "").strip().split("/", 1)[-1]
    if ":" not in model_name:
        return "7B"
    _, size = model_name.split(":", 1)
    return size.upper()


def parameter_count_for_model(model_tag: str) -> int:
    label = parameter_size_for_model(model_tag).rstrip("B")
    try:
        return int(float(label) * 1_000_000_000)
    except ValueError:
        return 7_000_000_000


def build_runtime_version_stamp(*, project_root: Path, runtime_model_tag: str, workstation_version: str) -> dict[str, Any]:
    release = dict(release_manifest_snapshot())
    branch = git_output(project_root, "branch", "--show-current")
    commit = git_output(project_root, "rev-parse", "--short=12", "HEAD")
    dirty = bool(git_output(project_root, "status", "--short"))
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
        "workstation_version": workstation_version,
        "model_tag": runtime_model_tag,
    }


def ensure_ollama_model(model_tag: str = "qwen2.5:7b") -> None:
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
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


def ensure_default_provider(registry: ModelRegistry, model_tag: str) -> None:
    from storage.model_provider_manifest import ModelProviderManifest, get_provider_manifest

    existing = get_provider_manifest("ollama-local", model_tag)
    existing_caps = {str(item).strip().lower() for item in list(getattr(existing, "capabilities", []) or [])}
    has_license = bool(
        str(getattr(existing, "license_name", None) or "").strip()
        and str(getattr(existing, "resolved_license_reference", None) or "").strip()
    )
    if existing and existing.enabled and "tool_intent" in existing_caps and has_license:
        return
    parameter_size = parameter_size_for_model(model_tag)
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


def bootstrap_runtime_services(*, project_root: Path, workstation_version: str) -> RuntimeServices:
    boot = bootstrap_runtime_mode(
        mode="api_server",
        workspace_root=resolve_workspace_root(),
        force_policy_reload=True,
        configure_logging=True,
        resolve_backend=True,
    )

    if is_first_boot():
        ensure_bootstrap_identity(
            default_agent_name="NULLA",
            privacy_pact="Store memory locally by default. Never share secrets or personal identity without explicit approval.",
        )
    peer_id = get_local_peer_id()
    from core.credit_ledger import ensure_starter_credits

    if ensure_starter_credits(peer_id):
        logger.info("Starter credits seeded for peer %s...", peer_id[:24])

    auth_result = ensure_public_hive_auth(project_root=project_root)
    if not auth_result.get("ok"):
        logger.warning("Public Hive auth is not wired for writes: %s", auth_result.get("status") or "unknown")

    probe = probe_machine()
    tier = select_qwen_tier(probe)
    runtime_model_tag = tier.ollama_tag
    runtime_parameter_size = parameter_size_for_model(runtime_model_tag)
    ensure_ollama_model(runtime_model_tag)
    logger.info("Hardware: %s | GPU: %s | Model tier: %s", probe.accelerator, probe.gpu_name or "none", tier.ollama_tag)
    runtime_version_stamp = build_runtime_version_stamp(
        project_root=project_root,
        runtime_model_tag=runtime_model_tag,
        workstation_version=workstation_version,
    )
    runtime_started_at = str(runtime_version_stamp.get("started_at") or "")
    logger.info(
        "Runtime build: %s | branch=%s | commit=%s | dirty=%s",
        runtime_version_stamp.get("build_id") or "unknown",
        runtime_version_stamp.get("branch") or "unknown",
        runtime_version_stamp.get("commit") or "unknown",
        runtime_version_stamp.get("dirty"),
    )

    compute_daemon = ComputeModeDaemon(has_gpu=probe.accelerator != "cpu")
    compute_daemon.start()

    model_registry = ModelRegistry()
    ensure_default_provider(model_registry, runtime_model_tag)
    for warning in model_registry.startup_warnings():
        logger.warning("Model warning: %s", warning)

    selection = boot.backend_selection
    if selection is None:
        raise RuntimeError("API bootstrap did not resolve a backend selection.")
    if selection.backend_name == "remote_only":
        logger.warning("No local backend found. Continuing in remote-only mode.")

    persona = load_active_persona("default")
    display_name = get_agent_display_name()
    if ensure_openclaw_registration(display_name=display_name, model_tag=runtime_model_tag):
        logger.info("OpenClaw registration ensured for agent '%s'.", display_name)
    else:
        logger.warning("OpenClaw registration could not be refreshed automatically.")

    agent = NullaAgent(
        backend_name=selection.backend_name,
        device=selection.device,
        persona_id=persona.persona_id,
    )
    agent.start()

    pool_cap = max(1, int(policy_engine.get("orchestration.local_worker_pool_max", 10)))
    daemon_capacity, _ = resolve_local_worker_capacity(requested=None, hard_cap=pool_cap)
    daemon = NullaDaemon(
        daemon_runtime_config(
            capacity=int(daemon_capacity),
            local_worker_threads=max(2, int(daemon_capacity) * 2),
        )
    )
    daemon.start()

    logger.info("%s API server ready.", display_name)
    logger.info("Peer ID: %s...", peer_id[:24])
    logger.info("Backend: %s | Device: %s", selection.backend_name, selection.device)
    logger.info("Mesh daemon: active on UDP %s", daemon.config.bind_port)

    return RuntimeServices(
        agent=agent,
        daemon=daemon,
        display_name=display_name,
        runtime_model_tag=runtime_model_tag,
        runtime_parameter_size=runtime_parameter_size,
        runtime_started_at=runtime_started_at,
        runtime_version_stamp=runtime_version_stamp,
    )


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return strip_openclaw_sender_wrapper(" ".join(content.split()).strip())
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if str(part.get("type") or "").strip().lower() == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    parts.append(text)
        return strip_openclaw_sender_wrapper(" ".join(parts).strip())
    return strip_openclaw_sender_wrapper(str(content or "").strip())


def strip_openclaw_sender_wrapper(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("Sender (untrusted metadata):"):
        return stripped
    match = _OPENCLAW_SENDER_WRAPPER_RE.match(stripped)
    if not match:
        return stripped
    return match.group(1).strip() or stripped


def normalize_chat_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            continue
        content = message_text(message.get("content", ""))
        if not content:
            continue
        history.append({"role": role, "content": content})
    return history


def extract_user_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(normalize_chat_history(messages)):
        if message.get("role") == "user":
            return str(message.get("content") or "").strip()
    return ""


def stable_openclaw_session_id(
    *,
    body: dict[str, Any],
    history: list[dict[str, str]],
    headers: dict[str, Any],
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
    digest = hashlib.sha256(json.dumps(seed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
    return f"openclaw:{digest}"


def runtime_headers(runtime: RuntimeServices) -> dict[str, str]:
    stamp = dict(runtime.runtime_version_stamp or {})
    return {
        "X-Nulla-Runtime-Version": str(stamp.get("release_version") or "unknown"),
        "X-Nulla-Runtime-Build": str(stamp.get("build_id") or "unknown"),
        "X-Nulla-Runtime-Started-At": str(stamp.get("started_at") or ""),
        "X-Nulla-Runtime-Commit": str(stamp.get("commit") or ""),
        "X-Nulla-Runtime-Dirty": "1" if bool(stamp.get("dirty")) else "0",
    }


def default_workspace_root() -> str:
    return str(resolve_workspace_root())


def run_agent(
    runtime: RuntimeServices,
    user_text: str,
    *,
    session_id: str | None = None,
    source_context: dict[str, Any] | None = None,
    workspace_root_provider: Callable[[], str] = default_workspace_root,
) -> dict[str, Any]:
    if not runtime.agent or not user_text:
        return {"response": "", "confidence": 0.0}
    base_context = {
        "surface": "channel",
        "platform": "openclaw",
        "allow_remote_fetch": policy_engine.allow_web_fallback(),
        "allow_cold_context": True,
    }
    if source_context:
        base_context.update(source_context)
    default_workspace = workspace_root_provider()
    base_context.setdefault("workspace", default_workspace)
    base_context.setdefault("workspace_root", default_workspace)
    if session_id:
        base_context["runtime_session_id"] = session_id
    return runtime.agent.run_once(
        user_text,
        session_id_override=session_id,
        source_context=base_context,
    )


def openai_chat_response(result: dict[str, Any], model: str) -> dict[str, Any]:
    response_text = str(result.get("response") or "").strip()
    return {
        "id": f"chatcmpl-{hashlib.sha256(response_text.encode()).hexdigest()[:12]}",
        "object": "chat.completion",
        "created": int(datetime.now(timezone.utc).timestamp()),
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


def ollama_chat_response(result: dict[str, Any], model: str, runtime: RuntimeServices) -> dict[str, Any]:
    response_text = str(result.get("response") or "").strip()
    return {
        "model": model,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "message": {"role": "assistant", "content": response_text},
        "done": True,
        "done_reason": "stop",
        "total_duration": 0,
        "load_duration": 0,
        "prompt_eval_count": 0,
        "prompt_eval_duration": 0,
        "eval_count": len(response_text.split()),
        "eval_duration": 0,
    }


def ollama_stream_chunk(*, model: str, content: str, created_at: str, done: bool, eval_count: int = 0) -> bytes:
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


def ollama_stream_chunks(result: dict[str, Any], model: str) -> list[bytes]:
    full_text = str(result.get("response") or "").strip()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    chunks: list[bytes] = []
    words = full_text.split(" ") if full_text else []
    for index, word in enumerate(words):
        token = word if index == 0 else " " + word
        chunks.append(ollama_stream_chunk(model=model, content=token, created_at=now, done=False))
    chunks.append(ollama_stream_chunk(model=model, content="", created_at=now, done=True, eval_count=len(words)))
    return chunks


def format_runtime_event_text(event: dict[str, Any]) -> str:
    message = str(event.get("message") or "").strip()
    return message + "\n" if message else ""


def stream_agent_with_events(
    runtime: RuntimeServices,
    user_text: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    model: str,
    include_runtime_events: bool = False,
    run_agent_provider: Callable[..., dict[str, Any]] | None = None,
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
            agent_runner = run_agent_provider or run_agent
            result = agent_runner(runtime, user_text, session_id=session_id, source_context=stream_context)
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
                content = format_runtime_event_text(dict(payload or {}))
                if content:
                    yield ollama_stream_chunk(
                        model=model,
                        content=content,
                        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        done=False,
                    )
                continue
            if kind == "error":
                for chunk in ollama_stream_chunks({"response": f"Runtime error: {payload}"}, model):
                    yield chunk
                break
            if kind == "result":
                for chunk in ollama_stream_chunks(dict(payload or {}), model):
                    yield chunk
                break
    finally:
        if include_runtime_events and stream_id:
            unregister_runtime_event_sink(stream_id)
        thread.join(timeout=0.1)
