from __future__ import annotations

from unittest import mock

from apps.nulla_agent import ChatTurnResult, NullaAgent, ResponseClass
from core.agent_runtime import response


def _build_agent() -> NullaAgent:
    return NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_turn_result_facade_matches_extracted_module() -> None:
    agent = _build_agent()

    result = agent._turn_result(
        "  hello world  ",
        ResponseClass.GENERIC_CONVERSATION,
        workflow_summary=" step one ",
        debug_origin="test",
        allow_planner_style=True,
    )

    assert result == response.turn_result(
        ChatTurnResult,
        "  hello world  ",
        ResponseClass.GENERIC_CONVERSATION,
        workflow_summary=" step one ",
        debug_origin="test",
        allow_planner_style=True,
    )


def test_shape_user_facing_text_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()
    turn = ChatTurnResult(text="raw reply", response_class=ResponseClass.GENERIC_CONVERSATION)

    with mock.patch(
        "core.agent_runtime.response.shape_user_facing_text",
        return_value="delegated text",
    ) as shape_user_facing_text:
        result = agent._shape_user_facing_text(turn)

    assert result == "delegated text"
    shape_user_facing_text.assert_called_once_with(agent, turn)


def test_decorate_chat_response_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()
    turn = ChatTurnResult(text="raw reply", response_class=ResponseClass.GENERIC_CONVERSATION)

    with mock.patch(
        "core.agent_runtime.response.decorate_chat_response",
        return_value="decorated text",
    ) as decorate_chat_response:
        result = agent._decorate_chat_response(
            turn,
            session_id="session-123",
            source_context={"surface": "openclaw"},
            workflow_summary="ignored summary",
            include_hive_footer=False,
        )

    assert result == "decorated text"
    decorate_chat_response.assert_called_once_with(
        agent,
        turn,
        session_id="session-123",
        source_context={"surface": "openclaw"},
        workflow_summary="ignored summary",
        include_hive_footer=False,
    )


def test_strip_planner_leakage_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    payload = '{"summary":"Here\'s what I’d suggest: claim the task","bullets":["post progress","deliver result"]}'

    assert agent._strip_planner_leakage(payload) == response.strip_planner_leakage(agent, payload)
