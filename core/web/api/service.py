from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.adaptation_autopilot import get_adaptation_autopilot_status, schedule_adaptation_autopilot_tick
from core.control_plane_workspace import collect_control_plane_status
from core.nulla_workstation_ui import NULLA_WORKSTATION_DEPLOYMENT_VERSION
from core.runtime_capabilities import runtime_capability_snapshot
from core.runtime_task_events import list_runtime_session_events, list_runtime_sessions
from core.runtime_task_rail import render_runtime_task_rail_html
from storage.adaptation_store import (
    list_adaptation_eval_runs,
    list_adaptation_job_events,
    list_adaptation_jobs,
)

from .runtime import (
    RuntimeServices,
    extract_user_message,
    normalize_chat_history,
    ollama_chat_response,
    openai_chat_response,
    parameter_count_for_model,
    run_agent,
    runtime_headers,
    stable_openclaw_session_id,
    stream_agent_with_events,
)


@dataclass
class ApiResponse:
    status: int
    content_type: str
    body: bytes | None = None
    stream: Iterable[bytes] | None = None
    headers: dict[str, str] = field(default_factory=dict)


def json_response(status: int, payload: Any, *, headers: dict[str, str] | None = None) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type="application/json",
        body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=dict(headers or {}),
    )


def text_response(status: int, text: str, *, headers: dict[str, str] | None = None) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type="text/plain; charset=utf-8",
        body=str(text).encode("utf-8"),
        headers=dict(headers or {}),
    )


def html_response(status: int, html: str, *, headers: dict[str, str] | None = None) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type="text/html; charset=utf-8",
        body=str(html).encode("utf-8"),
        headers=dict(headers or {}),
    )


def stream_response(
    status: int,
    stream: Iterable[bytes],
    *,
    content_type: str,
    headers: dict[str, str] | None = None,
) -> ApiResponse:
    return ApiResponse(
        status=status,
        content_type=content_type,
        stream=stream,
        headers=dict(headers or {}),
    )


def apply_runtime_headers(response: ApiResponse, runtime: RuntimeServices) -> ApiResponse:
    headers = runtime_headers(runtime)
    headers.update(response.headers)
    response.headers = headers
    return response


def dispatch_get(
    *,
    path: str,
    query: dict[str, list[str]],
    runtime: RuntimeServices,
    model_name: str,
    capability_snapshot_provider: Callable[[], dict[str, Any]] = runtime_capability_snapshot,
) -> ApiResponse:
    normalized_path = path.rstrip("/") or "/"

    if normalized_path in {"/task-rail", "/trace"}:
        return apply_runtime_headers(
            html_response(
                200,
                render_runtime_task_rail_html(),
                headers={
                    "X-Nulla-Workstation-Version": NULLA_WORKSTATION_DEPLOYMENT_VERSION,
                    "X-Nulla-Workstation-Surface": "trace-rail",
                },
            ),
            runtime,
        )

    if normalized_path == "/":
        return apply_runtime_headers(text_response(200, "Ollama is running"), runtime)

    if normalized_path in {"/api/tags", "/v1/models"}:
        payload = {
            "models": [
                {
                    "name": model_name,
                    "model": model_name,
                    "modified_at": datetime.now(timezone.utc).isoformat(),
                    "size": 0,
                    "digest": "nulla-runtime",
                    "details": {
                        "parent_model": "",
                        "format": "nulla",
                        "family": "qwen",
                        "parameter_size": runtime.runtime_parameter_size,
                        "quantization_level": "runtime",
                    },
                }
            ]
        }
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path in {"/healthz", "/v1/healthz"}:
        payload = {
            "ok": True,
            "agent": runtime.display_name,
            "daemon": runtime.daemon is not None,
            "runtime": dict(runtime.runtime_version_stamp or {}),
            "capabilities": capability_snapshot_provider(),
        }
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path in {"/api/runtime/version", "/v1/runtime/version"}:
        return apply_runtime_headers(json_response(200, dict(runtime.runtime_version_stamp or {})), runtime)

    if normalized_path in {"/api/runtime/capabilities", "/v1/runtime/capabilities"}:
        return apply_runtime_headers(json_response(200, capability_snapshot_provider()), runtime)

    if normalized_path == "/api/runtime/sessions":
        return apply_runtime_headers(json_response(200, {"sessions": list_runtime_sessions(limit=24)}), runtime)

    if normalized_path == "/api/runtime/events":
        session_id = str((query.get("session") or [""])[0] or "").strip()
        after_seq = int(str((query.get("after") or ["0"])[0] or "0"))
        limit = int(str((query.get("limit") or ["120"])[0] or "120"))
        events = list_runtime_session_events(session_id, after_seq=after_seq, limit=limit)
        next_after = after_seq
        if events:
            next_after = max(int(item.get("seq") or 0) for item in events)
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "session_id": session_id,
                    "events": events,
                    "next_after": next_after,
                },
            ),
            runtime,
        )

    if normalized_path == "/api/runtime/control-plane/status":
        return apply_runtime_headers(json_response(200, collect_control_plane_status()), runtime)

    if normalized_path in {"/api/adaptation/status", "/api/adaptation/loop"}:
        return apply_runtime_headers(json_response(200, get_adaptation_autopilot_status()), runtime)

    if normalized_path == "/api/adaptation/jobs":
        limit = int(str((query.get("limit") or ["24"])[0] or "24"))
        return apply_runtime_headers(json_response(200, {"jobs": list_adaptation_jobs(limit=max(1, min(limit, 200)))}), runtime)

    if normalized_path == "/api/adaptation/job-events":
        job_id = str((query.get("job") or [""])[0] or "").strip()
        limit = int(str((query.get("limit") or ["120"])[0] or "120"))
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "job_id": job_id,
                    "events": list_adaptation_job_events(job_id, limit=max(1, min(limit, 500))),
                },
            ),
            runtime,
        )

    if normalized_path == "/api/adaptation/evals":
        job_id = str((query.get("job") or [""])[0] or "").strip()
        limit = int(str((query.get("limit") or ["120"])[0] or "120"))
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "job_id": job_id,
                    "evals": list_adaptation_eval_runs(job_id=job_id or None, limit=max(1, min(limit, 500))),
                },
            ),
            runtime,
        )

    return apply_runtime_headers(json_response(404, {"error": "not found"}), runtime)


