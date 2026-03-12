from __future__ import annotations

import unittest
from pathlib import Path

from core.brain_hive_watch_config_loader import load_brain_hive_watch_config


class BrainHiveWatchConfigLoaderTests(unittest.TestCase):
    def test_loads_separated_watch_config(self) -> None:
        config = load_brain_hive_watch_config(
            Path("config/meet_clusters/separated_watch_4node/watch-edge-1.json")
        )
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 8788)
        self.assertEqual(config.request_timeout_seconds, 6)
        self.assertEqual(len(config.upstream_base_urls), 3)
        self.assertIn("https://meet-eu.parad0xlabs.com", config.upstream_base_urls)
        self.assertTrue(str(config.auth_token or "").startswith("set-strong-meet-token"))
        self.assertIsNone(config.tls_certfile)
        self.assertIsNone(config.tls_keyfile)
        self.assertIsNone(config.tls_ca_file)

    def test_loads_ip_first_watch_tls_config(self) -> None:
        config = load_brain_hive_watch_config(
            Path("config/meet_clusters/do_ip_first_4node/watch-edge-1.json")
        )
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 8788)
        self.assertTrue(str(config.tls_certfile or "").endswith("watch-edge-1-cert.pem"))
        self.assertTrue(str(config.tls_keyfile or "").endswith("watch-edge-1-key.pem"))
        self.assertTrue(str(config.tls_ca_file or "").endswith("cluster-ca.pem"))


if __name__ == "__main__":
    unittest.main()
