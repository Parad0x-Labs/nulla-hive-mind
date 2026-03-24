from __future__ import annotations

from typing import Any

from core import audit_logger, policy_engine
from core.identity_manager import load_active_persona


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
        return _disabled_live_info_result(
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

    notes = _live_info_search_notes_with_fallback(
        agent,
        session_id=session_id,
        user_input=user_input,
        query=query,
        live_mode=live_mode,
        interpretation=interpretation,
    )
    unresolved_price = agent._unresolved_price_lookup_response(query=query, notes=notes, mode=live_mode)
    if unresolved_price:
        return _live_info_result(
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
    if _should_use_chat_truth_wording(agent, source_context=source_context, live_mode=live_mode, notes=notes):
        return _chat_truth_live_info_result(
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


def _disabled_live_info_result(
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


def _live_info_search_notes_with_fallback(
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


def _live_info_result(
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


def _should_use_chat_truth_wording(
    agent: Any,
    *,
    source_context: dict[str, object] | None,
    live_mode: str,
    notes: list[dict[str, Any]],
) -> bool:
    structured_modes = {"weather", "news"}
    return (
        agent._is_chat_truth_surface(source_context)
        and live_mode not in structured_modes
        and agent._first_live_quote(notes) is None
    )


def _chat_truth_live_info_result(
    agent: Any,
    *,
    session_id: str,
    user_input: str,
    query: str,
    live_mode: str,
    notes: list[dict[str, Any]],
    source_context: dict[str, object] | None,
    interpretation: Any,
    response_class: Any,
    response: str,
) -> dict[str, Any]:
    return agent._chat_surface_model_wording_result(
        session_id=session_id,
        user_input=user_input,
        source_context=source_context,
        persona=load_active_persona(agent.persona_id),
        interpretation=interpretation,
        task_class="research",
        response_class=response_class,
        reason="live_info_model_wording",
        model_input=agent._chat_surface_live_info_model_input(
            user_input=user_input,
            query=query,
            mode=live_mode,
            notes=notes,
            runtime_note="" if notes else response,
        ),
        fallback_response=(
            "I pulled live evidence for this turn, but I couldn't produce a clean final synthesis in this run."
            if notes
            else response
        ),
        tool_backing_sources=["web_lookup"] if notes else [],
    )
