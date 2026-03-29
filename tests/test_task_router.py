from __future__ import annotations

from unittest import mock

from core.task_router import (
    _classify_via_model,
    build_task_envelope_for_request,
    classify,
    evaluate_word_math_request,
    looks_like_live_recency_lookup,
    looks_like_semantic_hive_request,
)


def test_word_math_request_classifies_as_chat_conversation() -> None:
    result = classify(
        "I have 3 tasks. Task A takes 17 minutes, Task B takes twice Task A minus 4 minutes, Task C takes 11 minutes. What is the total? Show the steps."
    )

    assert result["task_class"] == "chat_conversation"


def test_evaluate_word_math_request_solves_task_duration_prompt() -> None:
    response = evaluate_word_math_request(
        "I have 3 tasks. Task A takes 17 minutes, Task B takes twice Task A minus 4 minutes, Task C takes 11 minutes. What is the total? Show the steps."
    )

    assert response is not None
    assert "Task B = 2 * 17 - 4 = 30." in response
    assert response.endswith("= 58.")


def test_live_recency_lookup_classifies_as_research() -> None:
    prompt = "What happened five minutes ago in global markets?"

    assert looks_like_live_recency_lookup(prompt) is True
    result = classify(prompt)
    assert result["task_class"] == "research"


def test_model_classifier_is_disabled_under_pytest(monkeypatch) -> None:
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_task_router.py::test_model_classifier_is_disabled_under_pytest")

    with mock.patch("requests.post", side_effect=AssertionError("model classifier should not hit network under pytest")):
        assert _classify_via_model("do you think boredom is useful?") == ""


def test_patch_and_pytest_prompt_classifies_as_debugging() -> None:
    result = classify(
        "apply this patch, then run `python3 -m pytest -q test_app.py`\n"
        "```diff\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def answer():\n"
        "-    return 41\n"
        "+    return 42\n"
        "```\n"
    )

    assert result["task_class"] == "debugging"


def test_replace_and_ruff_format_check_prompt_is_not_risky_and_classifies_as_debugging() -> None:
    result = classify(
        "replace `foo( )` with `foo()` in app.py, then run `ruff format --check app.py`"
    )

    assert result["task_class"] == "debugging"
    assert result["risk_flags"] == []


def test_absolute_workspace_path_under_nulla_hive_mind_is_not_misclassified_as_hive_request() -> None:
    prompt = (
        "Create a file named nulla_test_01.txt in "
        "/Users/test/nulla-hive-mind/artifacts/acceptance_runs/2026-03-27-fresh-proof/workspace/main "
        "with exactly this content: ALPHA-LOCAL-FILE-01"
    )

    assert looks_like_semantic_hive_request(prompt) is False
    result = classify(prompt)
    assert result["task_class"] != "integration_orchestration"


def test_model_classifier_returns_empty_when_ollama_port_is_unreachable(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")

    with mock.patch("requests.post", side_effect=AssertionError("post should not run when socket preflight fails")):
        assert _classify_via_model("do you think boredom is useful?") == ""


def test_chat_truth_surface_skips_model_classifier_for_unknown_prompt() -> None:
    prompt = "Reply with exactly GREENLOOP-WARMUP-4 and nothing else."

    with mock.patch("core.task_router._classify_via_model", side_effect=AssertionError("chat surfaces should not model-classify unknown prompts")):
        result = classify(
            prompt,
            context={
                "source_surface": "openclaw",
                "source_platform": "openclaw",
            },
        )

    assert result["task_class"] == "unknown"


def test_chat_surface_envelope_routes_unknown_prompt_without_model_classifier() -> None:
    prompt = "Reply with exactly GREENLOOP-WARMUP-5 and nothing else."

    with mock.patch("core.task_router._classify_via_model", side_effect=AssertionError("chat surface envelope should not invoke model classifier")):
        envelope = build_task_envelope_for_request(
            prompt,
            context={},
            chat_surface=True,
        )

    assert envelope.inputs["task_class"] == "chat_conversation"
    assert envelope.inputs["routing_profile"]["output_mode"] == "plain_text"
