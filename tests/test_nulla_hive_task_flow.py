from __future__ import annotations

from unittest import mock

from core.autonomous_topic_research import AutonomousResearchResult


def test_show_open_hive_tasks_returns_real_list_not_fake_planner_sludge(make_agent):
    agent = make_agent()
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.maybe_handle_command.return_value = (
        True,
        "Available Hive tasks right now (2 total):\n"
        "- [open] OpenClaw integration audit (#7d33994f)\n"
        "- [researching] Hive footer cleanup (#ada43859)\n"
        "If you want, I can start one. Just point at the task name or short `#id`.",
    )
    agent.hive_activity_tracker.build_chat_footer.return_value = ""

    result = agent.run_once(
        "show me the open hive tasks",
        session_id_override="openclaw:hive-list",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    lowered = result["response"].lower()
    assert result["response_class"] == "task_list"
    assert "available hive tasks right now" in lowered
    assert "review problem" not in lowered
    assert "choose safe next step" not in lowered
    assert "validate result" not in lowered


def test_show_open_hive_tasks_is_not_misread_as_topic_create(make_agent):
    agent = make_agent()

    assert agent._extract_hive_topic_create_draft("show me the open hive tasks") is None


def test_short_yes_followup_reuses_last_shown_task_and_starts(make_agent):
    agent = make_agent()
    queue_rows = [
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        }
    ]
    hive_state = {
        "pending_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"],
            "shown_titles": ["Agent Commons: better human-visible watcher and task-flow UX"],
        },
    }

    with mock.patch("apps.nulla_agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "apps.nulla_agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            claim_id="claim-12345678",
        ),
    ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "yes",
            session_id_override="openclaw:hive-short-followup",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "task_started"
    assert "started hive research on" in result["response"].lower()
    assert "packed" not in result["response"].lower()
    selected_signal = research_topic_from_signal.call_args.args[0]
    assert selected_signal["topic_id"] == "7d33994f-dd40-4a7e-b78a-f8e2d94fb702"


def test_start_short_id_uses_assistant_style_summary(make_agent):
    agent = make_agent()
    queue_rows = [
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        }
    ]

    with mock.patch("apps.nulla_agent.session_hive_state", return_value={"pending_topic_ids": []}), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "apps.nulla_agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            claim_id="claim-12345678",
        ),
    ), mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "start #7d33994f",
            session_id_override="openclaw:hive-short-id",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert result["response_class"] == "task_started"
    assert "started hive research on" in lowered
    assert "packed 3 research queries" not in lowered
    assert "bounded queries run" not in lowered
    assert "candidate notes" not in lowered


def test_review_the_problem_clarifies_when_multiple_tasks_are_open(make_agent):
    agent = make_agent()
    queue_rows = [
        {
            "topic_id": "topic-1-aaaaaaaa",
            "title": "OpenClaw integration audit",
            "status": "open",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        },
        {
            "topic_id": "topic-2-bbbbbbbb",
            "title": "Hive footer cleanup",
            "status": "researching",
            "research_priority": 0.8,
            "active_claim_count": 0,
            "claims": [],
        },
    ]
    hive_state = {
        "pending_topic_ids": ["topic-1-aaaaaaaa", "topic-2-bbbbbbbb"],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": ["topic-1-aaaaaaaa", "topic-2-bbbbbbbb"],
            "shown_titles": ["OpenClaw integration audit", "Hive footer cleanup"],
        },
    }

    with mock.patch("apps.nulla_agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch("apps.nulla_agent.research_topic_from_signal") as research_topic_from_signal:
        result = agent.run_once(
            "review the problem",
            session_id_override="openclaw:hive-ambiguous",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "task_selection_clarification"
    assert "pick one by name or short `#id`" in result["response"].lower()
    research_topic_from_signal.assert_not_called()


def test_hive_status_followup_reports_clean_status_text(make_agent):
    agent = make_agent()
    hive_state = {
        "watched_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"],
        "interaction_payload": {"active_topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702"},
    }
    packet = {
        "topic": {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
        },
        "execution_state": {
            "execution_state": "claimed",
            "active_claim_count": 1,
            "artifact_count": 2,
        },
        "counts": {"post_count": 1, "active_claim_count": 1},
        "posts": [{"post_kind": "result", "body": "First bounded pass landed."}],
    }

    with mock.patch("apps.nulla_agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "get_public_research_packet", return_value=packet
    ):
        result = agent.run_once(
            "what is the status",
            session_id_override="openclaw:hive-status",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert result["response_class"] == "task_status"
    assert "is still `researching`" in lowered
    assert "active claims: 1." in lowered
    assert "latest result: first bounded pass landed." in lowered
