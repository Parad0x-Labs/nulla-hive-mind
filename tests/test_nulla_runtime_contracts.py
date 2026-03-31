from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest

from apps.nulla_agent import ChatTurnResult, ResponseClass
from core.human_input_adapter import adapt_user_input
from core.identity_manager import Persona, render_with_persona
from core.memory_first_router import ModelExecutionDecision
from core.persistent_memory import append_conversation_event


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


def test_utility_time_in_vilnius_binds_real_value_and_never_leaks_placeholder(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "what time is now in Vilnius?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in result["response"].lower()
    assert "[time]" not in result["response"].lower()
    assert "vilnius" in result["response"].lower()
    assert result["response"].count(":") >= 1


def test_malformed_vilnius_time_followup_recovers_to_bound_utility_answer(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "what time now vilnius",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "conversation_history": [
                {"role": "assistant", "content": "Ask me for the current time if you need it."}
            ],
        },
    )

    lowered = result["response"].lower()
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in lowered
    assert "[time]" not in lowered
    assert "i can help think, research, write code" not in lowered


def test_what_wheres_is_in_vilnius_recovers_from_recent_time_context(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "what where's is in Vilnius?",
        session_id_override="openclaw:vilnius-where-followup",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "conversation_history": [
                {"role": "user", "content": "what time is now in Vilnius?"},
                {"role": "assistant", "content": "Current time in Vilnius is 12:32 EET."},
            ],
        },
    )

    lowered = result["response"].lower()
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in lowered
    assert "[time]" not in lowered
    assert "i can help think, research, write code" not in lowered


