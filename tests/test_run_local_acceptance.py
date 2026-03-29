from __future__ import annotations

import json
import socket
from pathlib import Path
from types import SimpleNamespace

import ops.run_local_acceptance as acceptance


def _fake_online_payload(
    *,
    commit: str = "abc123",
    fast: bool = True,
    runtime_model: str = "qwen2.5:7b",
    install_profile_id: str = "local-only",
    install_profile_label: str = "Local only",
) -> dict[str, object]:
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
        "model": runtime_model,
        "profile": {
            "id": "local-qwen25-7b-v1",
            "display_name": "NULLA local acceptance for qwen2.5:7b",
            "benchmark_model": "qwen2.5:7b",
            "runtime_model": runtime_model,
            "install_profile_id": install_profile_id,
            "install_profile_label": install_profile_label,
        },
        "runtime_version": {"commit": commit, "build_id": f"0.4.0-closed-test+{commit}.dirty", "model_tag": runtime_model},
        "capabilities": {
            "install_profile": {
                "profile_id": install_profile_id,
                "label": install_profile_label,
            }
        },
        "machine": {"platform": "macOS", "cpu": "Apple M4", "ram_gb": 24.0, "gpu": "Apple M4"},
        "results": results,
    }


def test_load_profile_reads_locked_qwen_profile() -> None:
    profile = acceptance.load_profile()

    assert profile.profile_id == "local-qwen25-7b-v1"
    assert profile.model == "qwen2.5:7b"
    assert profile.cold_start_max_seconds == 120.0
    assert profile.consistency_min_passes == 2


def test_startup_reply_is_coherent_accepts_valid_greetings() -> None:
    assert acceptance._startup_reply_is_coherent("Hello. What do you need?")
    assert acceptance._startup_reply_is_coherent("Hey. I'm NULLA. What do you need?")
    assert acceptance._startup_reply_is_coherent("NULLA here. How can I help?")


def test_startup_reply_is_coherent_rejects_non_greetings() -> None:
    assert not acceptance._startup_reply_is_coherent("")
    assert not acceptance._startup_reply_is_coherent("The answer is 4.")


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


def test_preserve_previous_run_artifacts_copies_non_green_bundle(monkeypatch, tmp_path: Path) -> None:
    profile = acceptance.load_profile()
    run_root = tmp_path / "llm_eval_live"
    evidence_dir = run_root / "evidence"
    evidence_dir.mkdir(parents=True)
    online_payload = _fake_online_payload()
    online_payload["results"]["P0.1a_boot_hello"]["pass"] = False
    (evidence_dir / "online_acceptance.json").write_text(json.dumps(online_payload), encoding="utf-8")
    (evidence_dir / "offline_honesty.json").write_text(
        json.dumps({"result": {"latency_seconds": 0.05, "pass": True}}),
        encoding="utf-8",
    )
    (evidence_dir / "manual_btc_verification.json").write_text(json.dumps({"pass": True}), encoding="utf-8")
    monkeypatch.setattr(acceptance.time, "strftime", lambda fmt, now=None: "20260327T071000Z")

    preserved = acceptance._preserve_previous_run_artifacts(run_root=run_root, profile=profile)

    assert preserved == tmp_path / "llm_eval_live_preserved_fail_20260327T071000Z"
    assert (preserved / "evidence" / "online_acceptance.json").exists()


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
    assert "Benchmark profile model: qwen2.5:7b" in rendered
    assert "Threshold gates:" in rendered
    assert "cold start <= 120.0s" in rendered


def test_render_report_surfaces_runtime_model_and_install_profile(tmp_path: Path) -> None:
    profile = acceptance.load_profile()
    output = tmp_path / "evidence" / "NULLA_LOCAL_ACCEPTANCE_REPORT.md"
    acceptance.render_report(
        repo_root=Path("/tmp/repo"),
        online_payload=_fake_online_payload(
            commit="5fa1dcf",
            runtime_model="qwen2.5:14b",
            install_profile_id="local-only",
            install_profile_label="Local only",
        ),
        offline_payload={"result": {"latency_seconds": 0.05, "pass": True}},
        manual_btc_check={"pass": True, "source": "CoinGecko", "observed": "$70,573.00 at 2026-03-20 23:09 UTC", "assessment": "tight", "acceptance_response": "Bitcoin is $70,576.00 USD."},
        output_path=output,
        profile=profile,
    )
    rendered = output.read_text(encoding="utf-8")

    assert "Runtime model: qwen2.5:14b" in rendered
    assert "Runtime install profile: local-only (Local only)" in rendered
    assert "NULLA on qwen2.5:14b is acceptable for local use under this test profile." in rendered


