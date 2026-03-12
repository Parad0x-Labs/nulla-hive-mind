from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.release_channel import release_manifest_warnings


_IGNORED_DIRS = {
    ".git",
    ".github",
    ".nulla_local",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
}


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
            hints.append(str(path.relative_to(PROJECT_ROOT)))
    return hints


def _repo_key_artifacts() -> list[str]:
    matches: list[str] = []
    for path in PROJECT_ROOT.rglob("node_signing_key.b64"):
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        matches.append(str(path.relative_to(PROJECT_ROOT)))
    return sorted(matches)


def build_report() -> dict[str, object]:
    issues: list[str] = []
    key_artifacts = _repo_key_artifacts()
    if key_artifacts:
        issues.append(f"repo-local signing key artifacts present: {', '.join(key_artifacts)}")

    license_hints = _license_placeholder_hints()
    if license_hints:
        issues.append(f"license placeholders still present: {', '.join(license_hints)}")

    release_warnings = release_manifest_warnings()
    if release_warnings:
        issues.extend(release_warnings)

    return {
        "status": "CLEAN" if not issues else "FAIL",
        "repo_key_artifacts": key_artifacts,
        "license_placeholders": license_hints,
        "release_warnings": release_warnings,
        "issues": issues,
    }


def main() -> int:
    report = build_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "CLEAN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
