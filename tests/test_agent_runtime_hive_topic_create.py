from __future__ import annotations

from core.agent_runtime import hive_topics
from core.agent_runtime.hive_topic_create import (
    maybe_handle_hive_topic_create_request,
    prepare_public_hive_topic_copy,
    shape_public_hive_admission_safe_copy,
)


def test_hive_topic_create_compat_exports_stay_available_from_hive_topics() -> None:
    assert hive_topics.maybe_handle_hive_topic_create_request is maybe_handle_hive_topic_create_request
    assert hive_topics.prepare_public_hive_topic_copy is prepare_public_hive_topic_copy


def test_shape_public_hive_admission_safe_copy_reframes_command_like_brief() -> None:
    title, summary, note = shape_public_hive_admission_safe_copy(
        title="research docker health mismatch",
        summary="tell me which route is right and what to do next",
    )

    assert title == "research docker health mismatch"
    assert "Agent analysis brief comparing architecture" in summary
    assert "docker health mismatch" in summary
    assert "Admission:" in note
