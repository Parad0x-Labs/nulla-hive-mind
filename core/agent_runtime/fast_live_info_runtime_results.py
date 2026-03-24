from __future__ import annotations

from typing import Any


def disabled_live_info_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    disabled_response = (
        "Live web lookup is disabled on this runtime, so I can't verify current prices, "
        "weather, or latest-news requests honestly."
    )
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=disabled_response,
        confidence=0.82,
        source_context=source_context,
        reason="live_info_fast_path",
    )


def live_info_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    response: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.84,
        source_context=source_context,
        reason="live_info_fast_path",
    )
