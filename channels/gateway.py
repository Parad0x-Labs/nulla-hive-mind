"""Channel integration gateways for Telegram and Discord.

These are protocol stubs that define the interface for channel bots.
Each gateway translates incoming messages from the channel into NULLA task
capsules and returns responses back to the channel.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChannelMessage:
    """Normalized message from any channel."""
    channel_type: str  # "telegram", "discord", "web"
    channel_id: str
    user_id: str
    user_name: str
    text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelResponse:
    """Response to send back to a channel."""
    text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelGateway(ABC):
    """Base class for channel integrations."""

    @abstractmethod
    def channel_type(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_response(self, channel_id: str, response: ChannelResponse) -> bool:
        raise NotImplementedError

    def on_message(self, msg: ChannelMessage) -> ChannelResponse | None:
        """Default message handler — override for custom routing."""
        logger.info("Channel message from %s/%s: %s", msg.channel_type, msg.user_name, msg.text[:80])
        return ChannelResponse(text="I received your message. Let me process that.")


class TelegramGateway(ChannelGateway):
    """Telegram Bot API gateway stub."""

    def __init__(self, bot_token: str = "", webhook_url: str = "") -> None:
        self.bot_token = bot_token
        self.webhook_url = webhook_url
        self._running = False

    def channel_type(self) -> str:
        return "telegram"

    def start(self) -> None:
        if not self.bot_token:
            logger.warning("Telegram gateway: no bot_token configured, running in dry mode")
        self._running = True
        logger.info("Telegram gateway started (webhook=%s)", self.webhook_url or "polling")

    def stop(self) -> None:
        self._running = False
        logger.info("Telegram gateway stopped")

    def send_response(self, channel_id: str, response: ChannelResponse) -> bool:
        if not self.bot_token:
            logger.info("Telegram (dry): would send to %s: %s", channel_id, response.text[:80])
            return True
        # Real implementation would call Telegram Bot API here:
        # POST https://api.telegram.org/bot{token}/sendMessage
        logger.info("Telegram: sent response to channel %s", channel_id)
        return True

    def handle_webhook(self, update: dict[str, Any]) -> ChannelResponse | None:
        """Process a Telegram webhook update."""
        message = update.get("message", {})
        text = message.get("text", "")
        chat = message.get("chat", {})
        user = message.get("from", {})

        msg = ChannelMessage(
            channel_type="telegram",
            channel_id=str(chat.get("id", "")),
            user_id=str(user.get("id", "")),
            user_name=user.get("username", user.get("first_name", "unknown")),
            text=text,
        )
        return self.on_message(msg)


class DiscordGateway(ChannelGateway):
    """Discord Bot gateway stub."""

    def __init__(self, bot_token: str = "") -> None:
        self.bot_token = bot_token
        self._running = False

    def channel_type(self) -> str:
        return "discord"

    def start(self) -> None:
        if not self.bot_token:
            logger.warning("Discord gateway: no bot_token configured, running in dry mode")
        self._running = True
        logger.info("Discord gateway started")

    def stop(self) -> None:
        self._running = False
        logger.info("Discord gateway stopped")

    def send_response(self, channel_id: str, response: ChannelResponse) -> bool:
        if not self.bot_token:
            logger.info("Discord (dry): would send to %s: %s", channel_id, response.text[:80])
            return True
        # Real implementation would use Discord API / websocket
        logger.info("Discord: sent response to channel %s", channel_id)
        return True


# ── Gateway registry ──────────────────────────────────────────────────

_GATEWAYS: dict[str, ChannelGateway] = {}


def register_gateway(gateway: ChannelGateway) -> None:
    _GATEWAYS[gateway.channel_type()] = gateway
    logger.info("Registered channel gateway: %s", gateway.channel_type())


def get_gateway(channel_type: str) -> ChannelGateway | None:
    return _GATEWAYS.get(channel_type)


def start_all_gateways() -> None:
    for gw in _GATEWAYS.values():
        gw.start()


def stop_all_gateways() -> None:
    for gw in _GATEWAYS.values():
        gw.stop()
