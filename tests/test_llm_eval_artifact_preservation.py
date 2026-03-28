from __future__ import annotations

import json
import subprocess
from pathlib import Path

import ops.llm_eval as llm_eval
from core.llm_eval.pack import collect_recent_llm_inventory


def _fake_online_payload(*, failing: bool) -> dict[str, object]:
    p0_pass = not failing
    consistency_runs = [
        {"latency_seconds": 0.5, "pass": True, "assistant_text": "", "raw_response_text": ""}
        for _ in range(3)
    ]
    return {
        "captured_at_utc": "2026-03-27T00:00:00Z",
        "model": "qwen2.5:7b",
        "profile": {"id": "local-qwen25-7b-v1", "display_name": "NULLA local acceptance for qwen2.5:7b"},
        "runtime_version": {"commit": "abc123", "build_id": "test-build"},
        "machine": {"platform": "macOS", "cpu": "Apple M4", "ram_gb": 24.0, "gpu": "Apple M4"},
        "results": {
            "P0.1a_boot_hello": {"latency_seconds": 4.0, "pass": p0_pass, "assistant_text": "hello", "raw_response_text": ""},
            "P0.1b_capabilities": {"latency_seconds": 4.0, "pass": True, "assistant_text": "workspace", "raw_response_text": ""},
            "P0.2_local_file_create": {"latency_seconds": 0.6, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P0.3_append": {"latency_seconds": 0.6, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P0.3b_readback": {"latency_seconds": 0.6, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P0.5_tool_chain": {"latency_seconds": 0.8, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P0.6_logic": {"latency_seconds": 4.0, "pass": True, "assistant_text": "58 30", "raw_response_text": ""},
            "P0.4_live_lookup": {"latency_seconds": 0.2, "pass": True, "assistant_text": "Bitcoin is $70,576.00 USD. Source: CoinGecko.", "raw_response_text": ""},
            "P0.7_honesty_online": {"latency_seconds": 0.2, "pass": True, "assistant_text": "insufficient evidence", "raw_response_text": ""},
            "P1.3_instruction_fidelity": {"latency_seconds": 0.6, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P1.4_recovery": {"latency_seconds": 0.6, "pass": True, "assistant_text": "", "raw_response_text": ""},
            "P1.1_consistency": consistency_runs,
        },
    }


def test_preserve_previous_output_bundle_copies_non_green_summary(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "latest"
    output_root.mkdir()
    (output_root / "summary.json").write_text(
        json.dumps({"overall_full_green": False, "live_acceptance": {"status": "fail"}}),
        encoding="utf-8",
    )
    (output_root / "summary.md").write_text("old summary\n", encoding="utf-8")
    monkeypatch.setattr(llm_eval.time, "strftime", lambda fmt, now=None: "20260327T070000Z")

    preserved = llm_eval._preserve_previous_output_bundle(output_root)

    assert preserved == tmp_path / "latest_preserved_fail_20260327T070000Z"
    assert (preserved / "summary.json").exists()
    assert (preserved / "summary.md").read_text(encoding="utf-8") == "old summary\n"


def test_preserve_previous_live_run_artifacts_copies_non_green_bundle(monkeypatch, tmp_path: Path) -> None:
    run_root = tmp_path / "llm_eval_live"
    evidence_dir = run_root / "evidence"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "online_acceptance.json").write_text(
        json.dumps(_fake_online_payload(failing=True)),
        encoding="utf-8",
    )
    (evidence_dir / "offline_honesty.json").write_text(
        json.dumps({"result": {"latency_seconds": 0.05, "pass": True}}),
        encoding="utf-8",
    )
    (evidence_dir / "manual_btc_verification.json").write_text(
        json.dumps({"pass": True}),
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_eval.time, "strftime", lambda fmt, now=None: "20260327T070500Z")

    preserved = llm_eval._preserve_previous_live_run_artifacts(
        run_root=run_root,
        profile_path=llm_eval.DEFAULT_PROFILE_PATH,
    )

    assert preserved == tmp_path / "llm_eval_live_preserved_fail_20260327T070500Z"
    assert (preserved / "evidence" / "online_acceptance.json").exists()


def test_git_metadata_falls_back_to_build_source_json_when_git_checkout_is_missing(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "build-source.json").write_text(
        json.dumps(
            {
                "ref": "main",
                "branch": "main",
                "commit": "15b496e4992038cbd40a582c0e5aed9688d1d70e",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(llm_eval, "REPO_ROOT", tmp_path)

    seen_kwargs: list[dict[str, object]] = []

    def _raise_git_failure(*args, **kwargs):
        seen_kwargs.append(dict(kwargs))
        raise subprocess.CalledProcessError(128, args[0])

    monkeypatch.setattr(llm_eval.subprocess, "check_output", _raise_git_failure)

    assert llm_eval._git_branch() == "main"
    assert llm_eval._git_commit() == "15b496e4992038cbd40a582c0e5aed9688d1d70e"
    assert seen_kwargs
    assert all(item.get("stderr") == subprocess.DEVNULL for item in seen_kwargs)


def test_collect_recent_llm_inventory_returns_empty_inventory_when_git_history_is_unavailable(monkeypatch, tmp_path: Path) -> None:
    seen_kwargs: list[dict[str, object]] = []

    def _raise_git_failure(*args, **kwargs):
        seen_kwargs.append(dict(kwargs))
        raise subprocess.CalledProcessError(128, args[0])

    monkeypatch.setattr(subprocess, "check_output", _raise_git_failure)

    inventory = collect_recent_llm_inventory(tmp_path, since_hours=48)

    assert inventory == {
        "since_hours": 48,
        "changed_paths": [],
        "relevant_paths": [],
        "tests": [],
        "scripts": [],
        "docs": [],
        "workflows": [],
    }
    assert seen_kwargs
    assert all(item.get("stderr") == subprocess.DEVNULL for item in seen_kwargs)
