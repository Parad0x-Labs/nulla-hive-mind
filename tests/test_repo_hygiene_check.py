from __future__ import annotations

import unittest

from ops.repo_hygiene_check import build_report


class RepoHygieneCheckTests(unittest.TestCase):
    def test_repo_hygiene_report_is_clean(self) -> None:
        report = build_report()
        self.assertEqual(report["status"], "CLEAN")
        self.assertEqual(report["issues"], [])


if __name__ == "__main__":
    unittest.main()
