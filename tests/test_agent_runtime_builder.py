from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.nulla_agent import NullaAgent
from core.agent_runtime.builder import controller, scaffolds, support


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


def test_run_bounded_builder_loop_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.builder.controller.run_bounded_builder_loop",
        return_value=([], {"surface": "openclaw"}, "bounded_loop_complete", None),
    ) as run_bounded_builder_loop:
        result = agent._run_bounded_builder_loop(
            task=SimpleNamespace(task_id="task-builder-loop"),
            session_id="builder-loop-session",
            effective_input="build a telegram bot in this workspace",
            task_class="system_design",
            source_context={"workspace": "/tmp/test-builder"},
            initial_payloads=[{"intent": "workspace.write_file"}],
        )

    assert result == ([], {"surface": "openclaw"}, "bounded_loop_complete", None)
    run_bounded_builder_loop.assert_called_once_with(
        agent,
        task=mock.ANY,
        session_id="builder-loop-session",
        effective_input="build a telegram bot in this workspace",
        task_class="system_design",
        source_context={"workspace": "/tmp/test-builder"},
        initial_payloads=[{"intent": "workspace.write_file"}],
        plan_tool_workflow_fn=mock.ANY,
        execute_tool_intent_fn=mock.ANY,
    )


def test_maybe_run_builder_controller_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.builder.controller.maybe_run_builder_controller",
        return_value={"response": "builder-controller"},
    ) as maybe_run_builder_controller:
        result = agent._maybe_run_builder_controller(
            task=SimpleNamespace(task_id="task-builder-controller"),
            effective_input="build a discord bot in this workspace",
            classification={"task_class": "system_design"},
            interpretation=SimpleNamespace(topic_hints=[]),
            web_notes=[],
            session_id="builder-controller-session",
            source_context={"workspace": "/tmp/test-builder"},
        )

    assert result == {"response": "builder-controller"}
    maybe_run_builder_controller.assert_called_once_with(
        agent,
        task=mock.ANY,
        effective_input="build a discord bot in this workspace",
        classification={"task_class": "system_design"},
        interpretation=mock.ANY,
        web_notes=[],
        session_id="builder-controller-session",
        source_context={"workspace": "/tmp/test-builder"},
        render_capability_truth_response_fn=mock.ANY,
        load_active_persona_fn=mock.ANY,
    )


def test_maybe_run_builder_controller_uses_app_level_run_loop_override() -> None:
    agent = _build_agent()
    task = SimpleNamespace(task_id="task-builder-override")
    executed_steps = [{"tool_name": "workspace.write_file"}]
    artifacts = {"file_diffs": [{"path": "src/main.py"}], "stop_reason": "bounded_loop_complete"}

    with mock.patch.object(
        agent,
        "_builder_controller_profile",
        return_value={
            "should_handle": True,
            "supported": True,
            "mode": "workflow",
            "target": {"platform": "discord", "language": "python", "root_dir": "generated/discord-bot"},
        },
    ), mock.patch.object(
        agent,
        "_builder_initial_payloads",
        return_value=([{"intent": "workspace.write_file"}], [{"title": "Discord docs", "url": "https://discord.com"}]),
    ), mock.patch.object(
        agent,
        "_run_bounded_builder_loop",
        return_value=(executed_steps, {"surface": "openclaw"}, "bounded_loop_complete", None),
    ) as run_bounded_builder_loop, mock.patch.object(
        agent,
        "_builder_controller_artifacts",
        return_value=artifacts,
    ), mock.patch.object(
        agent,
        "_builder_controller_observations",
        return_value={"channel": "workspace_build"},
    ), mock.patch.object(
        agent,
        "_builder_controller_degraded_response",
        return_value="builder degraded",
    ), mock.patch.object(
        agent,
        "_builder_controller_workflow_summary",
        return_value="builder workflow",
    ), mock.patch.object(
        agent,
        "_builder_controller_direct_response",
        return_value="builder direct",
    ), mock.patch.object(
        agent,
        "_fast_path_result",
        return_value={"response": "builder direct"},
    ) as fast_path_result:
        result = agent._maybe_run_builder_controller(
            task=task,
            effective_input="build a discord bot in this workspace",
            classification={"task_class": "system_design"},
            interpretation=SimpleNamespace(topic_hints=[]),
            web_notes=[],
            session_id="builder-controller-session",
            source_context={"workspace": "/tmp/test-builder"},
        )

    run_bounded_builder_loop.assert_called_once()
    fast_path_result.assert_called_once()
    assert result["mode"] == "tool_executed"
    assert result["workflow_summary"] == "builder workflow"
    assert result["details"]["builder_controller"]["step_count"] == 1


def test_workspace_build_response_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    target = {"platform": "telegram", "language": "python", "root_dir": "generated/telegram-bot"}
    write_results = [{"path": "generated/telegram-bot/src/bot.py"}]
    verification = {"status": "executed", "response_text": "compileall ok"}
    sources = [{"title": "Telegram docs", "url": "https://core.telegram.org"}]

    assert agent._workspace_build_response(
        target=target,
        write_results=write_results,
        write_failures=[],
        verification=verification,
        sources=sources,
    ) == controller.workspace_build_response(
        target=target,
        write_results=write_results,
        write_failures=[],
        verification=verification,
        sources=sources,
    )


def test_builder_controller_does_not_hijack_plain_workspace_search_requests() -> None:
    agent = _build_agent()

    should_run = agent._should_run_builder_controller(
        effective_input='find a file in this workspace mentioning "runtime_capabilities"',
        classification={"task_class": "research"},
        source_context={"workspace": "/tmp/test-builder", "workspace_root": "/tmp/test-builder"},
    )

    assert should_run is False
