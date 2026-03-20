from __future__ import annotations

import json
from pathlib import Path

import ops.run_local_acceptance as acceptance


def _fake_online_payload(*, commit: str = "abc123", fast: bool = True) -> dict[str, object]:
    simple = 4.0 if fast else 10.0
    file_latency = 0.6 if fast else 20.0
    lookup_latency = 0.2 if fast else 50.0
    chain_latency = 0.8 if fast else 70.0
    pass_value = True
    consistency_runs = [
        {
            "latency_seconds": file_latency,
            "pass": pass_value,
            "assistant_text": "",
            "raw_response_text": "",
        }
        for _ in range(3)
    ]
    results = {
        "P0.1a_boot_hello": {"latency_seconds": simple, "pass": True, "assistant_text": "hello", "raw_response_text": ""},
        "P0.1b_capabilities": {"latency_seconds": simple, "pass": True, "assistant_text": "workspace", "raw_response_text": ""},
        "P0.2_local_file_create": {"latency_seconds": file_latency, "pass": True, "assistant_text": "", "raw_response_text": ""},
        "P0.3_append": {"latency_seconds": file_latency, "pass": True, "assistant_text": "", "raw_response_text": ""},
        "P0.3b_readback": {"latency_seconds": file_latency, "pass": True, "assistant_text": "", "raw_response_text": ""},
        "P0.5_tool_chain": {"latency_seconds": chain_latency, "pass": True, "assistant_text": "", "raw_response_text": ""},
        "P0.6_logic": {"latency_seconds": simple, "pass": True, "assistant_text": "58 30", "raw_response_text": ""},
        "P0.4_live_lookup": {
            "latency_seconds": lookup_latency,
            "pass": True,
            "assistant_text": "Bitcoin is $70,576.00 USD as of 2026-03-20 23:08 UTC. Source: [CoinGecko](https://www.coingecko.com/en/coins/bitcoin).",
            "raw_response_text": "",
        },
        "P0.7_honesty_online": {"latency_seconds": lookup_latency, "pass": True, "assistant_text": "insufficient evidence", "raw_response_text": ""},
        "P1.3_instruction_fidelity": {"latency_seconds": file_latency, "pass": True, "assistant_text": "", "raw_response_text": ""},
        "P1.4_recovery": {"latency_seconds": file_latency, "pass": True, "assistant_text": "", "raw_response_text": ""},
        "P1.1_consistency": consistency_runs,
    }
    return {
        "captured_at_utc": "2026-03-21T00:00:00Z",
        "model": "qwen2.5:7b",
        "profile": {"id": "local-qwen25-7b-v1", "display_name": "NULLA local acceptance for qwen2.5:7b"},
        "runtime_version": {"commit": commit, "build_id": f"0.4.0-closed-test+{commit}.dirty"},
        "machine": {"platform": "macOS", "cpu": "Apple M4", "ram_gb": 24.0, "gpu": "Apple M4"},
        "results": results,
    }


def test_load_profile_reads_locked_qwen_profile() -> None:
    profile = acceptance.load_profile()

    assert profile.profile_id == "local-qwen25-7b-v1"
    assert profile.model == "qwen2.5:7b"
    assert profile.cold_start_max_seconds == 120.0
    assert profile.consistency_min_passes == 2


def test_build_acceptance_summary_enforces_thresholds() -> None:
    profile = acceptance.load_profile()
    summary = acceptance.build_acceptance_summary(
        online_payload=_fake_online_payload(fast=False),
        offline_payload={"result": {"latency_seconds": 10.0, "pass": True}},
        manual_btc_check={"pass": True},
        profile=profile,
    )

    assert summary["threshold_checks"]["simple_prompt_median_max_seconds"]["pass"] is False
    assert summary["threshold_checks"]["file_task_median_max_seconds"]["pass"] is False
    assert summary["threshold_checks"]["live_lookup_median_max_seconds"]["pass"] is False
    assert summary["threshold_checks"]["chained_task_median_max_seconds"]["pass"] is False
    assert summary["overall_green"] is False


