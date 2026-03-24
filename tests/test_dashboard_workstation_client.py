from __future__ import annotations

import unittest

from core.dashboard.workstation_client import render_workstation_client_script
from core.dashboard.workstation_inspector_runtime import WORKSTATION_INSPECTOR_RUNTIME
from core.dashboard.workstation_nullabook_runtime import WORKSTATION_NULLABOOK_RUNTIME
from core.dashboard.workstation_overview_runtime import WORKSTATION_OVERVIEW_RUNTIME
from core.dashboard.workstation_trading_learning_runtime import WORKSTATION_TRADING_LEARNING_RUNTIME


class DashboardWorkstationClientTests(unittest.TestCase):
    def test_workstation_trading_learning_runtime_exports_market_and_learning_functions(self) -> None:
        self.assertIn("function latestTradingPresence(trading)", WORKSTATION_TRADING_LEARNING_RUNTIME)
        self.assertIn("function tradingPresenceState(trading, generatedAt, agents)", WORKSTATION_TRADING_LEARNING_RUNTIME)
        self.assertIn("function renderTrading(data)", WORKSTATION_TRADING_LEARNING_RUNTIME)
        self.assertIn("function renderLearningLab(data)", WORKSTATION_TRADING_LEARNING_RUNTIME)

    def test_workstation_inspector_runtime_exports_inspector_and_chrome_functions(self) -> None:
        self.assertIn("function encodeInspectPayload(payload)", WORKSTATION_INSPECTOR_RUNTIME)
        self.assertIn("function renderBrainInspector(type, label, payload)", WORKSTATION_INSPECTOR_RUNTIME)
        self.assertIn("function renderWorkstationChrome(data)", WORKSTATION_INSPECTOR_RUNTIME)
        self.assertIn("function bindWorkstationInspectorInteractions()", WORKSTATION_INSPECTOR_RUNTIME)

    def test_workstation_overview_runtime_exports_home_runtime_functions(self) -> None:
        self.assertIn("function renderTopStats(data)", WORKSTATION_OVERVIEW_RUNTIME)
        self.assertIn("function liveMovementSummary(data)", WORKSTATION_OVERVIEW_RUNTIME)
        self.assertIn("function renderOverview(data)", WORKSTATION_OVERVIEW_RUNTIME)
        self.assertNotIn("function renderAgents(data)", WORKSTATION_OVERVIEW_RUNTIME)

    def test_workstation_nullabook_runtime_exports_nullabook_surface(self) -> None:
        self.assertIn("function renderNullaBook(data)", WORKSTATION_NULLABOOK_RUNTIME)
        self.assertIn("initButterflyCanvas", WORKSTATION_NULLABOOK_RUNTIME)

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
        self.assertIn("function renderTopStats(data)", script)
        self.assertIn("function renderOverview(data)", script)
        self.assertIn("function renderAgents(data)", script)
        self.assertIn("function renderTrading(data)", script)
        self.assertIn("function renderLearningLab(data)", script)
        self.assertIn("function renderNullaBook(data)", script)
        self.assertIn("function renderBrainInspector(type, label, payload)", script)
        self.assertIn("function renderWorkstationChrome(data)", script)
        self.assertIn("function bindWorkstationInspectorInteractions()", script)
        self.assertIn("function renderCompactPostCard(post, options = {})", script)
        self.assertIn("function renderTaskEventFold(event)", script)
        self.assertIn("window.refresh = refresh;", script)


if __name__ == "__main__":
    unittest.main()
