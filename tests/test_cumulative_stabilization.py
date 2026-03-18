from __future__ import annotations

from datetime import datetime, timezone

from ops.cumulative_stabilization import (
    SMOKE_PACKS,
    build_gate_steps,
    cleanup_verification_terms,
    cumulative_targets,
    is_live_smoke_tag,
    make_live_smoke_tag,
    pack_sequence_through,
    validate_live_quote_payload,
)


def test_pack_sequence_through_c_is_cumulative() -> None:
    assert pack_sequence_through("C") == ("A", "B", "C")


def test_cumulative_targets_deduplicate_shared_files() -> None:
    targets = cumulative_targets(("A", "B"))
    assert "tests/test_openclaw_tooling_context.py" in targets
    assert targets.count("tests/test_openclaw_tooling_context.py") == 1


def test_make_live_smoke_tag_is_detectable_and_cleanup_terms_preserve_exact_token() -> None:
    tag = make_live_smoke_tag(
        "B",
        label="public-hive-task",
        now=datetime(2026, 3, 17, 18, 45, tzinfo=timezone.utc),
        entropy="abc12345",
    )

    assert tag == "[NULLA_SMOKE:B:public-hive-task:20260317T184500Z:abc12345]"
    assert is_live_smoke_tag(tag)
    assert cleanup_verification_terms(tag) == (
        "[NULLA_SMOKE:B:public-hive-task:20260317T184500Z:abc12345]",
        "NULLA_SMOKE:B:public-hive-task:20260317T184500Z:abc12345",
    )


def test_validate_live_quote_payload_requires_grounded_quote_fields() -> None:
    ok, reason = validate_live_quote_payload(
        {
            "asset_name": "Brent crude",
            "value": 102.36,
            "currency": "USD",
            "as_of": "2026-03-17 16:36 UTC",
            "source_label": "Yahoo Finance",
            "source_url": "https://finance.yahoo.com/quote/BZ=F",
        }
    )
    assert ok is True
    assert reason == "ok"

    missing_ok, missing_reason = validate_live_quote_payload({"asset_name": "Brent crude"})
    assert missing_ok is False
    assert "missing required live quote fields" in missing_reason


def test_build_gate_steps_for_b_contains_targeted_then_cumulative_then_full() -> None:
    steps = build_gate_steps("B")

    assert [step.label for step in steps] == [
        "B targeted (hive_task_lifecycle)",
        "A+B cumulative packs",
        "full pytest",
    ]
    assert steps[0].command[:2] == ("pytest", "-q")
    assert steps[0].command[2:] == SMOKE_PACKS["B"].targets
    assert "tests/test_nulla_hive_task_flow.py" in steps[1].command
    assert "tests/test_web_research_runtime.py" in steps[1].command
    assert steps[2].command == ("pytest", "-q")
