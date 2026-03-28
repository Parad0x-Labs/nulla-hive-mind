from __future__ import annotations

from pathlib import Path
from unittest import mock

from storage.db import active_default_db_path, configure_default_db_path, get_connection, reset_default_connection


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


def test_active_default_db_path_follows_active_runtime_home_when_unconfigured(tmp_path: Path) -> None:
    runtime_data_dir = (tmp_path / "receipt-runtime" / "data").resolve()

    configure_default_db_path(None)
    reset_default_connection()
    try:
        with mock.patch("storage.db.active_data_dir", return_value=runtime_data_dir):
            assert active_default_db_path() == str((runtime_data_dir / "nulla_web0_v2.db").resolve())
    finally:
        reset_default_connection()


def test_get_connection_uses_runtime_bound_default_db_path(tmp_path: Path) -> None:
    runtime_data_dir = (tmp_path / "receipt-runtime" / "data").resolve()
    expected_db = (runtime_data_dir / "nulla_web0_v2.db").resolve()

    configure_default_db_path(None)
    reset_default_connection()
    try:
        with mock.patch("storage.db.active_data_dir", return_value=runtime_data_dir):
            conn = get_connection()
            try:
                conn.execute("CREATE TABLE IF NOT EXISTS storage_db_pooling_probe (id INTEGER PRIMARY KEY)")
                conn.commit()
            finally:
                conn.close()
            assert expected_db.exists()
            assert active_default_db_path() == str(expected_db)
    finally:
        reset_default_connection()
