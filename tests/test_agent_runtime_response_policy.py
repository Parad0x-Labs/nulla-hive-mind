from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.nulla_agent import ChatTurnResult, NullaAgent, ResponseClass
from core.agent_runtime import (
    response_policy,
    response_policy_classification,
    response_policy_tool_history,
    response_policy_visibility,
)


def test_fast_path_response_class_facade_matches_extracted_policy() -> None:
    agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    assert agent._fast_path_response_class(
        reason="hive_research_followup",
        response="Research result: 1 new local research result landed.",
    ) == response_policy.fast_path_response_class(
        agent,
        reason="hive_research_followup",
        response="Research result: 1 new local research result landed.",
    )
    assert response_policy.fast_path_response_class is response_policy_classification.fast_path_response_class


def test_action_response_class_keeps_non_research_tool_intents_as_generic_conversation() -> None:
    agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    assert response_policy.action_response_class(
        agent,
        reason="model_tool_intent_multi_step_executed",
        success=True,
        task_outcome="success",
        response="Git repo summary for `/tmp/repo`:\n- visible branches: 7 total (5 local, 2 remote tracking)",
    ) == ResponseClass.GENERIC_CONVERSATION


def test_action_response_class_preserves_research_progress_for_explicit_research_result_text() -> None:
    agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    assert response_policy.action_response_class(
        agent,
        reason="model_tool_intent_multi_step_executed",
        success=True,
        task_outcome="success",
        response="Research result: 1 new local research result landed.",
    ) == ResponseClass.RESEARCH_PROGRESS


def test_should_attach_hive_footer_facade_matches_extracted_policy() -> None:
    agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    result = ChatTurnResult(
        text="Approval required before file write.",
        response_class=ResponseClass.APPROVAL_REQUIRED,
    )

    assert agent._should_attach_hive_footer(
        result,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    ) == response_policy.should_attach_hive_footer(
        agent,
        result,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    assert response_policy.should_attach_hive_footer is response_policy_visibility.should_attach_hive_footer


def test_normalize_tool_history_message_facade_matches_extracted_policy() -> None:
    agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    item = {
        "role": "assistant",
        "content": (
            "Real tool result from `workspace.search_text`:\n"
            'Search matches for "tool_intent":\n'
            "- core/tool_intent_executor.py:42 def execute_tool_intent("
        ),
    }

    assert agent._normalize_tool_history_message(item) == response_policy.normalize_tool_history_message(agent, item)
    assert response_policy.normalize_tool_history_message is response_policy_tool_history.normalize_tool_history_message


def test_append_tool_result_to_source_context_dedupes_observation_messages() -> None:
    agent = NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    execution = SimpleNamespace(
        details={"observation": {"schema": "tool_observation_v1", "intent": "workspace.search_text", "tool_surface": "workspace"}},
        response_text="Search matches for tool_intent",
        ok=True,
        status="executed",
        mode="tool_executed",
        tool_name="workspace.search_text",
    )

    first = agent._append_tool_result_to_source_context(
        {"conversation_history": []},
        execution=execution,
        tool_name="workspace.search_text",
    )
    second = agent._append_tool_result_to_source_context(
        first,
        execution=execution,
        tool_name="workspace.search_text",
    )

    history = list(second.get("conversation_history") or [])
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert "Grounding observations for this turn" in history[0]["content"]


def test_tool_intent_direct_message_reads_explicit_direct_response() -> None:
    assert response_policy.tool_intent_direct_message(
        {"intent": "respond.direct", "arguments": {"message": "Use the local proof receipt."}},
    ) == "Use the local proof receipt."


def test_maybe_attach_workflow_keeps_openclaw_clean_without_debug_flag() -> None:
    with mock.patch(
        "core.agent_runtime.response_policy_visibility.load_preferences",
        return_value=SimpleNamespace(show_workflow=True),
    ):
        attached = response_policy.maybe_attach_workflow(
            None,
            "Patched the failing test and reran the pack.",
            "- recognized operator action `workspace.apply_unified_diff`\n- execution posture: `tool_executed`",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert attached == "Patched the failing test and reran the pack."


def test_maybe_attach_workflow_allows_explicit_debug_on_openclaw() -> None:
    with mock.patch(
        "core.agent_runtime.response_policy_visibility.load_preferences",
        return_value=SimpleNamespace(show_workflow=True),
    ):
        attached = response_policy.maybe_attach_workflow(
            None,
            "Patched the failing test and reran the pack.",
            "- recognized operator action `workspace.apply_unified_diff`\n- execution posture: `tool_executed`",
            source_context={"surface": "openclaw", "platform": "openclaw", "workflow_debug": True},
        )

    assert attached.startswith("Workflow:\n")
    assert "Patched the failing test and reran the pack." in attached
