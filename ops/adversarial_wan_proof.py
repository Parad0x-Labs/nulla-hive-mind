from __future__ import annotations

import json

from core.meet_proof_pack import run_adversarial_proof_pack


def build_report() -> dict[str, object]:
    results = run_adversarial_proof_pack()
    return {
        "suite": "adversarial_wan_proof",
        "status": "PASS" if all(item.passed for item in results) else "FAIL",
        "scenarios": [
            {"name": item.name, "passed": item.passed, "details": item.details}
            for item in results
        ],
    }


def main() -> int:
    print(json.dumps(build_report(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
