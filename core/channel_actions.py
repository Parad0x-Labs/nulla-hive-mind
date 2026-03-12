from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from relay.channel_outbound import append_outbound_post
from relay.bridge_workers.discord_bridge import DiscordBridge
from relay.bridge_workers.telegram_bridge import TelegramBridge


_VERB_RE = re.compile(r"\b(post|send|publish|announce|share)\b", re.IGNORECASE)
_QUOTED_RE = re.compile(r"['\"]([^'\"]{1,4000})['\"]")
_TARGET_RE = re.compile(r"#([A-Za-z0-9_\-]{1,64})")


@dataclass(frozen=True)
class ChannelPostIntent:
    platform: str
    message: str
    target: str = "default"


@dataclass(frozen=True)
class ChannelPostDispatchResult:
    ok: bool
    status: str
    platform: str
    target: str
    record_id: str | None
    response_text: str
    error: str | None = None


def parse_channel_post_intent(text: str) -> tuple[ChannelPostIntent | None, str | None]:
    raw = str(text or "").strip()
    if not raw or not _VERB_RE.search(raw):
        return None, None
    if raw.endswith("?") or re.match(r"^(how|what|can|could|would|should|why|when|is|are)\b", raw, flags=re.IGNORECASE):
        return None, None

    lowered = raw.lower()
    if "discord" in lowered or re.search(r"\bdc\b", lowered):
        platform = "discord"
    elif "telegram" in lowered or re.search(r"\btg\b", lowered):
        platform = "telegram"
    else:
        return None, None

    target_match = _TARGET_RE.search(raw)
    target = target_match.group(1) if target_match else "default"

    quoted = _QUOTED_RE.search(raw)
    if quoted:
        message = quoted.group(1).strip()
    elif ":" in raw:
        message = raw.split(":", 1)[1].strip()
    else:
        marker_patterns = [
            r"\b(?:saying|says|message|with)\b\s+(.+)$",
            r"\b(?:to|into)\b\s+(?:discord|dc|telegram|tg)\b\s+(.+)$",
        ]
        message = ""
        for pattern in marker_patterns:
            match = re.search(pattern, raw, flags=re.IGNORECASE)
            if match:
                message = match.group(1).strip()
                break

    if not message:
        example = 'post to Discord: "We are live tonight."'
        return None, f"missing_message_text:{example}"

    return ChannelPostIntent(platform=platform, message=message, target=target), None


def _try_direct_delivery(intent: ChannelPostIntent) -> tuple[bool, str | None]:
    if intent.platform == "discord":
        bridge = DiscordBridge()
        webhook_url = bridge._resolve_webhook_url(intent.target)
        if webhook_url:
            ok = bridge._discord_webhook_post(intent.message, webhook_url=webhook_url)
            return ok, None if ok else "discord_webhook_post_failed"
        channel_id = bridge._resolve_channel_id(intent.target)
        if channel_id and bridge.bot_token:
            ok = bridge._post_bot_message(channel_id, intent.message)
            return ok, None if ok else "discord_bot_post_failed"
        return False, "missing_webhook_or_channel_id"

    if intent.platform == "telegram":
        bridge = TelegramBridge()
        chat_id = bridge._resolve_chat_id(intent.target)
        if not bridge.bot_token:
            return False, "missing_bot_token"
        if not chat_id:
            return False, "missing_chat_id"
        result = bridge._tg_request("sendMessage", {"chat_id": chat_id, "text": intent.message})
        return bool(result.get("ok")), None if result.get("ok") else "telegram_send_failed"

    return False, "unsupported_platform"


def dispatch_outbound_post_intent(
    intent: ChannelPostIntent,
    *,
    task_id: str,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> ChannelPostDispatchResult:
    try:
        ok, record = append_outbound_post(
            platform=intent.platform,
            content=intent.message,
            task_id=task_id,
            session_id=session_id,
            source_context=source_context,
            target=intent.target,
        )
    except Exception as exc:
        return ChannelPostDispatchResult(
            ok=False,
            status="failed",
            platform=intent.platform,
            target=intent.target,
            record_id=None,
            response_text=f"I couldn't queue that {intent.platform} post: {exc}",
            error=str(exc),
        )

    if not ok:
        delivered, delivery_error = _try_direct_delivery(intent)
        if delivered:
            return ChannelPostDispatchResult(
                ok=True,
                status="delivered_direct",
                platform=intent.platform,
                target=intent.target,
                record_id=str(record.get("record_id") or ""),
                response_text=(
                    f"Posted directly to {intent.platform} target `{intent.target}`. "
                    f"The relay mirror was unavailable, so I used the configured platform credentials directly."
                ),
                error=None,
            )
        return ChannelPostDispatchResult(
            ok=False,
            status="failed",
            platform=intent.platform,
            target=intent.target,
            record_id=str(record.get("record_id") or ""),
            response_text=(
                f"I parsed the {intent.platform} post, but the outbound relay mirror was unreachable "
                f"and direct delivery failed ({delivery_error or 'unknown_error'}). "
                f"Start the mirror and bridge, or configure direct platform credentials, then retry."
            ),
            error=delivery_error or "mirror_unreachable",
        )

    return ChannelPostDispatchResult(
        ok=True,
        status="queued",
        platform=intent.platform,
        target=intent.target,
        record_id=str(record.get("record_id") or ""),
        response_text=(
            f"Queued {intent.platform} post for `{intent.target}`. "
            f"Record `{record.get('record_id')}` will be delivered by the bridge."
        ),
        error=None,
    )
