from __future__ import annotations

import json
from typing import Any

from core.model_registry import ModelRegistry


def build_license_audit_report() -> dict[str, Any]:
    registry = ModelRegistry()
    rows = registry.provider_audit_rows()
    warnings = registry.startup_warnings()
    return {
        "registered_provider_count": len(rows),
        "providers": [
            {
                "provider_id": row.provider_id,
                "source_type": row.source_type,
                "license_name": row.license_name,
                "license_reference": row.license_reference,
                "runtime_dependency": row.runtime_dependency,
                "weight_location": row.weight_location,
                "weights_bundled": row.weights_bundled,
                "redistribution_allowed": row.redistribution_allowed,
                "warnings": row.warnings,
            }
            for row in rows
        ],
        "warnings": warnings,
    }


def render_license_audit(report: dict[str, Any]) -> str:
    lines = [
        "NULLA MODEL LICENSE AUDIT",
        "========================",
        f"Registered providers: {int(report.get('registered_provider_count') or 0)}",
    ]
    warnings = list(report.get("warnings") or [])
    if warnings:
        lines.append("Warnings:")
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("Warnings: none")
    for provider in list(report.get("providers") or []):
        lines.append("")
        lines.append(str(provider.get("provider_id") or "unknown"))
        lines.append(f"  Source:            {provider.get('source_type') or 'unknown'}")
        lines.append(f"  License:           {provider.get('license_name') or 'MISSING'}")
        lines.append(f"  License ref:       {provider.get('license_reference') or 'MISSING'}")
        lines.append(f"  Runtime dependency:{' '}{provider.get('runtime_dependency') or 'MISSING'}")
        lines.append(f"  Weight location:   {provider.get('weight_location') or 'unknown'}")
        lines.append(f"  Weights bundled:   {bool(provider.get('weights_bundled'))}")
        redist = provider.get("redistribution_allowed")
        lines.append(f"  Redistribution:    {redist if redist is not None else 'unknown'}")
        provider_warnings = list(provider.get("warnings") or [])
        lines.append(f"  Entry warnings:    {'; '.join(provider_warnings) if provider_warnings else 'none'}")
    return "\n".join(lines)


def main() -> int:
    report = build_license_audit_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
