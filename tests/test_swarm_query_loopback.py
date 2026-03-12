from __future__ import annotations

from retrieval.swarm_query import broadcast_task_offer


class _FakeOrderBook:
    def __init__(self) -> None:
        self.pushed: list[tuple[bytes, tuple[str, int], dict]] = []

    def push(self, raw_bytes: bytes, source_addr: tuple[str, int], offer_dict: dict) -> None:
        self.pushed.append((raw_bytes, source_addr, offer_dict))


def test_broadcast_task_offer_enqueues_local_loopback_when_no_helpers(monkeypatch) -> None:
    fake_book = _FakeOrderBook()
    monkeypatch.setattr("retrieval.swarm_query.get_best_helpers", lambda **kwargs: [])
    monkeypatch.setattr("retrieval.swarm_query.endpoint_for_peer", lambda peer_id: ("127.0.0.1", 49152))
    monkeypatch.setattr("retrieval.swarm_query.send_message", lambda *args, **kwargs: False)
    monkeypatch.setattr("retrieval.swarm_query.audit_logger.log", lambda *args, **kwargs: None)
    monkeypatch.setattr("retrieval.swarm_query.policy_engine.get", lambda path, default=None: True if path == "orchestration.local_loopback_offer_on_no_helpers" else default)
    monkeypatch.setattr("core.order_book.global_order_book", fake_book)

    sent = broadcast_task_offer(
        offer_payload={
            "task_id": "task-loopback-1",
            "reward_hint": {"points": 5},
        },
        required_capabilities=["research"],
        limit=3,
    )

    assert sent == 1
    assert len(fake_book.pushed) == 1
    assert fake_book.pushed[0][1] == ("127.0.0.1", 49152)
