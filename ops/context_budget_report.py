from __future__ import annotations

import json
import sys
from typing import Any

from storage.context_access_log import context_access_summary


def build_context_budget_report(limit: int = 200) -> dict[str, Any]:
    return context_access_summary(limit=limit)


def render_context_budget_report(report: dict[str, Any]) -> str:
    lines = [
        "NULLA CONTEXT BUDGET REPORT",
        "===========================",
        f"Entries                : {report['entries']}",
        f"Avg total budget       : {report['avg_total_budget']:.1f}",
        f"Avg tokens used        : {report['avg_tokens_used']:.1f}",
        f"Avg bootstrap tokens   : {report['avg_bootstrap_tokens']:.1f}",
        f"Avg relevant tokens    : {report['avg_relevant_tokens']:.1f}",
        f"Avg cold tokens        : {report['avg_cold_tokens']:.1f}",
        f"Swarm consult rate     : {report['swarm_consult_rate']:.2f}",
        f"Cold archive open rate : {report['cold_open_rate']:.2f}",
        "",
        "Retrieval confidence:",
    ]
    for key, value in sorted(report["retrieval_confidence_breakdown"].items()):
        lines.append(f"- {key}: {value}")
    if report["recent"]:
        lines.extend(["", "Recent prompt assemblies:"])
        for row in report["recent"]:
            lines.append(
                f"- {row['created_at']}: task={row['task_id']} confidence={row['retrieval_confidence']} tokens={row['tokens_used']}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    json_mode = "--json" in argv
    report = build_context_budget_report()
    if json_mode:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_context_budget_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
