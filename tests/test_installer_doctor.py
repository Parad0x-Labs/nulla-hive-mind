from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

from installer.doctor import build_report
from installer.write_install_receipt import build_receipt


def test_install_receipt_exposes_doctor_report_path() -> None:
    receipt = build_receipt(
        project_root="/tmp/nulla",
        runtime_home="/tmp/nulla-home",
        model_tag="qwen2.5:7b",
        openclaw_enabled=True,
        openclaw_config_path="/tmp/openclaw.json",
        openclaw_agent_dir="/tmp/agent",
        ollama_binary="/tmp/ollama",
    )

    assert receipt["doctor_report_path"].endswith("/nulla/install_doctor.json")


def test_build_report_marks_missing_components_as_degraded() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        (root / "Start_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Talk_To_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "OpenClaw_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Stage_Trainable_Base.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".nulla_runtime"
        runtime_home.mkdir()
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="qwen2.5:7b",
                openclaw_enabled=True,
                openclaw_config_path=str(root / "missing-openclaw.json"),
                openclaw_agent_dir=str(root / "missing-agent"),
                ollama_binary=str(root / "missing-ollama"),
            )

    assert report["overall_status"] == "degraded"
    assert "openclaw" in report["degraded_components"]
    assert "ollama" in report["degraded_components"]
    assert report["components"]["launchers"]["ok"] is True
    assert report["components"]["public_hive"]["ok"] is True
    assert report["components"]["public_hive"]["enabled"] is False


def test_build_report_flags_missing_public_hive_write_auth() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        (root / "config").mkdir()
        (root / "config" / "agent-bootstrap.json").write_text(
            json.dumps({"meet_seed_urls": ["https://seed-eu.example.test:8766"]}) + "\n",
            encoding="utf-8",
        )
        (root / "Start_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Talk_To_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "OpenClaw_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Stage_Trainable_Base.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".nulla_runtime"
        runtime_home.mkdir()
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="qwen2.5:7b",
                openclaw_enabled=False,
                openclaw_config_path="",
                openclaw_agent_dir="",
                ollama_binary="",
            )

    assert report["components"]["public_hive"]["ok"] is False
    assert report["components"]["public_hive"]["requires_auth"] is True
    assert report["components"]["public_hive"]["write_enabled"] is False
    assert "public_hive" in report["degraded_components"]


def test_build_report_accepts_bundled_public_hive_auth() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        (root / "config").mkdir()
        (root / "config" / "agent-bootstrap.json").write_text(
            json.dumps(
                {
                    "meet_seed_urls": ["https://seed-eu.example.test:8766"],
                    "auth_token": "bundle-token",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "Start_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Talk_To_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "OpenClaw_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Stage_Trainable_Base.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".nulla_runtime"
        runtime_home.mkdir()
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]):
            report = build_report(
                project_root=str(root),
                runtime_home=str(runtime_home),
                model_tag="qwen2.5:7b",
                openclaw_enabled=False,
                openclaw_config_path="",
                openclaw_agent_dir="",
                ollama_binary="",
            )

    assert report["components"]["public_hive"]["ok"] is True
    assert report["components"]["public_hive"]["write_enabled"] is True
    assert report["components"]["public_hive"]["bundled_auth_loaded"] is True


def test_build_report_resolves_ollama_binary_from_path_lookup() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / ".venv").mkdir()
        (root / "Start_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Talk_To_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "OpenClaw_NULLA.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "Stage_Trainable_Base.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "install_receipt.json").write_text("{}\n", encoding="utf-8")
        runtime_home = root / ".nulla_runtime"
        runtime_home.mkdir()
        with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]):
            with mock.patch("shutil.which", return_value="/usr/local/bin/ollama"):
                report = build_report(
                    project_root=str(root),
                    runtime_home=str(runtime_home),
                    model_tag="qwen2.5:7b",
                    openclaw_enabled=False,
                    openclaw_config_path="",
                    openclaw_agent_dir="",
                    ollama_binary="ollama",
                )

    assert report["components"]["ollama"]["ok"] is True
    assert report["components"]["ollama"]["path"] == "/usr/local/bin/ollama"