def test_short_vilnius_time_followup_reuses_recent_time_context(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    first = agent.run_once(
        "what time is now in Vilnius?",
        session_id_override="openclaw:vilnius-short-followup",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    second = agent.run_once(
        "and there?",
        session_id_override="openclaw:vilnius-short-followup",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert first["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert second["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in second["response"].lower()
    assert "[time]" not in second["response"].lower()
    assert "i can help think, research, write code" not in second["response"].lower()


def test_exact_vilnius_malformed_followup_reuses_session_time_context(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]
    session_id = "openclaw:vilnius-exact-followup"

    first = agent.run_once(
        "what time is now in Vilnius?",
        session_id_override=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    second = agent.run_once(
        "what where's is in Vilnius?",
        session_id_override=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert first["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert second["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in second["response"].lower()
    assert "[time]" not in second["response"].lower()
    assert "weather" not in second["response"].lower()


def test_direct_math_overrides_stale_toly_context(make_agent):
    session_id = "openclaw:math-after-toly"
    adapt_user_input("who is Toly in Solana?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="who is Toly in Solana?",
        assistant_output="Toly is Anatoly Yakovenko, one of Solana's co-founders.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("direct math should bypass stale conversational context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "17 * 19",
        session_id_override=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["response"] == "17 * 19 = 323."
    assert "toly" not in result["response"].lower()


def test_startup_sequence_stays_deterministic(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("startup fast path should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "A new session was started via /new or /reset. Execute your Session Startup sequence now - read the required files before responding to the user.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.SMALLTALK.value
    assert result["model_execution"]["used_model"] is False
    assert "new session is clean" in result["response"].lower()


@pytest.mark.parametrize(
    ("prompt", "provider_text", "expected"),
    [
        ("ohmy gad yu not a dumbs anymore?!", "Better than before, yes. The conversation lane still needs work.", "better than before"),
        ("you sound weird", "The routing is still too stitched together.", "routing is still too stitched together"),
        ("why are you acting like this", "The routing is still too stitched together.", "routing is still too stitched together"),
    ],
)
def test_evaluative_turns_stay_conversational_and_do_not_carry_hive_footer(
    make_agent,
    prompt,
    provider_text,
    expected,
):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash=f"evaluative-{prompt}",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=provider_text,
            confidence=0.84,
            trust_score=0.84,
        )
    )
    result = agent.run_once(
        prompt,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.GENERIC_CONVERSATION.value
    assert expected in result["response"].lower()
    assert "hive:" not in result["response"].lower()
    assert result["model_execution"]["used_model"] is False


@pytest.mark.parametrize(
    ("prompt", "expected_snippet"),
    [
        ("hey", "what do you need"),
        ("hello", "what do you need"),
        ("how are you", "running clean. what do you need"),
    ],
)
def test_chat_surface_smalltalk_uses_bounded_fast_path_on_openclaw_surface(make_agent, prompt, expected_snippet):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("trivial smalltalk should not hit model wording"))  # type: ignore[assignment]

    result = agent.run_once(
        prompt,
        session_id_override=f"openclaw:{prompt}",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert expected_snippet in result["response"].lower()
    assert result["model_execution"]["used_model"] is False
    assert result["model_execution"]["source"] == "fast_path"
    assert agent.memory_router.resolve.call_count == 0


def test_chat_surface_help_uses_deterministic_fast_path(make_agent):
    agent = make_agent()
    agent._help_capabilities_text = mock.Mock(return_value="deterministic help lane")  # type: ignore[method-assign]
    agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("help should stay on deterministic fast path"))  # type: ignore[assignment]

    result = agent.run_once(
        "help",
        session_id_override="openclaw:help-fast-path",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response"] == "deterministic help lane"
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert result["model_execution"]["source"] == "fast_path"


def test_repeated_chat_greetings_on_openclaw_surface_use_bounded_fast_path(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("trivial smalltalk should stay on bounded fast path"))  # type: ignore[assignment]

    first = agent.run_once(
        "hey",
        session_id_override="openclaw:greeting-loop",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    second = agent.run_once(
        "yo",
        session_id_override="openclaw:greeting-loop",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    third = agent.run_once(
        "hello",
        session_id_override="openclaw:greeting-loop",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert "what do you need" in first["response"].lower()
    assert "what needs fixing" in second["response"].lower()
    assert "skip the greeting" not in third["response"].lower()
    assert first["model_execution"]["used_model"] is False
    assert second["model_execution"]["used_model"] is False
    assert third["model_execution"]["used_model"] is False


def test_api_surface_smalltalk_uses_bounded_fast_path(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("api smalltalk should not hit model wording"))  # type: ignore[assignment]

    result = agent.run_once(
        "hey",
        session_id_override="openclaw:api-hey",
        source_context={"surface": "api", "platform": "api"},
    )

    assert "what do you need" in result["response"].lower()
    assert result["model_execution"]["used_model"] is False
    assert result["model_execution"]["source"] == "fast_path"


def test_low_verbosity_persona_wrapper_does_not_clip_to_first_paragraph():
    persona = Persona(
        persona_id="default",
        display_name="NULLA",
        spirit_anchor="anchor",
        tone="direct",
        verbosity="low",
        risk_tolerance=0.2,
        explanation_depth=0.4,
        execution_style="direct",
        strictness=0.5,
    )

    rendered = render_with_persona(
        "First paragraph stays.\n\nSecond paragraph still shows up.",
        persona,
    )

    assert rendered == "First paragraph stays.\n\nSecond paragraph still shows up."


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


def test_sanitization_contract_rewrites_botty_live_fallback(make_agent):
    agent = make_agent()
    result = ChatTurnResult(
        text="I pulled live evidence for this turn, but I couldn't produce a clean final synthesis in this run.",
        response_class=ResponseClass.UTILITY_ANSWER,
    )

    shaped = agent._shape_user_facing_text(result)

    assert shaped == "I checked, but I couldn't ground a confident answer from the evidence I found."
    assert "clean final synthesis" not in shaped.lower()


def test_sanitization_contract_rewrites_botty_conversation_fallback(make_agent):
    agent = make_agent()
    result = ChatTurnResult(
        text="I couldn't produce a grounded conversational reply in this run.",
        response_class=ResponseClass.GENERIC_CONVERSATION,
    )

    shaped = agent._shape_user_facing_text(result)

    assert shaped == "I couldn't answer that cleanly. Ask it another way."
    assert "grounded conversational reply" not in shaped.lower()


def test_sanitization_contract_strips_generic_planner_scaffold(make_agent):
    agent = make_agent()
    result = ChatTurnResult(
        text="review problem\n- choose safe next step\n- validate result",
        response_class=ResponseClass.GENERIC_CONVERSATION,
    )

    shaped = agent._shape_user_facing_text(result)

    assert shaped == "I'm here and ready to help. What do you want to do?"
    assert "review problem" not in shaped.lower()
    assert "choose safe next step" not in shaped.lower()
    assert "validate result" not in shaped.lower()


def test_sanitization_contract_strips_generic_planner_scaffold_from_wrapped_json_payload(make_agent):
    agent = make_agent()
    result = ChatTurnResult(
        text='{"summary":"review problem","bullets":["choose safe next step","validate result"]}',
        response_class=ResponseClass.GENERIC_CONVERSATION,
    )

    shaped = agent._shape_user_facing_text(result)

    assert shaped == "I'm here and ready to help. What do you want to do?"
    assert "review problem" not in shaped.lower()
    assert "choose safe next step" not in shaped.lower()
    assert "validate result" not in shaped.lower()


def test_step_by_step_prompt_still_strips_planner_wrapper_from_chat_surface(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="step-by-step-wrapper",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Claim the task, post progress, then deliver the result.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    with mock.patch(
        "apps.nulla_agent.render_response",
        return_value="Here's what I'd suggest:\n\n- claim the task\n- post progress\n- deliver the result",
    ), mock.patch(
        "apps.nulla_agent.classify",
        return_value={"task_class": "system_design", "risk_flags": [], "confidence_hint": 0.74},
    ), mock.patch("apps.nulla_agent.ingest_media_evidence", return_value=[]), mock.patch(
        "apps.nulla_agent.orchestrate_parent_task", return_value=None
    ), mock.patch("apps.nulla_agent.request_relevant_holders", return_value=[]), mock.patch(
        "apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "do all step by step",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert "here's what i'd suggest" not in lowered
    assert "claim the task" in lowered
    assert "post progress" in lowered
    assert "deliver the result" in lowered


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


def test_explicit_short_answer_request_can_still_stay_short(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="short-answer",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Yes. Use boredom as a signal.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    result = agent.run_once(
        "short answer only: is boredom useful?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response"] == "Yes. Use boredom as a signal."
    assert "\n\n" not in result["response"]


def test_capability_truth_query_reports_unwired_email_honestly(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability truth should not load context")  # type: ignore[attr-defined]
    with mock.patch.object(
        agent,
        "_capability_ledger_entries",
        return_value=[
            {
                "capability_id": "workspace.build_scaffold",
                "surface": "workspace",
                "supported": True,
                "support_level": "partial",
                "claim": "write narrow Telegram or Discord bot scaffolds into the active workspace",
                "partial_reason": "This is scaffold-level support only.",
            },
            {
                "capability_id": "operator.discord_post",
                "surface": "communication",
                "supported": False,
                "support_level": "unsupported",
                "claim": "send Discord messages through the configured bridge",
                "unsupported_reason": "Discord bridge sending is not configured on this runtime.",
            },
        ],
    ):
        result = agent.run_once(
            "can you send email from here?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "email sending is not wired on this runtime" in result["response"].lower()
    assert "draft the email text" in result["response"].lower()
    assert result["model_execution"]["used_model"] is False


def test_capability_truth_query_distinguishes_partial_support_from_full_autonomy(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability truth should not load context")  # type: ignore[attr-defined]
    with mock.patch.object(
        agent,
        "_capability_ledger_entries",
        return_value=[
            {
                "capability_id": "workspace.build_scaffold",
                "surface": "workspace",
                "supported": True,
                "support_level": "partial",
                "claim": "write narrow Telegram or Discord bot scaffolds into the active workspace",
                "partial_reason": "This is scaffold-level support only, not a full autonomous build/debug/test loop.",
            }
        ],
    ):
        result = agent.run_once(
            "can you build a full ios app end to end?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "partially" in result["response"].lower()
    assert "not a full autonomous build/debug/test loop" in result["response"].lower()


def test_capability_truth_query_distinguishes_impossible_from_unwired(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability truth should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "can you read my mind?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "outside what this runtime can actually do" in result["response"].lower()


def test_capability_truth_query_reports_self_tool_creation_limits_honestly(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability truth should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "can you create your own tools if you need one?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    lowered = result["response"].lower()
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "task-local helper files or scripts" in lowered
    assert "cannot auto-register" in lowered or "cannot register" in lowered


def test_generic_capability_inventory_prompt_uses_grounded_fast_path(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability inventory should not load context")  # type: ignore[attr-defined]

    with mock.patch.object(
        agent,
        "_capability_ledger_entries",
        return_value=[
            {
                "capability_id": "workspace.build_scaffold",
                "surface": "workspace",
                "supported": True,
                "support_level": "partial",
                "claim": "write narrow Telegram or Discord bot scaffolds into the active workspace",
                "partial_reason": "This is scaffold-level support only, not a full autonomous build/debug/test loop.",
            },
            {
                "capability_id": "workspace.write",
                "surface": "workspace",
                "supported": True,
                "support_level": "full",
                "claim": "read and write files inside the active workspace",
            },
        ],
    ):
        result = agent.run_once(
            "what can you do right now on this machine?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "wired on this runtime" in lowered
    assert "read and write files inside the active workspace" in lowered
    assert "not a full autonomous build/debug/test loop" in lowered


@pytest.mark.parametrize(
    "prompt",
    [
        "one short line only: what can you actually do on this machine right now?",
        "real quick, what can you do locally on this machine right now?",
        "in one clean line, what are your actual local powers here?",
    ],
)
def test_wrapped_capability_inventory_prompts_still_return_grounded_local_powers(make_agent, prompt):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability inventory should not load context")  # type: ignore[attr-defined]

    with mock.patch.object(
        agent,
        "_capability_ledger_entries",
        return_value=[
            {
                "capability_id": "workspace.write",
                "surface": "workspace",
                "supported": True,
                "support_level": "full",
                "claim": "read and write files inside the active workspace",
            },
            {
                "capability_id": "operator.download",
                "surface": "operator",
                "supported": True,
                "support_level": "full",
                "claim": "download files into local bounded roots",
            },
            {
                "capability_id": "sandbox.command",
                "surface": "sandbox",
                "supported": True,
                "support_level": "full",
                "claim": "run bounded local sandbox commands",
            },
        ],
    ):
        result = agent.run_once(
            prompt,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "\n" not in result["response"]
    assert "local powers here" in lowered
    assert "workspace file/folder read-write" in lowered
    assert "downloads" in lowered
    assert "sandbox commands" in lowered


def test_api_surface_greeting_falls_back_to_bounded_greeting_when_provider_is_cold(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="no_provider_available",
            task_hash="api-greeting-cold",
            confidence=0.3,
            trust_score=0.3,
            used_model=False,
        )
    )

    result = agent.run_once(
        "hello",
        source_context={"surface": "api", "platform": "api"},
    )

    lowered = result["response"].lower()
    assert result["response_class"] == ResponseClass.SMALLTALK.value
    assert result["model_execution"]["used_model"] is False
    assert "what do you need" in lowered


def test_unsupported_builder_request_reports_gap_honestly_instead_of_writing_a_brief(make_agent):
    agent = make_agent()
    result = agent.run_once(
        "build a web scraper service in this workspace and write the files",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "workspace": "/tmp/nulla-builder-gap",
        },
    )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "do not have a real bounded builder path" in result["response"].lower()
    assert "telegram or discord bot scaffold" in result["response"].lower()
