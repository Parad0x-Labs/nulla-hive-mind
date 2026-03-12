from __future__ import annotations

import importlib


def test_agent_entrypoint_exists() -> None:
    module = importlib.import_module("apps.nulla_agent")
    assert callable(getattr(module, "main", None))


def test_daemon_entrypoint_exists() -> None:
    module = importlib.import_module("apps.nulla_daemon")
    assert callable(getattr(module, "main", None))


def test_meet_entrypoint_exists() -> None:
    module = importlib.import_module("apps.meet_and_greet_server")
    assert callable(getattr(module, "main", None))
