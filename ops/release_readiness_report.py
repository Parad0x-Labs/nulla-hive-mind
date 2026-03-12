from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.release_channel import release_manifest_snapshot
from core.runtime_guard import runtime_artifact_hints


def _license_placeholder_hints() -> list[str]:
    candidates = [
        PROJECT_ROOT / "LICENSE",
        PROJECT_ROOT / "LICENSES" / "BSL-1.1.txt",
        PROJECT_ROOT / "LICENSES" / "Apache-2.0.txt",
    ]
    hints: list[str] = []
    for path in candidates:
        try:
            body = path.read_text(encoding="utf-8").lower()
        except Exception:
            continue
        if "placeholder" in body or "replace this file" in body:
            hints.append(path.name)
    return hints


def build_report() -> dict[str, object]:
    manifest = release_manifest_snapshot()
    warnings = list(manifest.get("warnings") or [])
    hints = runtime_artifact_hints()
    license_hints = _license_placeholder_hints()
    if hints:
        warnings.append(f"runtime hygiene hints present: {', '.join(hints)}")
    if license_hints:
        warnings.append(f"license placeholders still present: {', '.join(license_hints)}")
    return {
        "status": "READY" if not warnings else "WARN",
        "manifest": manifest,
        "runtime_hints": hints,
        "license_hints": license_hints,
        "warnings": warnings,
    }


def main() -> int:
    print(json.dumps(build_report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
