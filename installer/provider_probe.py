from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any
from urllib import request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.hardware_tier import MachineProbe, select_qwen_tier, tier_summary
from core.provider_routing import ProviderCapabilityTruth
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_install_profiles import build_install_profile_truth


def detect_ollama_binary() -> str:
    candidate = shutil.which("ollama")
    return str(candidate or "")


def _ollama_api_url() -> str:
    raw = str(os.environ.get("NULLA_RAW_OLLAMA_API_URL") or "").strip()
    if raw:
        return raw
    host = str(os.environ.get("OLLAMA_HOST") or "").strip()
    if host:
        if host.startswith(("http://", "https://")):
            return host
        return f"http://{host}"
    return "http://127.0.0.1:11434"


def _format_ollama_size_label(value: Any) -> str:
    try:
        size = float(value)
    except Exception:
        return ""
    gib = 1024.0 ** 3
    mib = 1024.0 ** 2
    if size >= gib:
        return f"{size / gib:.1f} GB"
    if size >= mib:
        return f"{size / mib:.1f} MB"
    if size > 0:
        return f"{int(size)} B"
    return ""


def _list_ollama_models_via_api(api_url: str | None = None) -> list[dict[str, str]]:
    base = str(api_url or "").strip() or _ollama_api_url()
    url = f"{base.rstrip('/')}/api/tags"
    try:
        with request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    rows: list[dict[str, str]] = []
    for raw in list(payload.get("models") or []):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("model") or "").strip()
        if not name:
            continue
        digest = str(raw.get("digest") or "").strip()
        rows.append(
            {
                "name": name,
                "id": digest[:12] if digest else "",
                "size": _format_ollama_size_label(raw.get("size")),
                "modified": str(raw.get("modified_at") or "").strip(),
            }
        )
    return rows


def list_ollama_models(ollama_binary: str | None = None, ollama_api_url: str | None = None) -> list[dict[str, str]]:
    api_rows = _list_ollama_models_via_api(ollama_api_url)
    if api_rows:
        return api_rows
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
    def _present(*names: str) -> bool:
        return any(bool(os.environ.get(name)) for name in names)

    kimi = {
        "api_key_present": _present("KIMI_API_KEY", "MOONSHOT_API_KEY", "NULLA_KIMI_API_KEY"),
        "base_url_present": _present("KIMI_BASE_URL", "NULLA_KIMI_BASE_URL", "MOONSHOT_BASE_URL"),
        "model_present": _present("KIMI_MODEL", "NULLA_KIMI_MODEL", "MOONSHOT_MODEL"),
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
        "kimi": kimi | {"configured": kimi["api_key_present"]},
        "generic_remote": generic_remote | {"configured": all(generic_remote.values())},
        "tether": tether | {"configured": tether["api_key_present"] and tether["base_url_present"]},
        "qvac": qvac | {"configured": qvac["api_key_present"] and qvac["base_url_present"]},
    }


def _provider_state_for_prefix(
    capability_truth: tuple[ProviderCapabilityTruth, ...],
    prefix: str,
) -> tuple[str, str]:
    matches = [item for item in capability_truth if item.provider_id.lower().startswith(prefix)]
    if not matches:
        return "unregistered", ""
    for candidate in matches:
        if candidate.availability_state == "ready":
            return candidate.availability_state, candidate.provider_id
    for candidate in matches:
        if candidate.availability_state == "degraded":
            return candidate.availability_state, candidate.provider_id
    return matches[0].availability_state, matches[0].provider_id


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


def _probe_env_for_install_profile(env_statuses: dict[str, dict[str, Any]]) -> dict[str, str]:
    env = dict(os.environ)
    if env_statuses.get("kimi", {}).get("configured"):
        env.setdefault("KIMI_API_KEY", "configured-via-provider-probe")
    if env_statuses.get("generic_remote", {}).get("configured"):
        env.setdefault("OPENAI_API_KEY", "configured-via-provider-probe")
    return env


