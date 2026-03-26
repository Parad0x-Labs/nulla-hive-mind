"""Post-install health summary for NULLA bundles."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


def _status(ok: bool, detail: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": bool(ok), "detail": str(detail)}
    payload.update(extra)
    return payload


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_binary(binary: str) -> str:
    candidate = str(binary or "").strip()
    if not candidate:
        return ""
    direct = Path(candidate).expanduser()
    if direct.exists():
        return str(direct)
    resolved = shutil.which(candidate)
    return str(resolved or "")


def _public_hive_status(project: Path, runtime: Path) -> dict[str, Any]:
    try:
        if str(project) not in sys.path:
            sys.path.insert(0, str(project))
        from core.public_hive_bridge import public_hive_has_auth, public_hive_write_requires_auth
    except Exception as exc:  # pragma: no cover - best effort only
        return _status(False, "public Hive status unavailable", error=str(exc))

    runtime_bootstrap = runtime / "config" / "agent-bootstrap.json"
    bundled_bootstrap = project / "config" / "agent-bootstrap.json"
    runtime_payload = _load_json(runtime_bootstrap)
    bundled_payload = _load_json(bundled_bootstrap)
    seed_urls = [
        str(url).strip()
        for url in list(runtime_payload.get("meet_seed_urls") or bundled_payload.get("meet_seed_urls") or [])
        if str(url).strip()
    ]
    runtime_auth_loaded = public_hive_has_auth(payload=runtime_payload)
    bundled_auth_loaded = public_hive_has_auth(payload=bundled_payload)

    if not seed_urls:
        return _status(
            True,
            "public Hive disabled or not configured",
            enabled=False,
            seed_count=0,
            requires_auth=False,
            write_enabled=False,
            runtime_bootstrap_path=str(runtime_bootstrap),
            runtime_bootstrap_exists=runtime_bootstrap.exists(),
            bundled_bootstrap_path=str(bundled_bootstrap),
            bundled_bootstrap_exists=bundled_bootstrap.exists(),
            runtime_auth_loaded=runtime_auth_loaded,
            bundled_auth_loaded=bundled_auth_loaded,
        )

    requires_auth = public_hive_write_requires_auth(seed_urls=seed_urls)
    write_enabled = not requires_auth or runtime_auth_loaded or bundled_auth_loaded
    if write_enabled:
        if runtime_auth_loaded:
            detail = "public Hive write auth ready"
        elif bundled_auth_loaded:
            detail = "public Hive auth bundled; runtime can hydrate on startup"
        else:
            detail = "public Hive write path ready without auth"
    else:
        detail = "public Hive write auth missing"
    return _status(
        write_enabled,
        detail,
        enabled=True,
        seed_count=len(seed_urls),
        requires_auth=requires_auth,
        write_enabled=write_enabled,
        runtime_bootstrap_path=str(runtime_bootstrap),
        runtime_bootstrap_exists=runtime_bootstrap.exists(),
        bundled_bootstrap_path=str(bundled_bootstrap),
        bundled_bootstrap_exists=bundled_bootstrap.exists(),
        runtime_auth_loaded=runtime_auth_loaded,
        bundled_auth_loaded=bundled_auth_loaded,
    )


def build_report(
    *,
    project_root: str,
    runtime_home: str,
    model_tag: str,
    openclaw_enabled: bool,
    openclaw_config_path: str,
    openclaw_agent_dir: str,
    ollama_binary: str,
) -> dict[str, Any]:
    project = Path(project_root).resolve()
    runtime = Path(runtime_home).expanduser().resolve()
    venv = project / ".venv"
    receipt = project / "install_receipt.json"
    liquefy_config = Path.home() / ".liquefy" / "config.json"
    ollama_path = _resolve_binary(ollama_binary)

    launchers = {
        "start": project / "Start_NULLA.sh",
        "chat": project / "Talk_To_NULLA.sh",
        "openclaw": project / "OpenClaw_NULLA.sh",
        "stage_trainable_base": project / "Stage_Trainable_Base.sh",
    }
    launcher_status = {
        name: _status(path.exists(), "present" if path.exists() else "missing", path=str(path))
        for name, path in launchers.items()
    }

    staged_bases: list[dict[str, Any]] = []
    try:
        if str(project) not in sys.path:
            sys.path.insert(0, str(project))
        from core.trainable_base_manager import list_staged_trainable_bases

        staged_bases = list_staged_trainable_bases()
    except Exception as exc:  # pragma: no cover - best effort only
        staged_bases = [{"error": str(exc)}]

    report = {
        "project_root": str(project),
        "runtime_home": str(runtime),
        "selected_model": str(model_tag or "").strip(),
        "components": {
            "project_root": _status(project.exists(), "project root found" if project.exists() else "project root missing", path=str(project)),
            "runtime_home": _status(runtime.exists(), "runtime home found" if runtime.exists() else "runtime home missing", path=str(runtime)),
            "venv": _status(venv.exists(), "virtualenv present" if venv.exists() else "virtualenv missing", path=str(venv)),
            "install_receipt": _status(receipt.exists(), "install receipt present" if receipt.exists() else "install receipt missing", path=str(receipt)),
            "launchers": {
                "ok": all(item["ok"] for item in launcher_status.values()),
                "items": launcher_status,
            },
            "openclaw": _status(
                (not openclaw_enabled) or (bool(openclaw_config_path) and Path(openclaw_config_path).expanduser().exists()),
                "OpenClaw configured" if openclaw_enabled and openclaw_config_path else ("OpenClaw skipped" if not openclaw_enabled else "OpenClaw config missing"),
                enabled=openclaw_enabled,
                config_path=str(openclaw_config_path or ""),
                agent_dir=str(openclaw_agent_dir or ""),
                agent_dir_exists=bool(openclaw_agent_dir) and Path(openclaw_agent_dir).expanduser().exists(),
            ),
            "liquefy": _status(liquefy_config.exists(), "Liquefy config present" if liquefy_config.exists() else "Liquefy config missing", path=str(liquefy_config)),
            "trainable_base": _status(bool(staged_bases), "staged trainable base found" if staged_bases else "no staged trainable base found", staged_bases=staged_bases),
            "ollama": _status(bool(ollama_path), "Ollama binary found" if ollama_path else "Ollama binary missing", path=str(ollama_path or ollama_binary or "")),
            "trace_surface": _status((project / "OpenClaw_NULLA.sh").exists(), "trace launcher path available" if (project / "OpenClaw_NULLA.sh").exists() else "trace launcher path missing", url="http://127.0.0.1:11435/trace"),
            "public_hive": _public_hive_status(project, runtime),
        },
    }

    degraded = []
    for key, value in report["components"].items():
        if isinstance(value, dict) and "ok" in value and not bool(value["ok"]):
            degraded.append(key)
    report["overall_status"] = "healthy" if not degraded else "degraded"
    report["degraded_components"] = degraded
    return report


def main() -> int:
    parser = argparse.ArgumentParser(prog="nulla-doctor")
    parser.add_argument("project_root")
    parser.add_argument("runtime_home")
    parser.add_argument("model_tag")
    parser.add_argument("openclaw_enabled")
    parser.add_argument("openclaw_config_path")
    parser.add_argument("openclaw_agent_dir")
    parser.add_argument("ollama_binary")
    args = parser.parse_args()

    report = build_report(
        project_root=args.project_root,
        runtime_home=args.runtime_home,
        model_tag=args.model_tag,
        openclaw_enabled=str(args.openclaw_enabled).strip().lower() in {"1", "true", "yes", "on"},
        openclaw_config_path=args.openclaw_config_path,
        openclaw_agent_dir=args.openclaw_agent_dir,
        ollama_binary=args.ollama_binary,
    )
    target = Path(args.project_root).resolve() / "install_doctor.json"
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
