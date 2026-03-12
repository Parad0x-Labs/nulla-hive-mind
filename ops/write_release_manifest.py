from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.runtime_paths import config_path


def _latest_checksum_manifest(build_root: Path) -> Path:
    manifests = sorted(build_root.glob("Decentralized_NULLA_Installer_*_SHA256SUMS.txt"))
    if not manifests:
        raise FileNotFoundError("No installer checksum manifest found under build/installer/")
    return manifests[-1]


def _parse_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, filename = line.split(None, 1)
        checksums[filename.strip()] = digest.strip()
    return checksums


def build_manifest(*, version: str) -> dict[str, object]:
    build_root = PROJECT_ROOT / "build" / "installer"
    checksum_path = _latest_checksum_manifest(build_root)
    checksums = _parse_checksums(checksum_path)
    stamp = checksum_path.name.removeprefix("Decentralized_NULLA_Installer_").removesuffix("_SHA256SUMS.txt")
    zip_name = f"Decentralized_NULLA_Installer_{stamp}.zip"
    tar_name = f"Decentralized_NULLA_Installer_{stamp}.tar.gz"

    return {
        "channel_name": "closed-test",
        "release_version": version,
        "protocol_version": 1,
        "schema_generation": 1,
        "minimum_compatible_release": version,
        "rollout_stage": "private_team_test",
        "update_strategy": "manual_pull_then_restart",
        "requires_clean_runtime": True,
        "signed_write_required": True,
        "notes": "Closed production-style testing manifest generated from the latest installer bundle.",
        "artifacts": [
            {
                "platform": "macos",
                "role": "agent_or_brain",
                "path": f"build/installer/{zip_name}",
                "sha256": checksums.get(zip_name, ""),
            },
            {
                "platform": "linux",
                "role": "agent_or_meet",
                "path": f"build/installer/{tar_name}",
                "sha256": checksums.get(tar_name, ""),
            },
            {
                "platform": "windows",
                "role": "agent_or_brain",
                "path": f"build/installer/{zip_name}",
                "sha256": checksums.get(zip_name, ""),
            },
        ],
    }


def write_manifest(*, version: str) -> Path:
    manifest_path = config_path("release", "update_channel.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(build_manifest(version=version), indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    version = args[0] if args else "0.4.0-closed-test"
    path = write_manifest(version=version)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
