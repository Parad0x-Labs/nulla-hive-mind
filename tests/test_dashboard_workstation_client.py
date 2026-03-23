from __future__ import annotations

import unittest

from core.dashboard.workstation_client import render_workstation_client_script


class DashboardWorkstationClientTests(unittest.TestCase):
    def test_workstation_client_script_keeps_runtime_placeholders_and_surface_contract(self) -> None:
        script = render_workstation_client_script()

        self.assertIn("__WORKSTATION_SCRIPT__", script)
        self.assertIn("__INITIAL_STATE__", script)
        self.assertIn("__API_ENDPOINT__", script)
        self.assertIn("__TOPIC_BASE_PATH__", script)
        self.assertIn("__INITIAL_MODE__", script)
        self.assertIn("function tradingPresenceState(", script)
        self.assertIn("renderWorkstationChrome", script)
        self.assertIn("workstationHomeBoard", script)
        self.assertIn("function renderInto(containerId, html, {preserveDetails = false} = {})", script)
        self.assertIn("function renderCompactPostCard(post, options = {})", script)
        self.assertIn("function renderTaskEventFold(event)", script)
        self.assertIn("window.refresh = refresh;", script)


if __name__ == "__main__":
    unittest.main()
