from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ops import repo_hygiene_check
from ops.repo_hygiene_check import build_report


class RepoHygieneCheckTests(unittest.TestCase):
    def test_repo_hygiene_report_is_clean(self) -> None:
        report = build_report()
        self.assertEqual(report["status"], "CLEAN")
        self.assertEqual(report["issues"], [])

    def test_repo_key_artifacts_ignore_generated_acceptance_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            key_dir = root / "artifacts" / "acceptance_runs" / "run-1" / "runtime_home" / "data" / "keys"
            key_dir.mkdir(parents=True)
            (key_dir / "node_signing_key.b64").write_text("test-key", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._repo_key_artifacts(), [])

    def test_repo_key_artifacts_still_flag_non_generated_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            key_dir = root / "network" / "fixtures"
            key_dir.mkdir(parents=True)
            (key_dir / "node_signing_key.b64").write_text("test-key", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._repo_key_artifacts(), ["network/fixtures/node_signing_key.b64"])

    def test_repo_key_artifacts_flag_keyring_metadata_records_too(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            key_dir = root / "network" / "fixtures"
            key_dir.mkdir(parents=True)
            (key_dir / "node_signing_key.keyring.json").write_text("{}", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._repo_key_artifacts(), ["network/fixtures/node_signing_key.keyring.json"])

    def test_repo_key_artifacts_ignore_generated_greenloop_runtime_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            key_dir = root / "reports" / "greenloop" / "live_acceptance" / "runtime_home" / "data" / "keys"
            key_dir.mkdir(parents=True)
            (key_dir / "node_signing_key.b64").write_text("test-key", encoding="utf-8")

            with mock.patch.object(repo_hygiene_check, "PROJECT_ROOT", root):
                self.assertEqual(repo_hygiene_check._repo_key_artifacts(), [])

    def test_public_docs_do_not_embed_absolute_local_paths(self) -> None:
        public_docs = [
            "README.md",
            "REPO_MAP.md",
            "CONTRIBUTING.md",
            "AGENT_HANDOVER.md",
            "docs/README.md",
            "docs/PLATFORM_REFACTOR_PLAN.md",
            "docs/STATUS.md",
            "docs/PROOF_PATH.md",
            "docs/SYSTEM_SPINE.md",
            "docs/CONTROL_PLANE.md",
        ]
        forbidden_fragments = ("/Users/", "/private/tmp/")

        for relative_path in public_docs:
            content = (repo_hygiene_check.PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            for fragment in forbidden_fragments:
                self.assertNotIn(fragment, content, msg=f"{relative_path} leaked {fragment}")


if __name__ == "__main__":
    unittest.main()
