from __future__ import annotations

import json
import sys

from core.trace_id import tasks_for_trace, trace_for_task
from storage.event_log import events_for_trace


def build_trace_report(task_id_or_trace_id: str) -> dict:
    trace = trace_for_task(task_id_or_trace_id)
    trace_id = trace.trace_id if trace else task_id_or_trace_id
    tasks = [record.__dict__ for record in tasks_for_trace(trace_id)]
    events = events_for_trace(trace_id)
    return {"trace_id": trace_id, "tasks": tasks, "events": events}


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if not argv:
        print("usage: python -m ops.swarm_trace_report <task_id_or_trace_id>")
        return 1
    print(json.dumps(build_trace_report(argv[0]), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
