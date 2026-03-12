from __future__ import annotations

import json

from core.meet_proof_pack import run_cross_region_convergence_proof


def build_report() -> dict[str, object]:
    result = run_cross_region_convergence_proof()
    return {
        "suite": "cross_region_convergence",
        "status": "PASS" if result.passed else "FAIL",
        "scenario": {"name": result.name, "passed": result.passed, "details": result.details},
    }


def main() -> int:
    print(json.dumps(build_report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
