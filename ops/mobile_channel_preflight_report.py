from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.mobile_companion_view import build_mobile_companion_snapshot


def build_mobile_channel_preflight_report() -> dict[str, Any]:
    snapshot = build_mobile_companion_snapshot(limit_recent=3)
    return {
        "status": "preflight_ready_for_controlled_testing",
        "surfaces": {
            "web_companion": "planned_test_path",
            "telegram": "optional_gateway_surface",
            "discord": "optional_gateway_surface",
        },
        "device_role_policy": {
            "phone": "companion_or_presence_mirror",
            "desktop_or_server": "primary_brain",
            "meet_node": "stable_non_phone_infrastructure",
        },
        "privacy_posture": {
            "metadata_first": True,
            "archive_included_by_default": False,
            "remote_payloads_included_by_default": False,
        },
        "current_snapshot": snapshot,
        "next_live_checks": [
            "web companion bounded task flow",
            "telegram bounded task flow",
            "discord bounded task flow",
            "phone reconnect and cache rebuild",
            "sensitive history exclusion",
        ],
    }


def render_mobile_channel_preflight_report(report: dict[str, Any]) -> str:
    lines = [
        "NULLA MOBILE AND CHANNEL PREFLIGHT",
        "",
        f"Status: {report['status']}",
        "Surfaces:",
    ]
    for key, value in report["surfaces"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "Device Roles:"])
    for key, value in report["device_role_policy"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "Privacy Posture:"])
    for key, value in report["privacy_posture"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "Next Live Checks:"])
    for item in report["next_live_checks"]:
        lines.append(f"- {item}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_mobile_channel_preflight_report(build_mobile_channel_preflight_report()))
