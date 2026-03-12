from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from core.discovery_index import register_peer_endpoint
from network.assist_router import handle_incoming_assist_message
from network.protocol import Protocol, encode_message
from network.signer import get_local_peer_id as local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AssistRouterMeshTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM nonce_cache")
            conn.commit()
        finally:
            conn.close()

    def test_find_block_advertises_registered_local_endpoint(self) -> None:
        peer_id = local_peer_id()
        register_peer_endpoint(peer_id, "198.51.100.10", 49200, source="self")

        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="FIND_BLOCK",
            sender_peer_id=peer_id,
            nonce=uuid.uuid4().hex,
            payload={"block_hash": "a" * 64},
        )

        with patch("core.liquefy_cas.get_chunk", return_value=b"test-bytes"):
            result = handle_incoming_assist_message(raw_bytes=raw, source_addr=None)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.generated_messages), 1)

        response = Protocol.decode_and_validate(result.generated_messages[0])
        self.assertEqual(response["msg_type"], "BLOCK_FOUND")
        peers = (response.get("payload") or {}).get("hosting_peers") or []
        self.assertTrue(peers)
        self.assertEqual(str(peers[0].get("ip")), "198.51.100.10")
        self.assertEqual(int(peers[0].get("port")), 49200)


if __name__ == "__main__":
    unittest.main()
