from __future__ import annotations

from typing import Any

from core import audit_logger


def live_info_search_notes_with_fallback(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    query: str,
    live_mode: str,
    interpretation: Any,
) -> list[dict[str, Any]]:
    try:
        notes = agent._live_info_search_notes(
            query=query,
            live_mode=live_mode,
            interpretation=interpretation,
        )
        raw_user_input = str(user_input or "").strip()
        if not notes and query != raw_user_input:
            notes = agent._live_info_search_notes(
                query=raw_user_input,
                live_mode=live_mode,
                interpretation=interpretation,
            )
        return notes
    except Exception as exc:
        audit_logger.log(
            "agent_live_info_fast_path_error",
            target_id=session_id,
            target_type="session",
            details={"error": str(exc), "query": query, "mode": live_mode},
        )
        return []
