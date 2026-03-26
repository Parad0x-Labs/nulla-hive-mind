from __future__ import annotations

import sqlite3

from core.discovery_index import (
    delivery_endpoints_for_peer,
    record_signed_peer_endpoint_observation,
    register_peer_endpoint,
    register_peer_endpoint_candidate,
    verified_endpoints_for_peer,
)
from network.protocol import Protocol, encode_message
from network.signer import get_local_peer_id as local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


def _clear_endpoint_tables() -> None:
    conn = get_connection()
    try:
        for table in ("peer_endpoints", "peer_endpoint_observations", "peer_endpoint_candidates"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def _signed_envelope(*, msg_type: str = "PING") -> dict[str, object]:
    raw = encode_message(
        msg_id="msg-" + msg_type.lower(),
        msg_type=msg_type,
        sender_peer_id=local_peer_id(),
        nonce="nonce-" + msg_type.lower(),
        payload={},
    )
    return Protocol.decode_and_validate(raw)


def test_run_migrations_rebuilds_legacy_peer_endpoints_into_multi_endpoint_rows(tmp_path) -> None:
    db_path = tmp_path / "mesh-endpoints.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE peer_endpoints (
                peer_id TEXT PRIMARY KEY,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'direct',
                last_seen_at TEXT NOT NULL,
                last_verified_at TEXT NOT NULL DEFAULT '',
                verification_kind TEXT NOT NULL DEFAULT '',
                proof_count INTEGER NOT NULL DEFAULT 0,
                proof_message_id TEXT NOT NULL DEFAULT '',
                proof_message_type TEXT NOT NULL DEFAULT '',
                proof_hash TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE peer_endpoint_observations (
                peer_id TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                source TEXT NOT NULL DEFAULT 'observed',
                verification_kind TEXT NOT NULL DEFAULT 'protocol_signature',
                proof_message_id TEXT NOT NULL DEFAULT '',
                proof_message_type TEXT NOT NULL DEFAULT '',
                proof_hash TEXT NOT NULL DEFAULT '',
                proof_signature TEXT NOT NULL DEFAULT '',
                proof_timestamp TEXT NOT NULL DEFAULT '',
                first_verified_at TEXT NOT NULL,
                last_verified_at TEXT NOT NULL,
                proof_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (peer_id, host, port, source)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO peer_endpoints (
                peer_id, host, port, source, last_seen_at, last_verified_at,
                verification_kind, proof_count, proof_message_id, proof_message_type, proof_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "peer-1",
                "198.51.100.10",
                49110,
                "bootstrap",
                "2026-03-26T08:00:00+00:00",
                "",
                "",
                0,
                "",
                "",
                "",
                "2026-03-26T08:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO peer_endpoint_observations (
                peer_id, host, port, source, verification_kind,
                proof_message_id, proof_message_type, proof_hash, proof_signature, proof_timestamp,
                first_verified_at, last_verified_at, proof_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "peer-1",
                "198.51.100.11",
                49111,
                "observed",
                "protocol_signature",
                "msg-1",
                "PING",
                "hash-1",
                "sig-1",
                "2026-03-26T09:00:00+00:00",
                "2026-03-26T09:00:00+00:00",
                "2026-03-26T09:00:00+00:00",
                1,
                "2026-03-26T09:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    run_migrations(db_path=db_path)
    migrated = get_connection(db_path)
    try:
        pk_columns = [
            row["name"]
            for row in migrated.execute("PRAGMA table_info(peer_endpoints)").fetchall()
            if int(row["pk"] or 0)
        ]
        rows = migrated.execute(
            "SELECT peer_id, host, port, source FROM peer_endpoints WHERE peer_id = ? ORDER BY host ASC",
            ("peer-1",),
        ).fetchall()
    finally:
        migrated.close()

    assert pk_columns == ["peer_id", "host", "port"]
    assert [(row["host"], row["port"], row["source"]) for row in rows] == [
        ("198.51.100.10", 49110, "bootstrap"),
        ("198.51.100.11", 49111, "observed"),
    ]


def test_verified_endpoints_keep_multiple_rows_per_peer_and_sort_by_strength() -> None:
    run_migrations()
    _clear_endpoint_tables()
    peer_id = "peer-multi-verified"
    register_peer_endpoint(peer_id, "198.51.100.20", 49120, source="bootstrap")
    register_peer_endpoint(peer_id, "198.51.100.21", 49121, source="self")

    endpoints = verified_endpoints_for_peer(peer_id, limit=4)

    assert [(item.host, item.port, item.source) for item in endpoints] == [
        ("198.51.100.21", 49121, "self"),
        ("198.51.100.20", 49120, "bootstrap"),
    ]


def test_signed_observation_upgrades_existing_endpoint_without_duplicate_rows() -> None:
    run_migrations()
    _clear_endpoint_tables()
    peer_id = local_peer_id()
    register_peer_endpoint(peer_id, "198.51.100.30", 49130, source="bootstrap")

    record_signed_peer_endpoint_observation(
        peer_id,
        "198.51.100.30",
        49130,
        envelope=_signed_envelope(msg_type="HEARTBEAT"),
    )

    endpoints = verified_endpoints_for_peer(peer_id, limit=4)
    assert len(endpoints) == 1
    assert (endpoints[0].host, endpoints[0].port, endpoints[0].source) == ("198.51.100.30", 49130, "observed")
    assert endpoints[0].verification_kind == "protocol_signature"
    assert endpoints[0].proof_count == 1


def test_delivery_endpoints_prefer_verified_rows_and_append_candidates() -> None:
    run_migrations()
    _clear_endpoint_tables()
    peer_id = "peer-delivery"
    register_peer_endpoint(peer_id, "198.51.100.40", 49140, source="bootstrap")
    register_peer_endpoint(peer_id, "198.51.100.41", 49141, source="observed")
    register_peer_endpoint_candidate(peer_id, "198.51.100.42", 49142, source="dht")

    endpoints = delivery_endpoints_for_peer(peer_id, verified_limit=4, include_candidates=True, candidate_limit=2)

    assert endpoints == [
        ("198.51.100.41", 49141),
        ("198.51.100.40", 49140),
        ("198.51.100.42", 49142),
    ]
