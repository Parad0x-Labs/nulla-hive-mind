from __future__ import annotations

from storage.db import get_connection, reset_default_connection


def test_reset_default_connection_drops_cached_default_connection() -> None:
    first = get_connection()
    second = get_connection()
    assert first is second

    reset_default_connection()

    third = get_connection()
    try:
        assert third is not first
    finally:
        third.close()
        reset_default_connection()
