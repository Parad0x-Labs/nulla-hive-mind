from __future__ import annotations

import unittest
from pathlib import Path

from core.meet_and_greet_config_loader import load_meet_node_config


class MeetAndGreetConfigLoaderTests(unittest.TestCase):
    def test_loads_global_3node_seed_config(self) -> None:
        config = load_meet_node_config(
            Path("config/meet_clusters/global_3node/seed-eu-1.json")
        )

        self.assertEqual(config.node_id, "seed-eu-1")
        self.assertEqual(config.region, "eu")
        self.assertEqual(config.service_config.local_region, "eu")
        self.assertTrue(config.replication_config.cross_region_summary_only)
        self.assertTrue(str(config.replication_config.auth_token or "").startswith("replace-with-strong-meet-token"))
        self.assertEqual(len(config.seed_peers), 2)
        self.assertEqual({seed.region for seed in config.seed_peers}, {"us", "apac"})

    def test_loads_do_ip_first_seed_config(self) -> None:
        config = load_meet_node_config(
            Path("config/meet_clusters/do_ip_first_4node/seed-eu-1.json")
        )
        self.assertEqual(config.node_id, "seed-eu-1")
        self.assertEqual(config.region, "eu")
        self.assertEqual(config.bind_port, 8766)
        self.assertEqual(config.service_config.local_region, "eu")
        self.assertTrue(str(config.replication_config.auth_token or "").startswith("set-strong-meet-token"))
        self.assertEqual(len(config.seed_peers), 2)
        self.assertEqual({seed.region for seed in config.seed_peers}, {"us", "apac"})


if __name__ == "__main__":
    unittest.main()
