from __future__ import annotations

import unittest

from core.runtime_task_rail_summary_client import (
    RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT,
)


class RuntimeTaskRailSummaryClientTests(unittest.TestCase):
    def test_summary_script_keeps_stage_and_receipt_contract(self) -> None:
        script = RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT
        self.assertIn("function buildSummary(session, events)", script)
        self.assertIn("received: Boolean(serverStages.received)", script)
        self.assertIn("packet: Boolean(serverStages.packet)", script)
        self.assertIn("bundle: Boolean(serverStages.bundle)", script)
        self.assertIn("result: Boolean(serverStages.result)", script)
        self.assertIn("stopReason", script)
        self.assertIn("queryCompletedCount", script)
        self.assertIn("artifactRows", script)
        self.assertIn("approvalState", script)
        self.assertIn("rollbackState", script)
        self.assertIn("verifierState", script)
        self.assertIn("toolReceiptCount", script)


if __name__ == "__main__":
    unittest.main()
