from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import request, error
from urllib.parse import urlencode

from core.channel_gateway import ChannelRequest, process_channel_request
from relay.channel_outbound import TOPIC_BY_PLATFORM

class DiscordBridge:
    def __init__(self):
        self.webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        self.webhook_urls = self._load_webhook_map()
        self.bot_token = os.environ.get("DISCORD_BOT_TOKEN")
        self.bot_user_id = os.environ.get("DISCORD_BOT_USER_ID")
        self.default_channel_id = os.environ.get("DISCORD_CHANNEL_ID")
        self.channel_ids = self._load_channel_map()
        self.mirror_url = os.environ.get("NULLA_MIRROR_URL", "http://127.0.0.1:8787").rstrip("/")
        self.sync_topic = os.environ.get("NULLA_DISCORD_OUTBOUND_TOPIC", TOPIC_BY_PLATFORM["discord"])
        self._last_seen_message_ids: dict[str, int] = {}
        self._agent = None

    def is_configured(self) -> bool:
        outbound_ready = bool(self.webhook_url or self.webhook_urls or (self.bot_token and (self.default_channel_id or self.channel_ids)))
        inbound_ready = bool(self.bot_token and (self.default_channel_id or self.channel_ids))
        return outbound_ready or inbound_ready

    def _load_webhook_map(self) -> dict[str, str]:
        raw = str(os.environ.get("DISCORD_WEBHOOK_URLS_JSON", "")).strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in payload.items():
            name = str(key or "").strip().lower()
            url = str(value or "").strip()
            if name and url:
                out[name] = url
        return out

    def _load_channel_map(self) -> dict[str, str]:
        raw = str(os.environ.get("DISCORD_CHANNEL_IDS_JSON", "")).strip()
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        out: dict[str, str] = {}
        for key, value in payload.items():
            name = str(key or "").strip().lower()
            channel_id = str(value or "").strip()
            if name and channel_id:
                out[name] = channel_id
        return out

    def _resolve_webhook_url(self, target: str | None) -> str | None:
        alias = str(target or "default").strip().lower() or "default"
        return self.webhook_urls.get(alias) or self.webhook_url

    def _resolve_channel_id(self, target: str | None) -> str | None:
        alias = str(target or "default").strip().lower() or "default"
        return self.channel_ids.get(alias) or self.default_channel_id

    def _discord_webhook_post(self, content: str, *, webhook_url: str | None = None) -> bool:
        target_url = str(webhook_url or "").strip() or self.webhook_url
        if not target_url:
            return False
        req = request.Request(target_url, method="POST")
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', 'NullaDiscordBridge/1.0')
        payload = {"content": content}
        
        try:
            body = json.dumps(payload).encode('utf-8')
            with request.urlopen(req, data=body, timeout=10) as response:
                return response.status in (200, 204)
        except error.URLError as e:
            print(f"[DiscordBridge] Error posting to Discord webhook: {e}")
            return False

    def _discord_api_request(self, path: str, *, method: str = "GET", data: dict | None = None) -> Any:
        if not self.bot_token:
            return {}
        url = f"https://discord.com/api/v10{path}"
        req = request.Request(url, method=method)
        req.add_header("Authorization", f"Bot {self.bot_token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "NullaDiscordBridge/1.0")
        try:
            body = json.dumps(data).encode("utf-8") if data else None
            with request.urlopen(req, data=body, timeout=10) as response:
                raw = response.read().decode("utf-8") if response.length != 0 else ""
                if not raw:
                    return {}
                return json.loads(raw)
        except error.URLError as e:
            print(f"[DiscordBridge] Error calling Discord API {path}: {e}")
            return {}

    def _post_bot_message(self, channel_id: str, content: str) -> bool:
        if not self.bot_token or not channel_id:
            return False
        payload = {"content": content}
        resp = self._discord_api_request(f"/channels/{channel_id}/messages", method="POST", data=payload)
        return bool(isinstance(resp, dict) and resp.get("id"))

    def _fetch_channel_messages(self, channel_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if not channel_id:
            return []
        query = urlencode({"limit": max(1, min(100, int(limit)))})
        payload = self._discord_api_request(f"/channels/{channel_id}/messages?{query}", method="GET")
        return payload if isinstance(payload, list) else []

    def _resolve_bot_user_id(self) -> str | None:
        if self.bot_user_id:
            return self.bot_user_id
        payload = self._discord_api_request("/users/@me", method="GET")
        bot_id = str((payload or {}).get("id") or "").strip()
        if bot_id:
            self.bot_user_id = bot_id
        return self.bot_user_id

    def _ensure_agent(self):
        if self._agent is not None:
            return self._agent
        from apps.nulla_agent import NullaAgent

        backend = os.environ.get("NULLA_CHANNEL_AGENT_BACKEND", "auto")
        device = os.environ.get("NULLA_CHANNEL_AGENT_DEVICE", "discord-bridge")
        persona_id = os.environ.get("NULLA_CHANNEL_PERSONA_ID", "default")
        agent = NullaAgent(backend_name=backend, device=device, persona_id=persona_id)
        agent.start()
        self._agent = agent
        return agent

    def _extract_command_text(self, content: str) -> str | None:
        text = str(content or "").strip()
        if not text:
            return None
        lowered = text.lower()
        if lowered.startswith("!nulla "):
            return text[7:].strip()
        if lowered.startswith("nulla:"):
            return text[6:].strip()
        if lowered.startswith("nulla,"):
            return text[6:].strip()
        bot_id = self._resolve_bot_user_id()
        if bot_id:
            mention_prefixes = (f"<@{bot_id}>", f"<@!{bot_id}>")
            for prefix in mention_prefixes:
                if text.startswith(prefix):
                    return text[len(prefix):].strip()
        return None

    def _mirror_request(self, path: str, method: str = "GET", data: dict = None) -> dict:
        req = request.Request(f"{self.mirror_url}{path}", method=method)
        req.add_header('Content-Type', 'application/json')
        try:
            body = json.dumps(data).encode('utf-8') if data else None
            with request.urlopen(req, data=body, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except error.URLError as e:
            print(f"[DiscordBridge] Error calling mirror {path}: {e}")
            return {}

    def fetch_mirror_and_push_to_discord(self):
        """Poll the outbound queue and deliver pending Discord posts."""
        snapshot = self._mirror_request(f"/topics/{self.sync_topic}")
        records = snapshot.get("records")
        if not isinstance(records, list) or not records:
            return

        changed = False
        for raw_record in records:
            if not isinstance(raw_record, dict):
                continue
            record = dict(raw_record)
            if str(record.get("kind") or "") != "outbound_post":
                continue
            if str(record.get("platform") or "").lower() != "discord":
                continue
            if str(record.get("delivery_status") or "pending").lower() == "delivered":
                continue

            webhook_url = self._resolve_webhook_url(str(record.get("target") or "default"))
            attempts = int(record.get("delivery_attempts") or 0) + 1
            record["delivery_attempts"] = attempts
            record["last_attempted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            content = str(record.get("content") or "").strip()
            if not content:
                record["last_error"] = "missing_content"
                changed = True
                raw_record.update(record)
                continue

            if not webhook_url:
                channel_id = self._resolve_channel_id(str(record.get("target") or "default"))
            else:
                channel_id = None
            if not webhook_url and not channel_id:
                record["last_error"] = "missing_delivery_target"
                changed = True
                raw_record.update(record)
                continue

            delivered = False
            if webhook_url:
                delivered = self._discord_webhook_post(content, webhook_url=webhook_url)
            elif channel_id:
                delivered = self._post_bot_message(channel_id, content)

            if delivered:
                record["delivery_status"] = "delivered"
                record["delivered_at"] = record["last_attempted_at"]
                record["last_error"] = None
            else:
                record["delivery_status"] = "pending"
                record["last_error"] = "discord_delivery_failed"
            changed = True
            raw_record.update(record)

        if changed:
            snapshot["record_count"] = len(records)
            self._mirror_request(f"/publish/{self.sync_topic}", method="POST", data=snapshot)

    def fetch_discord_and_push_to_mirror(self):
        """Poll configured Discord channels for directed commands and route them into NULLA."""
        inbound_channel_ids = {str(v).strip() for v in self.channel_ids.values() if str(v).strip()}
        if self.default_channel_id:
            inbound_channel_ids.add(str(self.default_channel_id).strip())
        if not self.bot_token or not inbound_channel_ids:
            return

        agent = self._ensure_agent()
        bot_user_id = self._resolve_bot_user_id()
        for channel_id in sorted(inbound_channel_ids):
            messages = self._fetch_channel_messages(channel_id, limit=20)
            if not messages:
                continue

            newest_seen = max(int(str(msg.get("id") or "0")) for msg in messages if str(msg.get("id") or "0").isdigit())
            if channel_id not in self._last_seen_message_ids:
                self._last_seen_message_ids[channel_id] = newest_seen
                continue

            last_seen = self._last_seen_message_ids.get(channel_id, 0)
            pending = [
                msg
                for msg in reversed(messages)
                if str(msg.get("id") or "0").isdigit() and int(str(msg.get("id"))) > last_seen
            ]
            if newest_seen > last_seen:
                self._last_seen_message_ids[channel_id] = newest_seen

            for message in pending:
                author = message.get("author") or {}
                author_id = str(author.get("id") or "").strip()
                if not author_id:
                    continue
                if author.get("bot") or (bot_user_id and author_id == bot_user_id):
                    continue

                command_text = self._extract_command_text(str(message.get("content") or ""))
                if not command_text:
                    continue

                result = process_channel_request(
                    agent,
                    ChannelRequest(
                        platform="discord",
                        user_id=author_id,
                        channel_id=channel_id,
                        text=command_text,
                        device_hint="channel",
                        surface="discord_bot",
                    ),
                )
                reply = f"<@{author_id}> {result.response_text}".strip()
                self._post_bot_message(channel_id, reply)

    def run_forever(self):
        if not self.is_configured():
            print("[DiscordBridge] Missing Discord webhook or bot configuration. Exiting.")
            return

        print(f"[DiscordBridge] Started syncing between {self.mirror_url} and Discord.")
        while True:
            self.fetch_discord_and_push_to_mirror()
            self.fetch_mirror_and_push_to_discord()
            time.sleep(5)


if __name__ == "__main__":
    DiscordBridge().run_forever()
