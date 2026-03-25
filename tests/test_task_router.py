from __future__ import annotations

from core.task_router import classify, evaluate_word_math_request, looks_like_live_recency_lookup


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
