from __future__ import annotations

from typing import Any

from .task_envelope import TaskEnvelopeV1


def merge_task_results(parent: TaskEnvelopeV1, results: list[dict[str, Any]]) -> dict[str, Any]:
    clean_results = [dict(item) for item in results if isinstance(item, dict)]
    strategy = str(parent.merge_strategy or "first_success").strip()
    if not clean_results:
        return {"strategy": strategy, "ok": False, "results": []}
    if strategy == "highest_score":
        winner = max(clean_results, key=lambda item: (float(item.get("score") or 0.0), str(item.get("task_id") or "")))
        return {"strategy": strategy, "ok": bool(winner.get("ok", True)), "winner": winner, "results": clean_results}
    if strategy == "concat_sections":
        ordered = sorted(clean_results, key=lambda item: str(item.get("task_id") or ""))
        combined = "\n\n".join(str(item.get("text") or "").strip() for item in ordered if str(item.get("text") or "").strip())
        return {"strategy": strategy, "ok": all(bool(item.get("ok", True)) for item in ordered), "text": combined, "results": ordered}
    ordered = sorted(clean_results, key=lambda item: str(item.get("task_id") or ""))
    winner = next((item for item in ordered if bool(item.get("ok", False))), ordered[0])
    return {"strategy": strategy, "ok": bool(winner.get("ok", False)), "winner": winner, "results": ordered}