def build_probe_report(
    *,
    machine: MachineProbe | None = None,
    ollama_models: list[dict[str, str]] | None = None,
    ollama_binary: str | None = None,
    env_statuses: dict[str, dict[str, Any]] | None = None,
    provider_capability_truth: tuple[ProviderCapabilityTruth, ...] | None = None,
    show_unsupported: bool = False,
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
    capability_truth = tuple(provider_capability_truth or build_provider_registry_snapshot().capability_truth)
    profile_truth = build_install_profile_truth(
        requested_profile="auto-recommended",
        probe=probe,
        tier=primary_tier,
        selected_model=primary_tier.ollama_tag,
        provider_capability_truth=capability_truth,
        env=_probe_env_for_install_profile(envs),
    )
    kimi_state, kimi_provider_id = _provider_state_for_prefix(capability_truth, "kimi-remote:")
    kimi_configured = bool(envs.get("kimi", {}).get("configured"))

    stacks: list[dict[str, Any]] = []
    local_only_ready = bool(binary)
    stacks.append(
        {
            "stack_id": "local_only",
            "install_profile_id": "local-only",
            "status": "ready" if local_only_ready else "needs_install",
            "recommended": False,
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
            "install_profile_id": "local-max",
            "status": dual_status,
            "recommended": False,
            "reason": dual_reason,
            "primary_model": primary_tier.ollama_tag,
            "helper_model": helper_model,
        }
    )

    if kimi_configured:
        if kimi_state == "ready":
            kimi_status = "ready"
            kimi_reason = "Kimi credentials are present and runtime bootstrap exposes a real remote Kimi queen lane."
        elif kimi_state == "degraded":
            kimi_status = "degraded"
            kimi_reason = "Kimi credentials are present and the remote Kimi queen lane exists, but it is currently degraded."
        elif kimi_state == "blocked":
            kimi_status = "blocked"
            kimi_reason = "Kimi credentials are present, but the remote Kimi queen lane is blocked by current health state."
        else:
            kimi_status = "misconfigured"
            kimi_reason = "Kimi credentials are present, but runtime bootstrap did not register a usable Kimi lane."
    else:
        kimi_status = "needs_config"
        kimi_reason = "Kimi becomes a real remote queen lane when KIMI_API_KEY or MOONSHOT_API_KEY is configured."
    stacks.append(
        {
            "stack_id": "local_plus_kimi",
            "install_profile_id": "hybrid-kimi",
            "status": kimi_status,
            "recommended": False,
            "reason": kimi_reason,
            "primary_model": primary_tier.ollama_tag,
            "helper_model": helper_model if local_fit != "single_model_only" else "",
            "provider_id": kimi_provider_id,
        }
    )

    unsupported_stacks: list[dict[str, Any]] = []
    if show_unsupported:
        unsupported_stacks.extend(
            [
                {
                    "stack_id": "local_plus_tether",
                    "status": "not_implemented",
                    "recommended": False,
                    "reason": "Tether does not have a real first-class installer/runtime lane in this repo yet.",
                    "primary_model": primary_tier.ollama_tag,
                    "helper_model": "",
                },
                {
                    "stack_id": "local_plus_qvac",
                    "status": "not_implemented",
                    "recommended": False,
                    "reason": "QVAC does not have a real first-class installer/runtime lane in this repo yet.",
                    "primary_model": primary_tier.ollama_tag,
                    "helper_model": "",
                },
            ]
        )

    for stack in stacks:
        stack["recommended"] = stack.get("install_profile_id") == profile_truth.profile_id
    recommended = next((item for item in stacks if item.get("recommended")), stacks[0])
    report = {
        "schema": "nulla.provider_probe.v1",
        "machine": summary,
        "ollama": {
            "binary_present": bool(binary),
            "binary_path": binary,
            "installed_models": models,
        },
        "remote_env": envs,
        "local_multi_llm_fit": local_fit,
        "recommended_install_profile_id": profile_truth.profile_id,
        "recommended_install_profile_label": profile_truth.label,
        "recommended_install_profile_summary": profile_truth.summary,
        "recommended_stack_id": str(recommended.get("stack_id") or ""),
        "stacks": stacks,
    }
    if unsupported_stacks:
        report["unsupported_stacks"] = unsupported_stacks
    return report


def render_probe_report(report: dict[str, Any]) -> str:
    machine = dict(report.get("machine") or {})
    ollama = dict(report.get("ollama") or {})
    stacks = [dict(item) for item in list(report.get("stacks") or []) if isinstance(item, dict)]
    unsupported_stacks = [dict(item) for item in list(report.get("unsupported_stacks") or []) if isinstance(item, dict)]
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
    lines.append(f"- recommended install profile: {report.get('recommended_install_profile_id') or 'unknown'}")
    lines.append(f"- recommended stack: {report.get('recommended_stack_id') or 'unknown'}")
    lines.append("- stack status:")
    for stack in stacks:
        install_profile_id = str(stack.get("install_profile_id") or "").strip()
        profile_suffix = f" -> {install_profile_id}" if install_profile_id else ""
        lines.append(f"  - {stack.get('stack_id')}{profile_suffix}: {stack.get('status')} — {stack.get('reason')}")
    if unsupported_stacks:
        lines.append("- unsupported stacks:")
        for stack in unsupported_stacks:
            lines.append(f"  - {stack.get('stack_id')}: {stack.get('status')} — {stack.get('reason')}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="nulla-provider-probe")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable text.")
    parser.add_argument(
        "--show-unsupported",
        action="store_true",
        help="Include unsupported remote ideas like Tether or QVAC in a separate section.",
    )
    args = parser.parse_args()

    report = build_probe_report(show_unsupported=bool(args.show_unsupported))
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_probe_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
