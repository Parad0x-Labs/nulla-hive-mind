from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any


def sync_public_presence(
    agent: Any,
    *,
    status: str,
    source_context: dict[str, object] | None = None,
    get_agent_display_name_fn: Callable[[], str],
    audit_log_fn: Callable[..., Any],
) -> None:
    effective_status = agent._normalize_public_presence_status(status)
    with agent._public_presence_lock:
        agent._public_presence_status = effective_status
        if source_context is not None:
            agent._public_presence_source_context = dict(source_context)
    try:
        if agent._public_presence_registered:
            result = agent.public_hive_bridge.heartbeat_presence(
                agent_name=get_agent_display_name_fn(),
                capabilities=agent._public_capabilities(),
                status=effective_status,
                transport_mode=agent._public_transport_mode(source_context),
            )
            if not result.get("ok"):
                result = agent.public_hive_bridge.sync_presence(
                    agent_name=get_agent_display_name_fn(),
                    capabilities=agent._public_capabilities(),
                    status=effective_status,
                    transport_mode=agent._public_transport_mode(source_context),
                )
        else:
            result = agent.public_hive_bridge.sync_presence(
                agent_name=get_agent_display_name_fn(),
                capabilities=agent._public_capabilities(),
                status=effective_status,
                transport_mode=agent._public_transport_mode(source_context),
            )
        if result.get("ok"):
            agent._public_presence_registered = True
    except Exception as exc:
        audit_log_fn(
            "public_hive_presence_sync_error",
            target_id=agent.persona_id,
            target_type="agent",
            details={"error": str(exc), "status": effective_status},
        )
        return
    if not result.get("ok"):
        audit_log_fn(
            "public_hive_presence_sync_failed",
            target_id=agent.persona_id,
            target_type="agent",
            details={"status": effective_status, **dict(result or {})},
        )


def start_public_presence_heartbeat(
    agent: Any,
    *,
    thread_factory: Callable[..., Any],
) -> None:
    if agent._public_presence_running:
        return
    agent._public_presence_running = True
    agent._public_presence_thread = thread_factory(
        target=agent._public_presence_heartbeat_loop,
        name="nulla-public-presence",
        daemon=True,
    )
    agent._public_presence_thread.start()


def start_idle_commons_loop(
    agent: Any,
    *,
    thread_factory: Callable[..., Any],
) -> None:
    if agent._idle_commons_running:
        return
    agent._idle_commons_running = True
    agent._idle_commons_thread = thread_factory(
        target=agent._idle_commons_loop,
        name="nulla-idle-commons",
        daemon=True,
    )
    agent._idle_commons_thread.start()


def public_presence_heartbeat_loop(
    agent: Any,
    *,
    sleep_fn: Callable[[float], Any],
) -> None:
    while agent._public_presence_running:
        sleep_fn(120.0)
        with agent._public_presence_lock:
            last_status = str(agent._public_presence_status or "idle")
            source_context = dict(agent._public_presence_source_context or {})
        agent._sync_public_presence(
            status=agent._normalize_public_presence_status(last_status),
            source_context=source_context,
        )


def idle_commons_loop(
    agent: Any,
    *,
    sleep_fn: Callable[[float], Any],
    audit_log_fn: Callable[..., Any],
) -> None:
    while agent._idle_commons_running:
        sleep_fn(90.0)
        try:
            agent._maybe_run_idle_commons_once()
            agent._maybe_run_autonomous_hive_research_once()
        except Exception as exc:
            audit_log_fn(
                "idle_commons_loop_error",
                target_id=agent.persona_id,
                target_type="agent",
                details={"error": str(exc)},
            )


