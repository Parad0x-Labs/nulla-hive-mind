from __future__ import annotations

from core.local_worker_pool import recommend_local_worker_capacity, resolve_local_worker_capacity


def test_recommend_local_worker_capacity_respects_hard_cap(monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 64)
    value = recommend_local_worker_capacity(hard_cap=10)
    assert 1 <= value <= 10


def test_resolve_local_worker_capacity_uses_override() -> None:
    effective, recommended = resolve_local_worker_capacity(requested=12, hard_cap=10)
    assert effective == 12
    assert 1 <= recommended <= 10


def test_resolve_local_worker_capacity_auto_path() -> None:
    effective, recommended = resolve_local_worker_capacity(requested=None, hard_cap=10)
    assert effective == recommended
    assert 1 <= effective <= 10


def test_recommend_local_worker_capacity_respects_vram_limit(monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 32)
    monkeypatch.setattr("core.local_worker_pool._detect_available_memory_gb", lambda: 64.0)
    monkeypatch.setattr("core.local_worker_pool._detect_available_vram_gb", lambda: 3.9)
    value = recommend_local_worker_capacity(hard_cap=10)
    assert value == 1
