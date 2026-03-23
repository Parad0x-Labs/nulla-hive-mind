from __future__ import annotations

from apps.nulla_agent import NullaAgent
from core.agent_runtime import hive_topic_create, hive_topic_public_copy


def _build_agent() -> NullaAgent:
    return NullaAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def test_hive_topic_public_copy_exports_stay_available_from_hive_topic_create() -> None:
    assert hive_topic_create.prepare_public_hive_topic_copy is hive_topic_public_copy.prepare_public_hive_topic_copy
    assert hive_topic_create.infer_hive_topic_tags is hive_topic_public_copy.infer_hive_topic_tags


def test_prepare_public_hive_topic_copy_blocks_raw_transcripts_without_structured_brief() -> None:
    agent = _build_agent()

    result = hive_topic_public_copy.prepare_public_hive_topic_copy(
        agent,
        raw_input="12:34\nU\ncan you help\n/new\nA",
        title="Research trace capture",
        summary="Dump this whole chat log to Hive",
    )

    assert result["ok"] is False
    assert result["reason"] == "hive_topic_create_transcript_blocked"
    assert "raw chat log/transcript" in str(result["response"])


def test_infer_hive_topic_tags_dedupes_and_normalizes() -> None:
    agent = _build_agent()

    tags = hive_topic_public_copy.infer_hive_topic_tags(
        agent,
        "Research OpenClaw OpenClaw UI for local OS and VM reliability",
    )

    assert tags == ["research", "openclaw", "ui", "local", "os", "vm"]
