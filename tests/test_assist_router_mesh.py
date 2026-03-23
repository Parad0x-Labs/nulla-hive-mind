from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from core.credit_ledger import get_credit_balance
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
            conn.execute("DELETE FROM compute_credit_ledger")
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

    def test_credit_transfer_uses_live_signer_identity_lookup(self) -> None:
        actual_sender = local_peer_id()
        patched_buyer = "buyer-peer-1234567890abcdef"
        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="CREDIT_TRANSFER",
            sender_peer_id=actual_sender,
            nonce=uuid.uuid4().hex,
            payload={
                "transfer_id": str(uuid.uuid4()),
                "seller_peer_id": "seller-peer-abcdef1234567890",
                "buyer_peer_id": patched_buyer,
                "credits_transferred": 300,
                "on_chain_tx_hash": "sol_usdc_tx_test_hash_1234567890abcd",
                "timestamp": _now_iso(),
            },
        )

        with patch("network.signer.get_local_peer_id", return_value=patched_buyer):
            result = handle_incoming_assist_message(raw_bytes=raw, source_addr=None)

        self.assertTrue(result.ok)
        self.assertIn("Received 300 purchased credits", result.reason)
        self.assertEqual(get_credit_balance(patched_buyer), 300.0)


if __name__ == "__main__":
    unittest.main()
