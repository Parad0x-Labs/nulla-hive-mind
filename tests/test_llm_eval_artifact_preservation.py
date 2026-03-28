from __future__ import annotations

import json
import subprocess
from argparse import Namespace
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


def _passing_group_result(name: str) -> dict[str, object]:
    return {
        "category": name,
        "status": "pass",
        "scenarios": [],
        "totals": {"total": 0, "passed": 0, "failed": 0},
    }


def _passing_regression_payload(baseline_root: Path, inventory: dict[str, object]) -> dict[str, object]:
    return {
        "status": "pass",
        "baseline_path": "",
        "inventory": inventory,
        "current": {
            "status": "pass",
            "targets": [],
            "summary": {"passed": 0, "failed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0},
            "duration_seconds": 0.0,
        },
        "comparison": {
            "status": "equal",
            "baseline_available": False,
            "summary_delta": {},
            "duration_delta_seconds": 0.0,
            "pass_regressed": False,
            "duration_regressed": False,
        },
    }


def test_run_skips_docs_report_write_by_default(monkeypatch, tmp_path: Path) -> None:
    docs_report_path = tmp_path / "docs" / "LLM_ACCEPTANCE_REPORT.md"
    output_root = tmp_path / "reports" / "llm_eval" / "latest"
    baseline_root = tmp_path / "reports" / "llm_eval" / "baselines"
    live_run_root = tmp_path / "artifacts" / "acceptance_runs" / "llm_eval_live"

    monkeypatch.setattr(llm_eval, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(llm_eval, "_git_commit", lambda: "abc123")
    monkeypatch.setattr(llm_eval, "_git_branch", lambda: "main")
    monkeypatch.setattr(llm_eval.time, "strftime", lambda fmt, now=None: "2026-03-28T20:00:00Z")
    monkeypatch.setattr(llm_eval.local_acceptance, "_machine_info", lambda: {"platform": "macOS", "python": "3.11.15", "cpu": "Apple M4", "ram_gb": 24.0, "gpu": "Apple M4"})
    monkeypatch.setattr(
        llm_eval.local_acceptance,
        "load_profile",
        lambda path: llm_eval.local_acceptance.AcceptanceProfile(
            profile_id="local-qwen25-7b-v1",
            display_name="NULLA local acceptance for qwen2.5:7b",
            model="qwen2.5:7b",
            cold_start_max_seconds=120.0,
            simple_prompt_median_max_seconds=8.0,
            file_task_median_max_seconds=15.0,
            live_lookup_median_max_seconds=45.0,
            chained_task_median_max_seconds=60.0,
            consistency_min_passes=2,
            manual_btc_source_label="CoinGecko",
            manual_btc_source_url="https://example.invalid",
        ),
    )
    monkeypatch.setattr(llm_eval, "collect_recent_llm_inventory", lambda repo_root, since_hours=48: {"since_hours": since_hours, "changed_paths": [], "relevant_paths": [], "tests": [], "scripts": [], "docs": [], "workflows": []})
    monkeypatch.setattr(
        llm_eval,
        "_regression_payload",
        _passing_regression_payload,
    )
    monkeypatch.setattr(llm_eval, "_scenario_group_result", lambda name, scenarios: _passing_group_result(name))

    args = Namespace(
        output_root=str(output_root),
        baseline_root=str(baseline_root),
        live_run_root=str(live_run_root),
        profile=str(llm_eval.DEFAULT_PROFILE_PATH),
        base_url=llm_eval.DEFAULT_BASE_URL,
        branch_label="",
        docs_report_path="",
        skip_live_runtime=True,
    )

    assert llm_eval.run(args) == 0
    assert not docs_report_path.exists()


def test_run_writes_docs_report_only_when_explicitly_requested(monkeypatch, tmp_path: Path) -> None:
    docs_report_path = tmp_path / "docs" / "LLM_ACCEPTANCE_REPORT.md"
    output_root = tmp_path / "reports" / "llm_eval" / "latest"
    baseline_root = tmp_path / "reports" / "llm_eval" / "baselines"
    live_run_root = tmp_path / "artifacts" / "acceptance_runs" / "llm_eval_live"

    monkeypatch.setattr(llm_eval, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(llm_eval, "_git_commit", lambda: "abc123")
    monkeypatch.setattr(llm_eval, "_git_branch", lambda: "main")
    monkeypatch.setattr(llm_eval.time, "strftime", lambda fmt, now=None: "2026-03-28T20:00:00Z")
    monkeypatch.setattr(llm_eval.local_acceptance, "_machine_info", lambda: {"platform": "macOS", "python": "3.11.15", "cpu": "Apple M4", "ram_gb": 24.0, "gpu": "Apple M4"})
    monkeypatch.setattr(
        llm_eval.local_acceptance,
        "load_profile",
        lambda path: llm_eval.local_acceptance.AcceptanceProfile(
            profile_id="local-qwen25-7b-v1",
            display_name="NULLA local acceptance for qwen2.5:7b",
            model="qwen2.5:7b",
            cold_start_max_seconds=120.0,
            simple_prompt_median_max_seconds=8.0,
            file_task_median_max_seconds=15.0,
            live_lookup_median_max_seconds=45.0,
            chained_task_median_max_seconds=60.0,
            consistency_min_passes=2,
            manual_btc_source_label="CoinGecko",
            manual_btc_source_url="https://example.invalid",
        ),
    )
    monkeypatch.setattr(llm_eval, "collect_recent_llm_inventory", lambda repo_root, since_hours=48: {"since_hours": since_hours, "changed_paths": [], "relevant_paths": [], "tests": [], "scripts": [], "docs": [], "workflows": []})
    monkeypatch.setattr(
        llm_eval,
        "_regression_payload",
        _passing_regression_payload,
    )
    monkeypatch.setattr(llm_eval, "_scenario_group_result", lambda name, scenarios: _passing_group_result(name))

    args = Namespace(
        output_root=str(output_root),
        baseline_root=str(baseline_root),
        live_run_root=str(live_run_root),
        profile=str(llm_eval.DEFAULT_PROFILE_PATH),
        base_url=llm_eval.DEFAULT_BASE_URL,
        branch_label="",
        docs_report_path="docs/LLM_ACCEPTANCE_REPORT.md",
        skip_live_runtime=True,
    )

    assert llm_eval.run(args) == 0
    assert docs_report_path.exists()
    assert "NULLA LLM Acceptance Summary" in docs_report_path.read_text(encoding="utf-8")
