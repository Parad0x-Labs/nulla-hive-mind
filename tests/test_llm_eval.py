from __future__ import annotations

import json
from pathlib import Path

import ops.llm_eval as llm_eval


def test_provider_inventory_includes_local_and_remote_rows(monkeypatch) -> None:
    report = {
        "machine": {"accelerator": "mps"},
        "ollama": {"installed_models": [{"name": "qwen2.5:14b"}]},
        "stacks": [
            {"stack_id": "local_plus_kimi", "status": "not_implemented", "reason": "not wired"},
        ],
    }
    monkeypatch.setattr(llm_eval, "build_probe_report", lambda: report)

    inventory = llm_eval._provider_inventory()

    assert inventory[0]["provider_id"] == "ollama-local:qwen2.5:14b"
    assert inventory[1]["provider_id"] == "remote:kimi"


def test_write_report_bundle_writes_required_artifacts(tmp_path: Path) -> None:
    output_root = tmp_path / "latest"
    baseline_root = tmp_path / "baselines"
    live_run_root = tmp_path / "live"
    report = {
        "metadata": {"run_id": "run-1", "branch": "main", "commit_sha": "abc", "finished_at": "2026-03-27T00:00:00Z"},
        "environment": {},
        "provider_inventory": [],
        "gates": [],
        "failure_ledger": [],
        "fix_ledger": [],
        "metrics": {
            "scenario_groups": {"local_core": {"passed": True}},
            "latency_summary": {
                "simple_prompt_median_s": 1.0,
                "file_task_median_s": 2.0,
                "live_lookup_median_s": 3.0,
                "chained_task_median_s": 4.0,
            },
        },
        "artifacts": {},
        "signoff": {"verdict": "green", "risk_level": "low"},
        "regression_md": "no baseline\n",
        "latency_rows": [("online_acceptance", "P0.1a_boot_hello", 1.23)],
    }

    original_doc = llm_eval.LATEST_DOC_PATH
    try:
        llm_eval.LATEST_DOC_PATH = tmp_path / "LLM_ACCEPTANCE_REPORT.md"
        llm_eval.write_report_bundle(report, output_root=output_root, baseline_root=baseline_root, live_run_root=live_run_root)
    finally:
        llm_eval.LATEST_DOC_PATH = original_doc

    assert (output_root / "summary.md").exists()
    assert (output_root / "summary.json").exists()
    assert (output_root / "latency.csv").exists()
    assert (output_root / "failures.md").exists()
    assert (output_root / "regression_48h.md").exists()
    assert (live_run_root / "evidence" / "LLM_ACCEPTANCE_REPORT.md").exists()
    saved = json.loads((output_root / "summary.json").read_text(encoding="utf-8"))
    assert saved["signoff"]["verdict"] == "green"


def test_main_uses_run_eval_cycle_and_writes_bundle(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "latest"
    baseline_root = tmp_path / "baselines"
    live_run_root = tmp_path / "live"
    captured: dict[str, object] = {}

    def fake_run_eval_cycle(**kwargs):
        captured["kwargs"] = kwargs
        return {
            "metadata": {"run_id": "run-1", "branch": "main", "commit_sha": "abc", "finished_at": "2026-03-27T00:00:00Z"},
            "environment": {},
            "provider_inventory": [],
            "gates": [],
            "failure_ledger": [],
            "fix_ledger": [],
            "metrics": {"scenario_groups": {}, "latency_summary": {}},
            "artifacts": {},
            "signoff": {"verdict": "green", "risk_level": "low"},
            "regression_md": "",
            "latency_rows": [],
        }

    monkeypatch.setattr(llm_eval, "run_eval_cycle", fake_run_eval_cycle)
    original_doc = llm_eval.LATEST_DOC_PATH
    try:
        llm_eval.LATEST_DOC_PATH = tmp_path / "LLM_ACCEPTANCE_REPORT.md"
        exit_code = llm_eval.main(
            [
                "--output-root",
                str(output_root),
                "--baseline-root",
                str(baseline_root),
                "--live-run-root",
                str(live_run_root),
                "--skip-live-runtime",
            ]
        )
    finally:
        llm_eval.LATEST_DOC_PATH = original_doc

    assert exit_code == 0
    assert captured["kwargs"]["skip_live_runtime"] is True
    assert (output_root / "summary.md").exists()
