"""Write a small install receipt for support and launcher diagnostics."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_install_profiles import InstallProfileTruth, build_install_profile_truth


def _provider_snapshot_and_profile(
    *,
    model_tag: str,
    runtime_home: str,
) -> tuple[list[dict], InstallProfileTruth]:
    snapshot = build_provider_registry_snapshot()
    install_profile = build_install_profile_truth(
        requested_profile=os.environ.get("NULLA_INSTALL_PROFILE"),
        selected_model=model_tag,
        runtime_home=runtime_home,
        provider_capability_truth=snapshot.capability_truth,
    )
    return [item.to_dict() for item in snapshot.capability_truth], install_profile


def build_receipt(
    *,
    project_root: str,
    runtime_home: str,
    model_tag: str,
    openclaw_enabled: bool,
    openclaw_config_path: str,
    openclaw_agent_dir: str,
    ollama_binary: str,
) -> dict:
    project = Path(project_root).resolve()
    provider_capability_truth, install_profile = _provider_snapshot_and_profile(
        model_tag=model_tag,
        runtime_home=runtime_home,
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project),
        "runtime_home": runtime_home,
        "selected_model": model_tag,
        "provider_capability_truth": provider_capability_truth,
        "install_profile": install_profile.to_dict(),
        "api_url": "http://127.0.0.1:11435",
        "openclaw_url": "http://127.0.0.1:18789",
        "trace_url": "http://127.0.0.1:11435/trace",
        "doctor_report_path": str(project / "install_doctor.json"),
        "openclaw_enabled": bool(openclaw_enabled),
        "openclaw_config_path": openclaw_config_path,
        "openclaw_agent_dir": openclaw_agent_dir,
        "ollama_binary": ollama_binary,
        "web_stack": {
            "provider_order": ["searxng", "ddg_instant", "duckduckgo_html"],
            "searxng_url": "http://127.0.0.1:8080",
            "playwright_enabled": True,
            "browser_engine": "chromium",
            "browser_render_default": "enabled_via_installer_launchers",
            "xsearch_bootstrap": "attempted_by_installer_and_launchers",
        },
        "launchers": {
            "install_and_run": {
                "macos": str(project / "Install_And_Run_NULLA.command"),
                "linux": str(project / "Install_And_Run_NULLA.sh"),
                "windows": str(project / "Install_And_Run_NULLA.bat"),
            },
            "start": {
                "macos": str(project / "Start_NULLA.command"),
                "linux": str(project / "Start_NULLA.sh"),
                "windows": str(project / "Start_NULLA.bat"),
            },
            "chat": {
                "macos": str(project / "Talk_To_NULLA.command"),
                "linux": str(project / "Talk_To_NULLA.sh"),
                "windows": str(project / "Talk_To_NULLA.bat"),
            },
            "openclaw": {
                "macos": str(project / "OpenClaw_NULLA.command"),
                "linux": str(project / "OpenClaw_NULLA.sh"),
                "windows": str(project / "OpenClaw_NULLA.bat"),
            },
            "stage_trainable_base": {
                "macos": str(project / "Stage_Trainable_Base.command"),
                "linux": str(project / "Stage_Trainable_Base.sh"),
                "windows": str(project / "Stage_Trainable_Base.bat"),
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="write_install_receipt")
    parser.add_argument("project_root")
    parser.add_argument("runtime_home")
    parser.add_argument("model_tag")
    parser.add_argument("openclaw_enabled")
    parser.add_argument("openclaw_config_path")
    parser.add_argument("openclaw_agent_dir")
    parser.add_argument("ollama_binary")
    args = parser.parse_args()

    receipt = build_receipt(
        project_root=args.project_root,
        runtime_home=args.runtime_home,
        model_tag=args.model_tag,
        openclaw_enabled=str(args.openclaw_enabled).strip().lower() in {"1", "true", "yes", "on"},
        openclaw_config_path=args.openclaw_config_path,
        openclaw_agent_dir=args.openclaw_agent_dir,
        ollama_binary=args.ollama_binary,
    )
    target_path = Path(args.project_root).resolve() / "install_receipt.json"
    target_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(str(target_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
