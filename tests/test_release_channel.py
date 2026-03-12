from __future__ import annotations

import unittest

from core.release_channel import load_release_manifest, release_manifest_snapshot, release_manifest_warnings


class ReleaseChannelTests(unittest.TestCase):
    def test_release_manifest_loads(self) -> None:
        manifest = load_release_manifest()
        self.assertEqual(manifest.channel_name, "closed-test")
        self.assertGreaterEqual(manifest.protocol_version, 1)

    def test_release_manifest_uses_real_artifacts(self) -> None:
        warnings = release_manifest_warnings()
        self.assertFalse(any("placeholder" in warning for warning in warnings))
        self.assertFalse(any("sha256" in warning for warning in warnings))
        snapshot = release_manifest_snapshot()
        self.assertIn("artifacts", snapshot)
        self.assertTrue(all(str(item.get("sha256") or "").strip() for item in snapshot["artifacts"]))
