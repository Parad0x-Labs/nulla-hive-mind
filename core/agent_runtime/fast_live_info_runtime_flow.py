from __future__ import annotations

from typing import Any

from core import policy_engine

from .fast_live_info_runtime_results import disabled_live_info_result, live_info_result
from .fast_live_info_runtime_search import live_info_search_notes_with_fallback
from .fast_live_info_runtime_truth import (
    chat_truth_live_info_result,
    should_use_chat_truth_wording,
)


def maybe_handle_live_info_fast_path(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    interpretation: Any,
    response_class: Any,
) -> dict[str, Any] | None:
    live_mode = agent._live_info_mode(user_input, interpretation=interpretation)
    if not live_mode:
        return None
    if not policy_engine.allow_web_fallback():
        return disabled_live_info_result(
            agent,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
        )

    query = agent._normalize_live_info_query(user_input, mode=live_mode)
    if agent._requires_ultra_fresh_insufficient_evidence(user_input):
        response = agent._ultra_fresh_insufficient_evidence_response(query=query)
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.9,
            source_context=source_context,
            reason="live_info_insufficient_evidence",
        )

    notes = live_info_search_notes_with_fallback(
        agent,
        session_id=session_id,
        user_input=user_input,
        query=query,
        live_mode=live_mode,
        interpretation=interpretation,
    )
    unresolved_price = agent._unresolved_price_lookup_response(query=query, notes=notes, mode=live_mode)
    if unresolved_price:
        return live_info_result(
            agent,
            session_id=session_id,
            user_input=user_input,
            response=unresolved_price,
            source_context=source_context,
        )
    if not notes and live_mode == "fresh_lookup":
        return None

    response = (
        agent._render_live_info_response(query=query, notes=notes, mode=live_mode)
        if notes
        else agent._live_info_failure_text(query=query, mode=live_mode)
    )
    if should_use_chat_truth_wording(agent, source_context=source_context, live_mode=live_mode, notes=notes):
        return chat_truth_live_info_result(
            agent,
            session_id=session_id,
            user_input=user_input,
            query=query,
            live_mode=live_mode,
            notes=notes,
            source_context=source_context,
            interpretation=interpretation,
            response_class=response_class,
            response=response,
        )
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.86 if notes else 0.52,
        source_context=source_context,
        reason="live_info_fast_path",
    )
