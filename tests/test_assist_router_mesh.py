from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from core.credit_ledger import get_credit_balance
from core.discovery_index import endpoint_for_peer, register_peer_endpoint
from network.assist_router import handle_incoming_assist_message
from network.dht import RoutingTable
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
            conn.execute("DELETE FROM peer_endpoints")
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

    def test_node_found_does_not_override_observed_endpoint_with_weaker_signed_endpoint(self) -> None:
        target_peer = "b" * 64
        register_peer_endpoint(target_peer, "203.0.113.10", 49001, source="observed")

        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="NODE_FOUND",
            sender_peer_id=local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={
                "target_id": "a" * 64,
                "nodes": [
                    {
                        "peer_id": target_peer,
                        "ip": "198.51.100.99",
                        "port": 49999,
                    }
                ],
            },
        )

        result = handle_incoming_assist_message(raw_bytes=raw, source_addr=("198.51.100.40", 49222))

        self.assertTrue(result.ok)
        self.assertEqual(endpoint_for_peer(target_peer), ("203.0.113.10", 49001))

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT source FROM peer_endpoints WHERE peer_id = ? LIMIT 1",
                (target_peer,),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["source"], "observed")

    def test_block_found_does_not_override_observed_endpoint_with_weaker_signed_endpoint(self) -> None:
        target_peer = "c" * 64
        register_peer_endpoint(target_peer, "203.0.113.10", 49001, source="observed")

        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="BLOCK_FOUND",
            sender_peer_id=local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={
                "block_hash": "d" * 64,
                "hosting_peers": [
                    {
                        "peer_id": target_peer,
                        "ip": "198.51.100.88",
                        "port": 49998,
                    }
                ],
            },
        )

        result = handle_incoming_assist_message(raw_bytes=raw, source_addr=("198.51.100.40", 49222))

        self.assertTrue(result.ok)
        self.assertEqual(endpoint_for_peer(target_peer), ("203.0.113.10", 49001))

    def test_find_node_reply_excludes_unverified_referrals(self) -> None:
        table = RoutingTable(local_peer_id=local_peer_id(), k_bucket_size=20, bucket_count=64)
        referral_peer = "0" * 63 + "1"
        observed_peer = "f" * 64
        table.add_node(referral_peer, "198.51.100.99", 49999, source="dht")
        table.add_node(observed_peer, "203.0.113.10", 49001, source="observed")

        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="FIND_NODE",
            sender_peer_id=local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={"target_id": "0" * 64},
        )

        with patch("network.dht.get_routing_table", return_value=table):
            result = handle_incoming_assist_message(raw_bytes=raw, source_addr=None)

        self.assertTrue(result.ok)
        response = Protocol.decode_and_validate(result.generated_messages[0])
        nodes = (response.get("payload") or {}).get("nodes") or []
        self.assertEqual([item["peer_id"] for item in nodes], [observed_peer])

    def test_find_block_reply_excludes_unverified_referrals_when_block_missing(self) -> None:
        table = RoutingTable(local_peer_id=local_peer_id(), k_bucket_size=20, bucket_count=64)
        referral_peer = "0" * 63 + "1"
        observed_peer = "f" * 64
        table.add_node(referral_peer, "198.51.100.99", 49999, source="dht")
        table.add_node(observed_peer, "203.0.113.10", 49001, source="observed")

        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="FIND_BLOCK",
            sender_peer_id=local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={"block_hash": "d" * 64},
        )

        with patch("network.dht.get_routing_table", return_value=table), patch(
            "core.liquefy_cas.get_chunk", return_value=None
        ):
            result = handle_incoming_assist_message(raw_bytes=raw, source_addr=None)

        self.assertTrue(result.ok)
        response = Protocol.decode_and_validate(result.generated_messages[0])
        peers = (response.get("payload") or {}).get("hosting_peers") or []
        self.assertEqual([item["peer_id"] for item in peers], [observed_peer])


if __name__ == "__main__":
    unittest.main()
