from __future__ import annotations

import ssl
from typing import Any

_UNSET_SENTINEL = object()


class PublicHiveBridgeTransportMixin:
    def _get_nullabook_token(self) -> str | None:
        if self._nullabook_token is _UNSET_SENTINEL:
            try:
                from core.nullabook_identity import load_local_token

                self._nullabook_token = load_local_token()
            except Exception:
                self._nullabook_token = None
        return self._nullabook_token

    def enabled(self) -> bool:
        return bool(self.config.enabled and self.config.meet_seed_urls)

    def auth_configured(self) -> bool:
        from core.public_hive_bridge import public_hive_has_auth

        return public_hive_has_auth(self.config)

    def write_enabled(self) -> bool:
        from core.public_hive_bridge import public_hive_write_enabled

        return public_hive_write_enabled(self.config)

    def _post_many(
        self,
        route: str,
        *,
        payload: dict[str, Any],
        base_urls: tuple[str, ...],
    ) -> dict[str, Any]:
        return self._client.post_many(route, payload=payload, base_urls=base_urls)

    def _get_json(self, base_url: str, route: str) -> Any:
        return self._client.get_json(base_url, route)

    def _post_json(self, base_url: str, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._client.post_json(base_url, route, payload)

    def _auth_token_for_url(self, url: str) -> str | None:
        return self._client.auth_token_for_url(url)

    def _write_grant_for_request(self, base_url: str, route: str) -> dict[str, Any] | None:
        return self._client.write_grant_for_request(base_url, route)

    def _ssl_context_for_url(self, url: str) -> ssl.SSLContext | None:
        return self._client.ssl_context_for_url(url)
