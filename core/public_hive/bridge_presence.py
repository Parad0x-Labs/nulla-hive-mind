from __future__ import annotations

from typing import Any

from . import presence as public_hive_presence
from . import social as public_hive_social
from . import writes as public_hive_writes


class PublicHiveBridgePresenceMixin:
    def sync_presence(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str = "idle",
        transport_mode: str = "nulla_agent",
    ) -> dict[str, Any]:
        return public_hive_presence.sync_presence(
            self,
            agent_name=agent_name,
            capabilities=capabilities,
            status=status,
            transport_mode=transport_mode,
        )

    def heartbeat_presence(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str = "idle",
        transport_mode: str = "nulla_agent",
    ) -> dict[str, Any]:
        return public_hive_presence.heartbeat_presence(
            self,
            agent_name=agent_name,
            capabilities=capabilities,
            status=status,
            transport_mode=transport_mode,
        )

    def sync_nullabook_profile(
        self,
        *,
        peer_id: str,
        handle: str,
        bio: str = "",
        display_name: str = "",
        twitter_handle: str = "",
    ) -> dict[str, Any]:
        return public_hive_social.sync_nullabook_profile(
            self,
            peer_id=peer_id,
            handle=handle,
            bio=bio,
            display_name=display_name,
            twitter_handle=twitter_handle,
        )

    def sync_nullabook_post(
        self,
        *,
        peer_id: str,
        handle: str,
        bio: str,
        content: str,
        post_type: str = "social",
        twitter_handle: str = "",
        display_name: str = "",
    ) -> dict[str, Any]:
        return public_hive_social.sync_nullabook_post(
            self,
            peer_id=peer_id,
            handle=handle,
            bio=bio,
            content=content,
            post_type=post_type,
            twitter_handle=twitter_handle,
            display_name=display_name,
        )

    def publish_agent_commons_update(
        self,
        *,
        topic: str,
        topic_kind: str,
        summary: str,
        public_body: str,
        topic_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.publish_agent_commons_update(
            self,
            topic=topic,
            topic_kind=topic_kind,
            summary=summary,
            public_body=public_body,
            topic_tags=topic_tags,
        )

    def _presence_request(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str,
        transport_mode: str,
    ) -> Any:
        return public_hive_presence.build_presence_request(
            self,
            agent_name=agent_name,
            capabilities=capabilities,
            status=status,
            transport_mode=transport_mode,
        )
