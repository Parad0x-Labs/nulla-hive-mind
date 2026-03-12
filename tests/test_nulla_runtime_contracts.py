from __future__ import annotations

from datetime import datetime

import pytest

from apps.nulla_agent import ChatTurnResult, ResponseClass


def test_utility_day_and_date_answers_are_clean_and_footerless(make_agent, forbidden_chat_leaks):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    expected_day = datetime.now().astimezone().strftime("%A")
    day_result = agent.run_once(
        "what is the day today ?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    date_result = agent.run_once(
        "what is the date today?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert day_result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert expected_day.lower() in day_result["response"].lower()
    assert date_result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "today is" in date_result["response"].lower()
    assert "hive" not in day_result["response"].lower()
    for marker in forbidden_chat_leaks:
        assert marker not in day_result["response"].lower()
        assert marker not in date_result["response"].lower()


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("ohmy gad yu not a dumbs anymore?!", "better than before"),
        ("you sound weird", "routing is still too stitched together"),
        ("why are you acting like this", "routing is still too stitched together"),
    ],
)
def test_evaluative_turns_stay_conversational_and_do_not_carry_hive_footer(
    make_agent,
    prompt,
    expected,
):
    agent = make_agent()
    result = agent.run_once(
        prompt,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.GENERIC_CONVERSATION.value
    assert expected in result["response"].lower()
    assert "hive:" not in result["response"].lower()


def test_smalltalk_repeated_greetings_are_not_identical_dead_loops(make_agent):
    agent = make_agent()

    first = agent._smalltalk_fast_path("hey", source_surface="openclaw", session_id="openclaw:greeting-loop")
    second = agent._smalltalk_fast_path("yo", source_surface="openclaw", session_id="openclaw:greeting-loop")
    third = agent._smalltalk_fast_path("hello", source_surface="openclaw", session_id="openclaw:greeting-loop")

    assert first and second and third
    assert first != second != third
    assert "what do you want me to do" in second.lower()
    assert "skip the greeting" in third.lower()


def test_sanitization_contract_strips_runtime_preamble_and_forbidden_tool_garbage(make_agent):
    agent = make_agent()
    text = (
        "Real steps completed:\n"
        "- workspace.search_text\n\n"
        "I won't fake it: the model returned an invalid tool payload with no intent name."
    )
    result = ChatTurnResult(text=text, response_class=ResponseClass.TASK_FAILED_USER_SAFE)

    shaped = agent._shape_user_facing_text(result)

    assert shaped == "I couldn't map that cleanly to a real action."


def test_footer_policy_only_allows_selection_and_approval(make_agent):
    agent = make_agent()
    source_context = {"surface": "openclaw", "platform": "openclaw"}

    assert not agent._should_attach_hive_footer(
        ChatTurnResult(text="Today is Thursday, 2026-03-12.", response_class=ResponseClass.UTILITY_ANSWER),
        source_context=source_context,
    )
    assert not agent._should_attach_hive_footer(
        ChatTurnResult(text="Available Hive tasks right now...", response_class=ResponseClass.TASK_LIST),
        source_context=source_context,
    )
    assert agent._should_attach_hive_footer(
        ChatTurnResult(text="Pick one by name or short `#id`.", response_class=ResponseClass.TASK_SELECTION_CLARIFICATION),
        source_context=source_context,
    )
    assert agent._should_attach_hive_footer(
        ChatTurnResult(text="Approval required before file write.", response_class=ResponseClass.APPROVAL_REQUIRED),
        source_context=source_context,
    )