def test_fetch_manual_btc_verification_writes_json(monkeypatch, tmp_path: Path) -> None:
    profile = acceptance.load_profile()
    online_payload = _fake_online_payload()

    class _Response:
        def read(self) -> bytes:
            return b'{"bitcoin":{"usd":70573}}'

    monkeypatch.setattr(acceptance.request, "urlopen", lambda *args, **kwargs: _Response())
    monkeypatch.setattr(acceptance.time, "strftime", lambda fmt, now=None: "2026-03-20 23:09 UTC")
    monkeypatch.setattr(acceptance.time, "gmtime", lambda: None)

    manual = acceptance.fetch_manual_btc_verification(
        repo_root=Path("/tmp/repo"),
        run_root=tmp_path,
        online_payload=online_payload,
        profile=profile,
    )

    assert manual["pass"] is True
    assert manual["source"] == "CoinGecko simple price API"
    saved = json.loads((tmp_path / "evidence" / "manual_btc_verification.json").read_text(encoding="utf-8"))
    assert saved["observed"] == "$70,573.00 at 2026-03-20 23:09 UTC"


def test_render_report_includes_profile_and_thresholds(tmp_path: Path) -> None:
    profile = acceptance.load_profile()
    output = tmp_path / "evidence" / "NULLA_LOCAL_ACCEPTANCE_REPORT.md"
    acceptance.render_report(
        repo_root=Path("/tmp/repo"),
        online_payload=_fake_online_payload(commit="9141b55"),
        offline_payload={"result": {"latency_seconds": 0.05, "pass": True}},
        manual_btc_check={"pass": True, "source": "CoinGecko", "observed": "$70,573.00 at 2026-03-20 23:09 UTC", "assessment": "tight", "acceptance_response": "Bitcoin is $70,576.00 USD."},
        output_path=output,
        profile=profile,
    )
    rendered = output.read_text(encoding="utf-8")

    assert "Profile: local-qwen25-7b-v1" in rendered
    assert "Threshold gates:" in rendered
    assert "cold start <= 120.0s" in rendered


def test_run_full_acceptance_restores_online_runtime(monkeypatch, tmp_path: Path) -> None:
    profile = acceptance.load_profile()
    calls: list[str] = []
    runtime_home = tmp_path / "runtime_home"
    workspace_root = tmp_path / "workspace"
    start_script = tmp_path / "Start_NULLA.sh"
    start_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    monkeypatch.setattr(acceptance.subprocess, "check_output", lambda *args, **kwargs: "9141b55\n")
    monkeypatch.setattr(acceptance, "_stop_runtime", lambda base_url: calls.append(f"stop:{base_url}"))
    monkeypatch.setattr(
        acceptance,
        "_start_runtime",
        lambda **kwargs: calls.append(f"start:{kwargs['expected_commit']}") or object(),
    )
    monkeypatch.setattr(
        acceptance.AcceptanceRunner,
        "run_online",
        lambda self: _fake_online_payload(commit="9141b55"),
    )
    monkeypatch.setattr(
        acceptance,
        "fetch_manual_btc_verification",
        lambda **kwargs: {"pass": True, "source": "CoinGecko", "observed": "$70,573.00 at 2026-03-20 23:09 UTC", "assessment": "tight", "acceptance_response": "Bitcoin is $70,576.00 USD."},
    )
    monkeypatch.setattr(
        acceptance,
        "run_offline_honesty",
        lambda *args, **kwargs: {"result": {"latency_seconds": 0.05, "pass": True}},
    )
    monkeypatch.setattr(acceptance, "render_report", lambda **kwargs: calls.append("report"))

    exit_code = acceptance.run_full_acceptance(
        base_url="http://127.0.0.1:11435",
        repo_root=tmp_path,
        run_root=tmp_path,
        profile=profile,
        runtime_home=runtime_home,
        workspace_root=workspace_root,
        start_script=start_script,
    )

    assert exit_code == 0
    assert not (runtime_home / "config" / "default_policy.yaml").exists()
    assert calls.count("report") == 1
    assert calls.count("start:9141b55") == 3
