from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9_\-]+")


@dataclass(frozen=True)
class ChannelRequest:
    platform: str
    user_id: str
    text: str
    channel_id: str | None = None
    persona_id: str = "default"
    device_hint: str = "channel"
    surface: str = "channel"
    allow_cold_context: bool = False
    allow_remote_fetch: bool = False
    attachments: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class ChannelOutputPolicy:
    platform: str
    max_chars: int
    compact: bool
    metadata_first: bool


@dataclass(frozen=True)
class ChannelGatewayResult:
    task_id: str
    platform: str
    session_id: str
    response_text: str
    truncated: bool
    mode: str
    confidence: float
    prompt_assembly_report: dict[str, Any]
    source_context: dict[str, Any]


def _safe_segment(value: str, *, fallback: str) -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("-", value.strip()).strip("-").lower()
    return cleaned or fallback


def channel_session_id(
    *,
    platform: str,
    user_id: str,
    channel_id: str | None,
    persona_id: str,
    device_hint: str = "channel",
) -> str:
    safe_platform = _safe_segment(platform, fallback="platform")
    safe_user = _safe_segment(user_id, fallback="user")
    safe_channel = _safe_segment(channel_id or "direct", fallback="direct")
    safe_persona = _safe_segment(persona_id, fallback="default")
    safe_device = _safe_segment(device_hint, fallback="channel")
    return f"{safe_device}:{safe_platform}:{safe_channel}:{safe_user}:{safe_persona}"


def channel_output_policy(platform: str) -> ChannelOutputPolicy:
    normalized = _safe_segment(platform, fallback="channel")
    if normalized == "telegram":
        return ChannelOutputPolicy(platform=normalized, max_chars=3200, compact=True, metadata_first=True)
    if normalized == "discord":
        return ChannelOutputPolicy(platform=normalized, max_chars=1800, compact=True, metadata_first=True)
    if normalized in {"web", "web-companion", "web_companion"}:
        return ChannelOutputPolicy(platform="web_companion", max_chars=6000, compact=False, metadata_first=True)
    return ChannelOutputPolicy(platform=normalized, max_chars=2200, compact=True, metadata_first=True)


def build_source_context(request: ChannelRequest) -> dict[str, Any]:
    policy = channel_output_policy(request.platform)
    return {
        "surface": request.surface,
        "platform": policy.platform,
        "channel_id": request.channel_id,
        "source_user_id": request.user_id,
        "allow_cold_context": bool(request.allow_cold_context),
        "allow_remote_fetch": bool(request.allow_remote_fetch),
        "external_evidence": list(request.attachments or []),
        "output_policy": {
            "max_chars": policy.max_chars,
            "compact": policy.compact,
            "metadata_first": policy.metadata_first,
        },
    }


def render_channel_response(text: str, *, platform: str) -> tuple[str, bool]:
    policy = channel_output_policy(platform)
    normalized = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(normalized) <= policy.max_chars:
        return normalized, False
    clipped = normalized[: max(0, policy.max_chars - 20)].rstrip()
    return f"{clipped}\n\n[truncated]", True


def process_channel_request(agent: Any, request: ChannelRequest) -> ChannelGatewayResult:
    session_id = channel_session_id(
        platform=request.platform,
        user_id=request.user_id,
        channel_id=request.channel_id,
        persona_id=request.persona_id,
        device_hint=request.device_hint,
    )
    source_context = build_source_context(request)
    result = agent.run_once(
        request.text,
        session_id_override=session_id,
        source_context=source_context,
    )
    response_text, truncated = render_channel_response(result["response"], platform=request.platform)
    return ChannelGatewayResult(
        task_id=str(result["task_id"]),
        platform=channel_output_policy(request.platform).platform,
        session_id=session_id,
        response_text=response_text,
        truncated=truncated,
        mode=str(result.get("mode") or "unknown"),
        confidence=float(result.get("confidence") or 0.0),
        prompt_assembly_report=dict(result.get("prompt_assembly_report") or {}),
        source_context=source_context,
    )
