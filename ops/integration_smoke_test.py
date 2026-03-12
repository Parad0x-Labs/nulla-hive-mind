from __future__ import annotations

from core.feature_flags import flag_map
from core.task_state_machine import current_state, transition
from core.trace_id import ensure_trace
from ops.feature_flags_report import write_report
from storage.cas import get_bytes, put_bytes


def run_smoke_test() -> dict:
    payload = b"nulla-smoke-payload"
    manifest = put_bytes(payload)
    roundtrip = get_bytes(manifest["blob_hash"])
    task_id = "smoke-task"
    ensure_trace(task_id, trace_id=task_id)
    if current_state("local_task", task_id) is None:
        transition(entity_type="local_task", entity_id=task_id, to_state="created", trace_id=task_id, details={"source": "smoke"})
    report_path = write_report()
    return {
        "cas_roundtrip_ok": roundtrip == payload,
        "feature_flags_known": "LOCAL_STANDALONE" in flag_map(),
        "status_report_path": report_path,
    }


def main() -> int:
    result = run_smoke_test()
    print(result)
    return 0 if all(value for key, value in result.items() if isinstance(value, bool)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
