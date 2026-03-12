from __future__ import annotations

import unittest

from ops.morning_after_audit_report import build_morning_after_audit_report, render_morning_after_audit_report
from ops.overnight_readiness_report import build_overnight_readiness_report, render_overnight_readiness_report
from storage.migrations import run_migrations


class OvernightReportsTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_overnight_readiness_report_has_go_no_go_and_checks(self) -> None:
        report = build_overnight_readiness_report()
        self.assertIn(report["go_no_go"], {"GO", "GO_WITH_WARNINGS", "NO_GO"})
        self.assertTrue(report["checks"])
        names = {item["name"] for item in report["checks"]}
        self.assertIn("schema_integrity", names)
        self.assertIn("event_hash_chain", names)
        rendered = render_overnight_readiness_report(report)
        self.assertIn("NULLA OVERNIGHT READINESS REPORT", rendered)
        self.assertIn("Go / No-Go:", rendered)

    def test_morning_after_audit_report_has_sections(self) -> None:
        report = build_morning_after_audit_report()
        self.assertIn(report["status"], {"pass", "warn", "fail"})
        self.assertTrue(report["sections"])
        names = {item["name"] for item in report["sections"]}
        self.assertIn("task_state", names)
        self.assertIn("event_chain", names)
        rendered = render_morning_after_audit_report(report)
        self.assertIn("NULLA MORNING-AFTER AUDIT", rendered)
        self.assertIn("Sections:", rendered)


if __name__ == "__main__":
    unittest.main()
