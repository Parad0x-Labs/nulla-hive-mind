from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.hardware_tier import MachineProbe, select_qwen_tier, tier_summary


def detect_ollama_binary() -> str:
    candidate = shutil.which("ollama")
    return str(candidate or "")


def list_ollama_models(ollama_binary: str | None = None) -> list[dict[str, str]]:
    binary = str(ollama_binary or "").strip() or detect_ollama_binary()
    if not binary:
        return []
    try:
        completed = subprocess.run(
            [binary, "list"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception:
        return []
    lines = [line.rstrip() for line in str(completed.stdout or "").splitlines() if line.strip()]
    if len(lines) <= 1:
        return []
    rows: list[dict[str, str]] = []
    for raw in lines[1:]:
        parts = [part.strip() for part in re.split(r"\s{2,}", raw.strip()) if part.strip()]
        if len(parts) < 4:
            continue
        name = parts[0]
        model_id = parts[1]
        size = parts[2]
        modified = " ".join(parts[3:])
        rows.append({"name": name, "id": model_id, "size": size, "modified": modified})
    return rows


def remote_env_statuses() -> dict[str, dict[str, Any]]:
    kimi = {
        "api_key_present": bool(os.environ.get("KIMI_API_KEY")),
        "base_url_present": bool(os.environ.get("KIMI_BASE_URL")),
        "model_present": bool(os.environ.get("NULLA_KIMI_MODEL")),
    }
    generic_remote = {
        "api_key_present": bool(os.environ.get("NULLA_REMOTE_API_KEY")),
        "base_url_present": bool(os.environ.get("NULLA_REMOTE_BASE_URL")),
        "model_present": bool(os.environ.get("NULLA_REMOTE_MODEL")),
    }
    tether = {
        "api_key_present": bool(os.environ.get("TETHER_API_KEY")),
        "base_url_present": bool(os.environ.get("TETHER_BASE_URL")),
        "model_present": bool(os.environ.get("NULLA_TETHER_MODEL")),
    }
    qvac = {
        "api_key_present": bool(os.environ.get("QVAC_API_KEY")),
        "base_url_present": bool(os.environ.get("QVAC_BASE_URL")),
        "model_present": bool(os.environ.get("NULLA_QVAC_MODEL")),
    }
    return {
        "kimi": kimi | {"configured": kimi["api_key_present"] and kimi["base_url_present"]},
        "generic_remote": generic_remote | {"configured": all(generic_remote.values())},
        "tether": tether | {"configured": tether["api_key_present"] and tether["base_url_present"]},
        "qvac": qvac | {"configured": qvac["api_key_present"] and qvac["base_url_present"]},
    }


def local_multi_llm_fit(summary: dict[str, Any]) -> str:
    ram_gb = float(summary.get("ram_gb") or 0.0)
    accelerator = str(summary.get("accelerator") or "").strip().lower()
    vram_gb = float(summary.get("vram_gb") or 0.0) if summary.get("vram_gb") is not None else 0.0
    if accelerator == "mps":
        if ram_gb >= 48.0:
            return "comfortable"
        if ram_gb >= 24.0:
            return "pressure_sensitive"
        return "single_model_only"
    if vram_gb >= 20.0 or ram_gb >= 48.0:
        return "comfortable"
    if vram_gb >= 10.0 or ram_gb >= 24.0:
        return "pressure_sensitive"
    return "single_model_only"


def build_probe_report(
    *,
    machine: MachineProbe | None = None,
    ollama_models: list[dict[str, str]] | None = None,
    ollama_binary: str | None = None,
    env_statuses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    summary = tier_summary(machine)
    probe = machine
    if probe is None:
        from core.hardware_tier import probe_machine

        probe = probe_machine()
    primary_tier = select_qwen_tier(probe)
    helper_model = "qwen2.5:7b"
    binary = str(ollama_binary or "").strip() or detect_ollama_binary()
    models = list(ollama_models if ollama_models is not None else list_ollama_models(binary))
    model_names = {str(item.get("name") or "").strip() for item in models if str(item.get("name") or "").strip()}
    envs = dict(env_statuses or remote_env_statuses())
    local_fit = local_multi_llm_fit(summary)

    stacks: list[dict[str, Any]] = []
    local_only_ready = bool(binary)
    stacks.append(
        {
            "stack_id": "local_only",
            "status": "ready" if local_only_ready else "needs_install",
            "recommended": local_fit == "single_model_only",
            "reason": (
                "Local Ollama lane is ready."
                if local_only_ready
                else "Ollama is missing; installer must provision it before this lane is usable."
            ),
            "primary_model": primary_tier.ollama_tag,
            "helper_model": "",
        }
    )

    dual_ready = bool(binary) and primary_tier.ollama_tag in model_names and helper_model in model_names
    dual_status = (
        "ready"
        if dual_ready and local_fit != "single_model_only"
        else "needs_model_pull"
        if bool(binary) and local_fit != "single_model_only"
        else "too_small"
        if local_fit == "single_model_only"
        else "needs_install"
    )
    dual_reason = {
        "ready": "This machine can run a primary local model plus a lighter local helper lane, but 24 GiB unified memory is still pressure-sensitive under concurrency.",
        "needs_model_pull": "The machine can support a dual local lane, but the required helper or primary model is not installed yet.",
        "too_small": "This machine should stay on one local model at a time.",
        "needs_install": "Ollama is missing, so no dual-local stack is ready yet.",
    }[dual_status]
    stacks.append(
        {
            "stack_id": "local_dual_ollama",
            "status": dual_status,
            "recommended": local_fit in {"comfortable", "pressure_sensitive"},
            "reason": dual_reason,
            "primary_model": primary_tier.ollama_tag,
            "helper_model": helper_model,
        }
    )

    kimi_configured = bool(envs.get("kimi", {}).get("configured"))
    stacks.append(
        {
            "stack_id": "local_plus_kimi",
            "status": "not_implemented",
            "recommended": False,
            "reason": (
                "Kimi credentials are present, but the installer/runtime profile is not wired yet."
                if kimi_configured
                else "Kimi is not configured on this machine, and the installer/runtime profile is not wired yet."
            ),
            "primary_model": primary_tier.ollama_tag,
            "helper_model": helper_model if local_fit != "single_model_only" else "",
        }
    )

    stacks.append(
        {
            "stack_id": "local_plus_tether",
            "status": "not_implemented",
            "recommended": False,
            "reason": "Tether does not have a real first-class installer/runtime lane in this repo yet.",
            "primary_model": primary_tier.ollama_tag,
            "helper_model": "",
        }
    )
    stacks.append(
        {
            "stack_id": "local_plus_qvac",
            "status": "not_implemented",
            "recommended": False,
            "reason": "QVAC does not have a real first-class installer/runtime lane in this repo yet.",
            "primary_model": primary_tier.ollama_tag,
            "helper_model": "",
        }
    )

    recommended = next((item for item in stacks if item.get("recommended")), stacks[0])
    return {
        "schema": "nulla.provider_probe.v1",
        "machine": summary,
        "ollama": {
            "binary_present": bool(binary),
            "binary_path": binary,
            "installed_models": models,
        },
        "remote_env": envs,
        "local_multi_llm_fit": local_fit,
        "recommended_stack_id": str(recommended.get("stack_id") or ""),
        "stacks": stacks,
    }


def render_probe_report(report: dict[str, Any]) -> str:
    machine = dict(report.get("machine") or {})
    ollama = dict(report.get("ollama") or {})
    stacks = [dict(item) for item in list(report.get("stacks") or []) if isinstance(item, dict)]
    lines = [
        "NULLA machine/provider probe",
        f"- machine: {machine.get('cpu_cores')} cores, {machine.get('ram_gb')} GiB RAM, {machine.get('gpu') or 'no gpu'}",
        f"- accelerator: {machine.get('accelerator') or 'unknown'}",
        f"- recommended local model: {machine.get('ollama_model') or 'unknown'}",
        f"- local multi-LLM fit: {report.get('local_multi_llm_fit') or 'unknown'}",
        f"- ollama present: {'yes' if ollama.get('binary_present') else 'no'}",
    ]
    installed = [str(item.get("name") or "").strip() for item in list(ollama.get("installed_models") or []) if str(item.get("name") or "").strip()]
    lines.append(f"- installed local models: {', '.join(installed) if installed else 'none'}")
    lines.append(f"- recommended stack: {report.get('recommended_stack_id') or 'unknown'}")
    lines.append("- stack status:")
    for stack in stacks:
        lines.append(f"  - {stack.get('stack_id')}: {stack.get('status')} — {stack.get('reason')}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="nulla-provider-probe")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    args = parser.parse_args()

    report = build_probe_report()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_probe_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
