from __future__ import annotations

import json
from pathlib import Path

from core.proof_manifest import repo_source_snapshot


def test_repo_source_snapshot_uses_archive_build_metadata_when_git_is_absent(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "build-source.json").write_text(
        json.dumps(
            {
                "branch": "codex/honest-ollama-prewarm-bootstrap",
                "commit": "0123456789abcdef0123456789abcdef01234567",
                "dirty_state": True,
                "source_kind": "archive",
            }
        ),
        encoding="utf-8",
    )

    truth = repo_source_snapshot(tmp_path)

    assert truth["source_kind"] == "archive"
    assert truth["branch"] == "codex/honest-ollama-prewarm-bootstrap"
    assert truth["commit"] == "0123456789abcdef0123456789abcdef01234567"
    assert truth["dirty_state"] is True


def test_repo_source_snapshot_coerces_string_dirty_state_values(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "build-source.json").write_text(
        json.dumps(
            {
                "branch": "main",
                "commit": "archive",
                "dirty_state": "false",
            }
        ),
        encoding="utf-8",
    )

    truth = repo_source_snapshot(tmp_path)

    assert truth["dirty_state"] is False
