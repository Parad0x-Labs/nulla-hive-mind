from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.prompt_normalizer import normalize_prompt


def _context_result() -> SimpleNamespace:
    return SimpleNamespace(
        local_candidates=[],
        swarm_metadata=[],
        retrieval_confidence_score=0.35,
        assembled_context=lambda: "Prior note: the user prefers direct, useful answers.",
        context_snippets=lambda: [],
        report=SimpleNamespace(
            retrieval_confidence=0.35,
            total_tokens_used=lambda: 18,
            to_dict=lambda: {"external_evidence_attachments": []},
        ),
    )


def _normalize_request(
    prompt: str,
    *,
    task_class: str,
    task_kind: str,
    output_mode: str,
    history_messages: list[dict[str, str]] | None = None,
) -> SimpleNamespace:
    return normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary=prompt),
        classification={"task_class": task_class, "risk_flags": []},
        interpretation=SimpleNamespace(
            reconstructed_text=prompt,
            topic_hints=[],
            understanding_confidence=0.84,
        ),
        context_result=_context_result(),
        persona=SimpleNamespace(persona_id="default", display_name="NULLA", tone="direct"),
        output_mode=output_mode,
        task_kind=task_kind,
        trace_id="trace-1",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "conversation_history": list(history_messages or []),
        },
    )


def _to_model_request(internal_request: SimpleNamespace) -> ModelRequest:
    return ModelRequest(
        task_kind=internal_request.task_kind,
        prompt=internal_request.user_prompt(),
        system_prompt=internal_request.system_prompt(),
        context=internal_request.context_summary,
        temperature=internal_request.temperature,
        max_output_tokens=internal_request.max_output_tokens,
        messages=internal_request.as_openai_messages(),
        output_mode=internal_request.output_mode,
        trace_id=internal_request.trace_id,
        contract={"mode": internal_request.output_mode},
        metadata=internal_request.metadata,
        attachments=internal_request.attachments,
    )


@pytest.mark.parametrize(
    ("task_class", "prompt"),
    [
        ("chat_conversation", "Do you think boredom is useful, or is it mostly a signal that I need better constraints?"),
        ("business_advisory", "How should I position a B2B analytics product that keeps getting dismissed as another dashboard?"),
        ("debugging", "How do I fix this Python traceback without turning the parser into spaghetti?"),
        ("system_design", "Design a clean local Telegram bot runtime that stays debuggable."),
        ("chat_research", "Tell me about stoicism, but keep it conversational and grounded."),
    ],
)
def test_plain_text_chat_generation_profile_is_hotter_and_adaptive(task_class: str, prompt: str) -> None:
    request = _normalize_request(
        prompt,
        task_class=task_class,
        task_kind="normalization_assist",
        output_mode="plain_text",
        history_messages=[
            {"role": "assistant", "content": "Tell me what tradeoff feels most painful."},
            {"role": "user", "content": "It keeps sounding generic and replaceable."},
        ],
    )

    profile = dict(request.metadata.get("generation_profile") or {})
    chat_truth = dict(request.metadata.get("chat_truth_prompt") or {})

    assert profile["profile_id"] == "chat_plain_text"
    assert request.temperature == pytest.approx(0.72)
    assert profile["top_p"] == pytest.approx(0.92)
    assert request.max_output_tokens > 240
    assert profile["adaptive_length"] is True
    assert chat_truth["generation_profile_id"] == "chat_plain_text"
    assert chat_truth["adaptive_length"] is True


def test_chat_research_generation_profile_now_uses_same_plain_text_lane() -> None:
    research_request = _normalize_request(
        "Tell me about stoicism, but compare the main schools and where modern pop-stoicism usually distorts them.",
        task_class="chat_research",
        task_kind="normalization_assist",
        output_mode="plain_text",
        history_messages=[{"role": "assistant", "content": "Do you want the historical version or the productivity-bro version?"}],
    )

    profile = dict(research_request.metadata.get("generation_profile") or {})

    assert profile["profile_id"] == "chat_plain_text"
    assert research_request.temperature == pytest.approx(0.72)
    assert profile["top_p"] == pytest.approx(0.92)
    assert profile["adaptive_length"] is True


@pytest.mark.parametrize(
    ("output_mode", "task_kind", "expected_profile", "expected_temperature", "expected_top_p", "expected_tokens"),
    [
        ("summary_block", "summarization", "structured_response_low_temp", 0.1, 0.25, 220),
        ("action_plan", "action_plan", "planner_structured_low_temp", 0.08, 0.2, 320),
        ("tool_intent", "tool_intent", "tool_extraction_low_temp", 0.05, 0.15, 700),
    ],
)
def test_structured_generation_profiles_stay_low_temp_and_fixed(
    output_mode: str,
    task_kind: str,
    expected_profile: str,
    expected_temperature: float,
    expected_top_p: float,
    expected_tokens: int,
) -> None:
    request = _normalize_request(
        "Figure out the exact next step.",
        task_class="system_design",
        task_kind=task_kind,
        output_mode=output_mode,
    )

    profile = dict(request.metadata.get("generation_profile") or {})

    assert profile["profile_id"] == expected_profile
    assert request.temperature == pytest.approx(expected_temperature)
    assert profile["top_p"] == pytest.approx(expected_top_p)
    assert request.max_output_tokens == expected_tokens
    assert profile["adaptive_length"] is False


def test_openai_adapter_forwards_different_payloads_for_chat_and_tool_extraction() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="local:test",
            model_name="test-model",
            metadata={},
            runtime_config={
                "base_url": "http://adapter.example.test",
                "supports_json_mode": True,
                "timeout_seconds": 5.0,
            },
        )
    )
    chat_request = _to_model_request(
        _normalize_request(
            "Brainstorm a launch campaign idea for a weird soda brand with a strong point of view.",
            task_class="creative_ideation",
            task_kind="normalization_assist",
            output_mode="plain_text",
        )
    )
    tool_request = _to_model_request(
        _normalize_request(
            "Search the web for the latest OpenClaw release notes.",
            task_class="system_design",
            task_kind="tool_intent",
            output_mode="tool_intent",
        )
    )

    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post_mock:
        adapter.run_text_task(chat_request)
        adapter.run_structured_task(tool_request)

    chat_payload = dict(post_mock.call_args_list[0].kwargs["json"])
    tool_payload = dict(post_mock.call_args_list[1].kwargs["json"])

    assert chat_payload["temperature"] == pytest.approx(0.72)
    assert chat_payload["top_p"] == pytest.approx(0.92)
    assert chat_payload["max_tokens"] == chat_request.max_output_tokens
    assert "response_format" not in chat_payload

    assert tool_payload["temperature"] == pytest.approx(0.05)
    assert tool_payload["top_p"] == pytest.approx(0.15)
    assert tool_payload["max_tokens"] == tool_request.max_output_tokens
    assert tool_payload["response_format"] == {"type": "json_object"}
