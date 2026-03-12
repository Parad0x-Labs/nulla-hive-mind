from __future__ import annotations

from unittest import mock

from apps.nulla_agent import ChatTurnResult, ResponseClass
from core.autonomous_topic_research import AutonomousResearchResult
from core.curiosity_roamer import CuriosityResult
from core.hive_activity_tracker import prune_stale_hive_interaction_state, session_hive_state, update_session_hive_state
from core.memory_first_router import ModelExecutionDecision


def test_utility_turn_preserves_pending_hive_selection_context(make_agent):
    agent = make_agent()
    session_id = "openclaw:preserve-hive-selection"
    update_session_hive_state(
        session_id,
        watched_topic_ids=[],
        seen_post_ids=[],
        pending_topic_ids=["topic-1"],
        seen_curiosity_topic_ids=[],
        seen_curiosity_run_ids=[],
        seen_agent_ids=[],
        last_active_agents=0,
        interaction_mode="hive_task_selection_pending",
        interaction_payload={"shown_topic_ids": ["topic-1"], "shown_titles": ["OpenClaw integration audit"]},
    )
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "what is the date today?",
        session_id_override=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    state = session_hive_state(session_id)
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert state["interaction_mode"] == "hive_task_selection_pending"
    assert state["interaction_payload"]["shown_topic_ids"] == ["topic-1"]


def test_interaction_transition_modes_are_centralized(make_agent):
    agent = make_agent()
    session_id = "openclaw:transition-contract"

    agent._apply_interaction_transition(
        session_id,
        ChatTurnResult(text="Pick one by name.", response_class=ResponseClass.TASK_SELECTION_CLARIFICATION),
    )
    assert session_hive_state(session_id)["interaction_mode"] == "hive_task_selection_pending"

    agent._apply_interaction_transition(
        session_id,
        ChatTurnResult(text="Started Hive research.", response_class=ResponseClass.TASK_STARTED),
    )
    assert session_hive_state(session_id)["interaction_mode"] == "hive_task_active"

    agent._apply_interaction_transition(
        session_id,
        ChatTurnResult(text="I couldn't map that cleanly.", response_class=ResponseClass.TASK_FAILED_USER_SAFE),
    )
    assert session_hive_state(session_id)["interaction_mode"] == "error_recovery"


def test_stale_hive_selection_state_expires_cleanly():
    session_id = "openclaw:state-expiry"
    update_session_hive_state(
        session_id,
        watched_topic_ids=[],
        seen_post_ids=[],
        pending_topic_ids=["topic-1"],
        seen_curiosity_topic_ids=[],
        seen_curiosity_run_ids=[],
        seen_agent_ids=[],
        last_active_agents=0,
        interaction_mode="hive_task_selection_pending",
        interaction_payload={"shown_topic_ids": ["topic-1"], "shown_titles": ["OpenClaw integration audit"]},
    )
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE session_hive_watch_state SET updated_at = '2026-03-10T10:00:00+00:00' WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
    finally:
        conn.close()

    state = prune_stale_hive_interaction_state(session_id)
    assert state["interaction_mode"] == ""
    assert state["interaction_payload"] == {}
    assert state["pending_topic_ids"] == []


def test_ambiguous_hive_followup_clarifies_instead_of_silent_guess(make_agent):
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
    ), mock.patch(
        "apps.nulla_agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(ok=True, status="completed", topic_id="topic-1-aaaaaaaa"),
    ) as research_topic_from_signal:
        result = agent.run_once(
            "review the problem",
            session_id_override="openclaw:ambiguous-followup",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == ResponseClass.TASK_SELECTION_CLARIFICATION.value
    assert "pick one by name or short `#id`" in result["response"].lower()
    research_topic_from_signal.assert_not_called()


def test_fresh_web_query_beats_evaluative_detection(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(source="memory_hit", task_hash="fresh-web", used_model=False)
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "apps.nulla_agent.WebAdapter.planned_search_query",
        return_value=[
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
                "confidence": 0.66,
            }
        ],
    ) as planned_search, mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
        "apps.nulla_agent.request_relevant_holders", return_value=[]
    ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "latest telegram bot api updates",
            source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert planned_search.called
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "canonical source" in result["response"].lower()


from storage.db import get_connection