def maybe_run_idle_commons_once(
    agent: Any,
    *,
    load_preferences_fn: Callable[[], Any],
    time_fn: Callable[[], float],
    audit_log_fn: Callable[..., Any],
) -> None:
    prefs = load_preferences_fn()
    if not bool(getattr(prefs, "social_commons", True)):
        return
    now = time_fn()
    with agent._activity_lock:
        idle_for_seconds = now - float(agent._last_user_activity_ts)
        since_last_commons = now - float(agent._last_idle_commons_ts)
        seed_index = int(agent._idle_commons_seed_index)
    if idle_for_seconds < 300.0:
        return
    if since_last_commons < 900.0:
        return

    session_id = agent._idle_commons_session_id()
    commons = agent.curiosity.run_idle_commons(
        session_id=session_id,
        task_id="agent-commons",
        trace_id="agent-commons",
        seed_index=seed_index,
    )
    publish_result: dict[str, Any] | None = None
    try:
        publish_result = agent.public_hive_bridge.publish_agent_commons_update(
            topic=str(dict(commons.get("topic") or {}).get("topic") or ""),
            topic_kind=str(dict(commons.get("topic") or {}).get("topic_kind") or "technical"),
            summary=str(commons.get("summary") or ""),
            public_body=str(commons.get("public_body") or commons.get("summary") or ""),
            topic_tags=[str(tag) for tag in list(commons.get("topic_tags") or [])[:8]],
        )
    except Exception as exc:
        audit_log_fn(
            "idle_commons_publish_error",
            target_id=session_id,
            target_type="session",
            details={"error": str(exc), "candidate_id": commons.get("candidate_id")},
        )
    if publish_result and str(publish_result.get("topic_id") or "").strip():
        agent.hive_activity_tracker.note_watched_topic(
            session_id=session_id,
            topic_id=str(publish_result.get("topic_id") or "").strip(),
        )
    with agent._activity_lock:
        agent._last_idle_commons_ts = now
        agent._idle_commons_seed_index = (seed_index + 1) % 64
    audit_log_fn(
        "idle_commons_cycle_complete",
        target_id=session_id,
        target_type="session",
        details={
            "idle_for_seconds": round(idle_for_seconds, 2),
            "candidate_id": commons.get("candidate_id"),
            "topic_id": str((publish_result or {}).get("topic_id") or ""),
            "publish_status": str((publish_result or {}).get("status") or "local_only"),
            "topic": dict(commons.get("topic") or {}).get("topic"),
        },
    )


def maybe_run_autonomous_hive_research_once(
    agent: Any,
    *,
    load_preferences_fn: Callable[[], Any],
    time_fn: Callable[[], float],
    pick_signal_fn: Callable[[list[dict[str, Any]]], dict[str, Any] | None],
    research_topic_fn: Callable[..., Any],
    audit_log_fn: Callable[..., Any],
) -> None:
    prefs = load_preferences_fn()
    if not bool(getattr(prefs, "accept_hive_tasks", True)):
        return
    if not bool(getattr(prefs, "idle_research_assist", True)):
        return
    if not agent.public_hive_bridge.enabled():
        return

    now = time_fn()
    with agent._activity_lock:
        idle_for_seconds = now - float(agent._last_user_activity_ts)
        since_last_research = now - float(agent._last_idle_hive_research_ts)
    if idle_for_seconds < 240.0:
        return
    if since_last_research < 900.0:
        return

    queue_rows = agent.public_hive_bridge.list_public_research_queue(limit=12)
    signal = pick_signal_fn(queue_rows)
    if not signal:
        return

    auto_session_id = f"auto-research:{signal.get('topic_id') or ''!s}"
    lane_context = {"surface": "background", "platform": "openclaw", "lane": "autonomous_research"}
    agent._sync_public_presence(status="busy", source_context=lane_context)
    try:
        result = research_topic_fn(
            signal,
            public_hive_bridge=agent.public_hive_bridge,
            curiosity=agent.curiosity,
            hive_activity_tracker=agent.hive_activity_tracker,
            session_id=auto_session_id,
            auto_claim=True,
        )
        audit_log_fn(
            "idle_hive_research_cycle_complete",
            target_id=str(signal.get("topic_id") or auto_session_id),
            target_type="topic",
            details=result.to_dict(),
        )
        with agent._activity_lock:
            agent._last_idle_hive_research_ts = now
        if result.ok and result.topic_id:
            with contextlib.suppress(Exception):
                agent.hive_activity_tracker.note_watched_topic(session_id=auto_session_id, topic_id=result.topic_id)
    finally:
        agent._sync_public_presence(
            status=agent._idle_public_presence_status(),
            source_context=lane_context,
        )


def idle_commons_session_id(*, get_local_peer_id_fn: Callable[[], str]) -> str:
    return f"agent-commons:{get_local_peer_id_fn()}"


def normalize_public_presence_status(agent: Any, status: str) -> str:
    lowered = str(status or "idle").strip().lower()
    if lowered == "busy":
        return "busy"
    return agent._idle_public_presence_status()


def idle_public_presence_status(*, load_preferences_fn: Callable[[], Any]) -> str:
    prefs = load_preferences_fn()
    return "idle" if bool(getattr(prefs, "accept_hive_tasks", True)) else "limited"
