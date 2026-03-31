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


def test_orchestration_failure_text_is_humanized_for_user_reply() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                "coder envelope `coder-1` is not allowed to run `workspace.write_file` because it lacks "
                'capability `workspace.write`.\n\n{"task_envelope":{"task_id":"coder-1","tool_permissions":["workspace.read"]}}'
            ),
            response_class=ResponseClass.TASK_FAILED_USER_SAFE,
        ),
        session_id="openclaw:orchestration-failure",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert "permissions did not allow the requested action" in decorated.lower()
    assert "coder envelope" not in decorated.lower()
    assert "task_envelope" not in decorated.lower()
    assert "workspace.write_file" not in decorated.lower()


def test_orchestration_success_text_is_humanized_for_user_reply() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text="queen envelope `queen-1` completed merge.",
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:orchestration-success",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert decorated == "I finished the bounded multi-step run."


def test_capacity_blocked_text_is_humanized_for_user_reply() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                "coder envelope `coder-remote-lane` is blocked by provider-capacity policy: "
                "requires_local_provider.\n\n"
                '{"capacity_state":{"availability_state":"blocked","notes":["requires_local_provider"]}}'
            ),
            response_class=ResponseClass.TASK_FAILED_USER_SAFE,
        ),
        session_id="openclaw:capacity-blocked",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert "local execution requirements" in decorated.lower()
    assert "capacity_state" not in decorated.lower()
    assert "requires_local_provider" not in decorated.lower()


def test_routing_payload_is_stripped_from_generic_user_reply() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                '{"selection_notes":["Queue-depth pressure was applied while scoring provider candidates."],'
                '"rejected_candidates":[{"provider_id":"kimi:k2","reason":"requires_local_provider"}],'
                '"routing_requirements":{"required_locality":"local"}}'
            ),
            response_class=ResponseClass.GENERIC_CONVERSATION,
        ),
        session_id="openclaw:routing-payload",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert decorated == "I finished the work and stripped the internal routing details from the reply."


def test_grounded_workspace_search_result_is_not_mistaken_for_routing_leak() -> None:
    agent = _build_agent()

    decorated = agent._decorate_chat_response(
        ChatTurnResult(
            text=(
                'Search matches for "provider_capability_truth":\n'
                "- notes.md:1 provider_capability_truth is documented here"
            ),
            response_class=ResponseClass.UTILITY_ANSWER,
        ),
        session_id="openclaw:workspace-search-provider-capability-truth",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert decorated.startswith('Search matches for "provider_capability_truth"')
    assert "stripped the internal routing details" not in decorated.lower()