def dispatch_post(
    *,
    path: str,
    body: dict[str, Any],
    headers: dict[str, Any],
    runtime: RuntimeServices,
    model_name: str,
    workspace_root_provider,
    normalize_chat_history_provider: Callable[[list[dict[str, Any]]], list[dict[str, str]]] = normalize_chat_history,
    extract_user_message_provider: Callable[[list[dict[str, Any]]], str] = extract_user_message,
    stable_openclaw_session_id_provider: Callable[..., str] = stable_openclaw_session_id,
    run_agent_provider: Callable[..., dict[str, Any]] = run_agent,
    stream_agent_with_events_provider: Callable[..., Iterable[bytes]] = stream_agent_with_events,
) -> ApiResponse:
    normalized_path = path.rstrip("/") or "/"

    if normalized_path in {"/api/chat", "/v1/chat/completions"}:
        messages = list(body.get("messages", []) or [])
        history = normalize_chat_history_provider(messages)
        user_text = extract_user_message_provider(messages)
        if not user_text:
            return apply_runtime_headers(json_response(400, {"error": "no user message found"}), runtime)

        model = body.get("model", model_name)
        stream = body.get("stream", False)
        include_runtime_events = bool(body.get("stream_runtime_events") or body.get("include_runtime_events"))
        session_id = stable_openclaw_session_id_provider(body=body, history=history, headers=headers)
        requested_workspace = str(
            body.get("workspace") or body.get("workspace_root") or body.get("cwd") or body.get("projectRoot") or ""
        ).strip()
        default_workspace = workspace_root_provider()
        source_context = {
            "client_conversation_history": history,
            "client_history_message_count": len(history),
            "conversation_history": history,
            "history_message_count": len(history),
            "workspace": requested_workspace or default_workspace,
            "workspace_root": requested_workspace or default_workspace,
        }

        if stream:
            stream_iter = stream_agent_with_events_provider(
                runtime,
                user_text,
                session_id=session_id,
                source_context=source_context,
                model=model,
                include_runtime_events=include_runtime_events,
            )
            return apply_runtime_headers(
                stream_response(200, stream_iter, content_type="application/x-ndjson"),
                runtime,
            )

        try:
            result = run_agent_provider(
                runtime,
                user_text,
                session_id=session_id,
                source_context=source_context,
                workspace_root_provider=workspace_root_provider,
            )
        except Exception as exc:
            return apply_runtime_headers(json_response(500, {"error": str(exc)}), runtime)
        payload = openai_chat_response(result, model) if normalized_path.startswith("/v1/") else ollama_chat_response(result, model, runtime)
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/api/generate":
        prompt = str(body.get("prompt", "")).strip()
        if not prompt:
            return apply_runtime_headers(json_response(400, {"error": "no prompt"}), runtime)
        model = body.get("model", model_name)
        try:
            result = run_agent_provider(
                runtime,
                prompt,
                workspace_root_provider=workspace_root_provider,
            )
        except Exception as exc:
            return apply_runtime_headers(json_response(500, {"error": str(exc)}), runtime)
        response_text = str(result.get("response") or "").strip()
        return apply_runtime_headers(
            json_response(
                200,
                {
                    "model": model,
                    "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "response": response_text,
                    "done": True,
                },
            ),
            runtime,
        )

    if normalized_path == "/api/show":
        name = str(body.get("name") or body.get("model") or "").strip()
        if name and name not in {model_name, f"{model_name}:latest"}:
            return apply_runtime_headers(json_response(404, {"error": f"model '{name}' not found"}), runtime)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = {
            "modelfile": f"# NULLA runtime model\nFROM {model_name}",
            "parameters": "stop <|im_end|>",
            "template": "{{ .Prompt }}",
            "details": {
                "parent_model": "",
                "format": "nulla",
                "family": "qwen",
                "families": ["qwen"],
                "parameter_size": runtime.runtime_parameter_size,
                "quantization_level": "runtime",
            },
            "model_info": {
                "general.architecture": "qwen2",
                "general.parameter_count": parameter_count_for_model(runtime.runtime_model_tag),
                "general.file_type": 0,
            },
            "modified_at": now,
        }
        return apply_runtime_headers(json_response(200, payload), runtime)

    if normalized_path == "/api/adaptation/loop/tick":
        return apply_runtime_headers(json_response(200, schedule_adaptation_autopilot_tick(force=True, wait=True)), runtime)

    return apply_runtime_headers(json_response(404, {"error": "not found"}), runtime)
