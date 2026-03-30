from __future__ import annotations

import urllib.request
from typing import Any

from . import client as public_hive_client
from .bridge_presence import PublicHiveBridgePresenceMixin
from .bridge_topics import PublicHiveBridgeTopicsMixin
from .bridge_transport import _UNSET_SENTINEL, PublicHiveBridgeTransportMixin
from .config import PublicHiveBridgeConfig


class PublicHiveBridge(
    PublicHiveBridgeTransportMixin,
    PublicHiveBridgePresenceMixin,
    PublicHiveBridgeTopicsMixin,
):
    def __init__(
        self,
        config: PublicHiveBridgeConfig | None = None,
        *,
        urlopen: Any | None = None,
    ) -> None:
        self.config = config or _load_public_hive_bridge_config()
        self._urlopen = urlopen or urllib.request.urlopen
        self._nullabook_token: str | None = _UNSET_SENTINEL
        self._preferred_topic_base_url: str | None = None
        self._client = public_hive_client.PublicHiveHttpClient(
            self.config,
            urlopen=self._urlopen,
            nullabook_token_fn=self._get_nullabook_token,
        )


def _load_public_hive_bridge_config() -> PublicHiveBridgeConfig:
    from core.public_hive_bridge import load_public_hive_bridge_config

    return load_public_hive_bridge_config()