def test_default_runtime_command_targets_api_server_and_base_url_port(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    venv_python = repo_root / ".venv" / "bin"
    venv_python.mkdir(parents=True)
    (venv_python / "python").write_text("", encoding="utf-8")
    monkeypatch.setattr(acceptance, "REPO_ROOT", repo_root)

    command = acceptance._default_runtime_command(
        repo_root=repo_root,
        base_url="http://127.0.0.1:18080",
    )

    assert command[-6:] == ["-m", "apps.nulla_api_server", "--bind", "127.0.0.1", "--port", "18080"]
    assert command[0] == str(repo_root / ".venv" / "bin" / "python")


def test_resolve_runtime_command_uses_direct_launch_for_nondefault_port(tmp_path: Path) -> None:
    start_script = tmp_path / "run.sh"
    start_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    command = acceptance._resolve_runtime_command(
        repo_root=tmp_path,
        base_url="http://127.0.0.1:18080",
        start_script=start_script,
    )

    assert "apps.nulla_api_server" in command
    assert "--port" in command
    assert "18080" in command


def test_resolve_runtime_command_uses_start_script_for_default_launch_port_when_repo_venv_exists(tmp_path: Path) -> None:
    start_script = tmp_path / "run.sh"
    start_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    venv_python = tmp_path / ".venv" / "bin"
    venv_python.mkdir(parents=True)
    (venv_python / "python").write_text("", encoding="utf-8")

    command = acceptance._resolve_runtime_command(
        repo_root=tmp_path,
        base_url="http://127.0.0.1:11435",
        start_script=start_script,
    )

    assert command == ["sh", str(start_script)]


def test_resolve_runtime_command_falls_back_for_default_launch_port_when_start_script_repo_venv_missing(tmp_path: Path) -> None:
    start_script = tmp_path / "run.sh"
    start_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    command = acceptance._resolve_runtime_command(
        repo_root=tmp_path,
        base_url="http://127.0.0.1:11435",
        start_script=start_script,
    )

    assert "apps.nulla_api_server" in command
    assert "--port" in command
    assert "11435" in command


def test_suspend_installed_launch_agent_boots_out_loaded_default_port_on_macos(monkeypatch, tmp_path: Path) -> None:
    launch_agent_path = tmp_path / "ai.nulla.runtime.plist"
    launch_agent_path.write_text("<plist/>", encoding="utf-8")
    (tmp_path / "install_receipt.json").write_text(
        json.dumps({"launch_agent": {"macos": str(launch_agent_path), "enabled": True}}),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def _fake_run(command, **kwargs):
        commands.append(list(command))
        if command[:2] == ["launchctl", "print"]:
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(acceptance.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(acceptance.subprocess, "run", _fake_run)

    state = acceptance._suspend_installed_launch_agent(
        repo_root=tmp_path,
        base_url="http://127.0.0.1:11435",
    )

    assert state == {"path": str(launch_agent_path.resolve())}
    assert commands[0][:2] == ["launchctl", "print"]
    assert commands[1][:2] == ["launchctl", "bootout"]


def test_run_full_acceptance_restores_installed_launch_agent_when_suspended(monkeypatch, tmp_path: Path) -> None:
    profile = acceptance.load_profile()
    calls: list[str] = []
    runtime_home = tmp_path / "runtime_home"
    workspace_root = tmp_path / "workspace"
    start_script = tmp_path / "Start_NULLA.sh"
    start_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    monkeypatch.setattr(acceptance.subprocess, "check_output", lambda *args, **kwargs: "9141b55\n")
    monkeypatch.setattr(acceptance, "_pick_isolated_daemon_bind_port", lambda **kwargs: 60220)
    monkeypatch.setattr(acceptance, "_suspend_installed_launch_agent", lambda **kwargs: {"path": "/tmp/ai.nulla.runtime.plist"})
    monkeypatch.setattr(acceptance, "_restore_installed_launch_agent", lambda state: calls.append(f"restore:{state['path']}"))
    monkeypatch.setattr(acceptance, "_installed_default_model", lambda repo_root: "qwen2.5:14b")
    monkeypatch.setattr(acceptance, "_stop_runtime", lambda base_url: calls.append(f"stop:{base_url}"))
    monkeypatch.setattr(
        acceptance,
        "_start_runtime",
        lambda **kwargs: calls.append(f"start:{kwargs['expected_commit']}:{kwargs['daemon_bind_port']}:{kwargs['model']}") or object(),
    )
    monkeypatch.setattr(
        acceptance,
        "_wait_for_runtime",
        lambda base_url, *, expected_commit, expected_model, timeout=120.0: calls.append(
            f"wait:{expected_commit}:{expected_model}:{timeout}"
        ) or {"ok": True},
    )
    monkeypatch.setattr(
        acceptance.AcceptanceRunner,
        "run_online",
        lambda self: _fake_online_payload(commit="9141b55"),
    )
    monkeypatch.setattr(
        acceptance,
        "fetch_manual_btc_verification",
        lambda **kwargs: {
            "pass": True,
            "source": "CoinGecko",
            "observed": "$70,573.00 at 2026-03-20 23:09 UTC",
            "assessment": "tight",
            "acceptance_response": "Bitcoin is $70,576.00 USD.",
        },
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
    assert calls.count("report") == 1
    assert calls.count("start:9141b55:60220:qwen2.5:7b") == 2
    assert "restore:/tmp/ai.nulla.runtime.plist" in calls
    assert "wait:9141b55:qwen2.5:14b:180.0" in calls


def test_pick_isolated_daemon_bind_port_returns_stream_safe_pair() -> None:
    port = acceptance._pick_isolated_daemon_bind_port(host="127.0.0.1")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_sock:
        udp_sock.bind(("127.0.0.1", port))

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_sock:
        tcp_sock.bind(("127.0.0.1", port + 1))


def test_pick_isolated_daemon_bind_port_retries_when_stream_pair_is_occupied(monkeypatch) -> None:
    scripted_udp_ports = [41000, 42000]
    occupied_stream_ports = {41001}

    class _FakeSocket:
        def __init__(self, family: int, sock_type: int) -> None:
            self.family = family
            self.sock_type = sock_type
            self.bound = ("127.0.0.1", 0)

        def __enter__(self) -> _FakeSocket:
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def bind(self, addr: tuple[str, int]) -> None:
            host, port = addr
            if self.sock_type == acceptance.socket.SOCK_DGRAM and port == 0:
                if not scripted_udp_ports:
                    raise OSError("no scripted udp ports left")
                self.bound = (host, scripted_udp_ports.pop(0))
                return
            if self.sock_type == acceptance.socket.SOCK_STREAM and port in occupied_stream_ports:
                raise OSError("occupied stream port")
            self.bound = (host, port)

        def getsockname(self) -> tuple[str, int]:
            return self.bound

    monkeypatch.setattr(acceptance.socket, "socket", _FakeSocket)

    assert acceptance._pick_isolated_daemon_bind_port(host="127.0.0.1", attempts=2) == 42000


def test_run_full_acceptance_restores_online_runtime(monkeypatch, tmp_path: Path) -> None:
    profile = acceptance.load_profile()
    calls: list[str] = []
    runtime_home = tmp_path / "runtime_home"
    workspace_root = tmp_path / "workspace"
    start_script = tmp_path / "Start_NULLA.sh"
    start_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    monkeypatch.setattr(acceptance.subprocess, "check_output", lambda *args, **kwargs: "9141b55\n")
    monkeypatch.setattr(acceptance, "_pick_isolated_daemon_bind_port", lambda **kwargs: 60220)
    monkeypatch.setattr(acceptance, "_stop_runtime", lambda base_url: calls.append(f"stop:{base_url}"))
    monkeypatch.setattr(
        acceptance,
        "_start_runtime",
        lambda **kwargs: calls.append(f"start:{kwargs['expected_commit']}:{kwargs['daemon_bind_port']}") or object(),
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
    assert calls.count("start:9141b55:60220") == 3


def test_run_full_acceptance_uses_build_source_commit_when_git_is_unavailable(monkeypatch, tmp_path: Path) -> None:
    profile = acceptance.load_profile()
    calls: list[str] = []
    runtime_home = tmp_path / "runtime_home"
    workspace_root = tmp_path / "workspace"
    start_script = tmp_path / "Start_NULLA.sh"
    start_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "build-source.json").write_text(
        json.dumps({"commit": "82fb2a030909deadbeef"}),
        encoding="utf-8",
    )

    def _raise_git(*args, **kwargs):
        raise acceptance.subprocess.CalledProcessError(128, args[0])

    monkeypatch.setattr(acceptance.subprocess, "check_output", _raise_git)
    monkeypatch.setattr(acceptance, "_pick_isolated_daemon_bind_port", lambda **kwargs: 60220)
    monkeypatch.setattr(acceptance, "_stop_runtime", lambda base_url: calls.append(f"stop:{base_url}"))
    monkeypatch.setattr(
        acceptance,
        "_start_runtime",
        lambda **kwargs: calls.append(f"start:{kwargs['expected_commit']}:{kwargs['daemon_bind_port']}") or object(),
    )
    monkeypatch.setattr(
        acceptance.AcceptanceRunner,
        "run_online",
        lambda self: _fake_online_payload(commit="82fb2a030909"),
    )
    monkeypatch.setattr(
        acceptance,
        "fetch_manual_btc_verification",
        lambda **kwargs: {
            "pass": True,
            "source": "CoinGecko",
            "observed": "$70,573.00 at 2026-03-20 23:09 UTC",
            "assessment": "tight",
            "acceptance_response": "Bitcoin is $70,576.00 USD.",
        },
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
    assert calls.count("start:82fb2a030909:60220") == 3
