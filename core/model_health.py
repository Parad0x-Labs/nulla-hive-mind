from __future__ import annotations

import time
from dataclasses import asdict, dataclass


@dataclass
class ProviderHealth:
    provider_id: str
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    timeout_failures: int = 0
    circuit_open_until: float = 0.0
    last_error: str | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None

    @property
    def circuit_open(self) -> bool:
        return self.circuit_open_until > time.time()


_HEALTH: dict[str, ProviderHealth] = {}


def reset_provider_health(provider_id: str | None = None) -> None:
    if provider_id is None:
        _HEALTH.clear()
        return
    _HEALTH.pop(provider_id, None)


def get_provider_health(provider_id: str) -> ProviderHealth:
    if provider_id not in _HEALTH:
        _HEALTH[provider_id] = ProviderHealth(provider_id=provider_id)
    return _HEALTH[provider_id]


def circuit_is_open(provider_id: str) -> bool:
    return get_provider_health(provider_id).circuit_open


def record_provider_success(provider_id: str) -> None:
    state = get_provider_health(provider_id)
    state.total_successes += 1
    state.consecutive_failures = 0
    state.last_success_at = time.time()
    state.last_error = None
    state.circuit_open_until = 0.0


def record_provider_failure(
    provider_id: str,
    *,
    error: str,
    timeout: bool = False,
    failure_threshold: int = 5,
    cooldown_seconds: int = 20,
) -> ProviderHealth:
    state = get_provider_health(provider_id)
    state.total_failures += 1
    state.consecutive_failures += 1
    state.last_failure_at = time.time()
    state.last_error = error
    if timeout:
        state.timeout_failures += 1
    if state.consecutive_failures >= max(1, failure_threshold):
        state.circuit_open_until = time.time() + max(1, cooldown_seconds)
    return state


def provider_health_snapshot(provider_id: str) -> dict[str, object]:
    return asdict(get_provider_health(provider_id)) | {"circuit_open": circuit_is_open(provider_id)}
