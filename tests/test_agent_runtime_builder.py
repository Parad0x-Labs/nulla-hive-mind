from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.nulla_agent import NullaAgent
from core.agent_runtime.builder import scaffolds, support


def _build_agent() -> NullaAgent:
    return NullaAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_workspace_build_target_uses_app_level_root_and_heuristic_sources() -> None:
    agent = _build_agent()
    interpretation = SimpleNamespace(topic_hints=["discord bot"])

    with mock.patch.object(agent, "_extract_requested_builder_root", return_value="sandbox/discord-bot") as extract_root, mock.patch(
        "apps.nulla_agent.search_user_heuristics",
        return_value=[{"category": "preferred_stack", "signal": "typescript"}],
    ) as search_user_heuristics_mock:
        target = agent._workspace_build_target(
            query_text="build a discord bot for this workspace",
            interpretation=interpretation,
        )

    extract_root.assert_called_once_with("build a discord bot for this workspace")
    search_user_heuristics_mock.assert_called_once()
    assert target == {
        "platform": "discord",
        "language": "typescript",
        "root_dir": "sandbox/discord-bot",
    }


def test_builder_controller_profile_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.builder.support.controller_profile",
        return_value={"should_handle": True, "mode": "scaffold"},
    ) as controller_profile:
        result = agent._builder_controller_profile(
            effective_input="build a discord bot",
            classification={"task_class": "system_design"},
            interpretation=SimpleNamespace(topic_hints=[]),
            source_context={"workspace": "/tmp/test-builder"},
        )

    assert result == {"should_handle": True, "mode": "scaffold"}
    controller_profile.assert_called_once_with(
        agent,
        effective_input="build a discord bot",
        classification={"task_class": "system_design"},
        interpretation=mock.ANY,
        source_context={"workspace": "/tmp/test-builder"},
        plan_tool_workflow_fn=mock.ANY,
        looks_like_workspace_bootstrap_request_fn=mock.ANY,
    )


def test_workspace_build_file_map_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    target = {"platform": "telegram", "language": "python", "root_dir": "generated/telegram-bot"}
    web_notes = [{"result_title": "Telegram docs", "result_url": "https://core.telegram.org"}]

    assert agent._workspace_build_file_map(
        target=target,
        user_request="build a telegram bot",
        web_notes=web_notes,
    ) == scaffolds.workspace_build_file_map(
        target=target,
        user_request="build a telegram bot",
        web_notes=web_notes,
    )


def test_builder_artifact_citation_block_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    artifacts = {
        "file_diffs": [{"path": "src/bot.py", "diff_preview": "print('hello world')"}],
        "command_outputs": [{"command": "python3 -m compileall -q generated/telegram-bot/src", "returncode": 0}],
        "failures": [{"summary": "initial compile failed on missing import"}],
        "retry_history": [{"command": "python3 -m compileall -q generated/telegram-bot/src", "attempts": 2}],
        "stop_reason": "command_stop_after_success",
    }

    assert agent._builder_artifact_citation_block(artifacts) == support.artifact_citation_block(agent, artifacts)


def test_append_builder_artifact_citations_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    artifacts = {"file_diffs": [{"path": "src/main.py", "diff_preview": "print('ready')"}], "stop_reason": "builder_complete"}

    assert agent._append_builder_artifact_citations("done", artifacts=artifacts) == support.append_artifact_citations(
        agent,
        "done",
        artifacts=artifacts,
    )
