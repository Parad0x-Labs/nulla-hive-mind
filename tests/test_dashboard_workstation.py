from __future__ import annotations

import unittest

from core.dashboard.workstation import render_workstation_dashboard_html
from core.dashboard.workstation_render import render_workstation_document
from core.dashboard.workstation_state import build_workstation_initial_state_payload


class _Hooks:
    def _branding_payload(self) -> dict[str, str]:
        return {"title": "NULLA"}


class DashboardWorkstationTests(unittest.TestCase):
    def test_workstation_state_payload_keeps_expected_top_level_sections(self) -> None:
        payload = build_workstation_initial_state_payload(hooks=_Hooks())

        self.assertEqual(payload["branding"], {"title": "NULLA"})
        self.assertIn("recent_activity", payload)
        self.assertIn("trading_learning", payload)
        self.assertIn("learning_lab", payload)
        self.assertEqual(payload["recent_activity"]["tasks"], [])

    def test_workstation_document_replaces_template_placeholders(self) -> None:
        html = render_workstation_document(
            initial_state='{"branding":{"title":"NULLA"}}',
            api_endpoint="/api/dashboard",
            topic_base_path="/task",
            initial_mode="fabric",
            canonical_url="https://nulla.test/hive?mode=fabric",
        )

        self.assertIn("/api/dashboard", html)
        self.assertIn('"branding":{"title":"NULLA"}', html)
        self.assertIn("NULLA Brain Hive · Live dashboard", html)
        self.assertNotIn("__INITIAL_STATE__", html)
        self.assertNotIn("__WORKSTATION_HEADER__", html)

    def test_workstation_dashboard_html_keeps_existing_surface_contract(self) -> None:
        html = render_workstation_dashboard_html(
            api_endpoint="/api/dashboard",
            topic_base_path="/task",
            initial_mode="fabric",
            canonical_url="https://nulla.test/hive?mode=fabric",
            hooks=_Hooks(),
        )

        self.assertIn("/api/dashboard", html)
        self.assertIn("/task", html)
        self.assertIn("NULLA Operator Workstation", html)
        self.assertIn("https://nulla.test/hive?mode=fabric", html)
        self.assertNotIn("__PUBLIC_META__", html)


if __name__ == "__main__":
    unittest.main()
