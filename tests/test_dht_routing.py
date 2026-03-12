from __future__ import annotations

import unittest

from network.dht import RoutingTable


class DhtRoutingTests(unittest.TestCase):
    def test_kbucket_size_is_enforced(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=3, bucket_count=16)
        for i in range(20):
            peer_id = f"{i + 1:064x}"
            table.add_node(peer_id, f"198.51.100.{(i % 200) + 1}", 49000 + i)
        self.assertLessEqual(len(table.get_all_nodes()), 3 * 16)

    def test_find_closest_prefers_xor_distance(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        near = "0" * 63 + "1"
        mid = "0" * 62 + "10"
        far = "f" * 64
        table.add_node(near, "203.0.113.10", 49001)
        table.add_node(mid, "203.0.113.11", 49002)
        table.add_node(far, "203.0.113.12", 49003)

        out = table.find_closest_peers("0" * 64, count=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].peer_id, near)
        self.assertIn(out[1].peer_id, {mid, far})

    def test_prune_stale_nodes(self) -> None:
        table = RoutingTable(local_peer_id="0" * 64, k_bucket_size=20, bucket_count=64)
        stale_peer = "a" * 64
        fresh_peer = "b" * 64
        table.add_node(stale_peer, "198.51.100.10", 49010)
        table.add_node(fresh_peer, "198.51.100.11", 49011)
        table.nodes[stale_peer].last_seen = 0.0

        removed = table.prune_stale_nodes(max_age_seconds=10.0)
        self.assertEqual(removed, 1)
        self.assertNotIn(stale_peer, {n.peer_id for n in table.get_all_nodes()})
        self.assertIn(fresh_peer, {n.peer_id for n in table.get_all_nodes()})


if __name__ == "__main__":
    unittest.main()

