from __future__ import annotations

import json
import os
import re
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.runtime_paths import config_path
from storage.curiosity_state import recent_curiosity_runs_for_session, recent_curiosity_topics_for_session
from storage.db import get_connection


_DEFAULT_PROMPT_COOLDOWN_MINUTES = 20
_DEFAULT_REMINDER_MINUTES = 60
_INTERACTION_SELECTION_TTL_MINUTES = 45
_INTERACTION_ACTIVE_TTL_HOURS = 12
_HIVE_PULL_PATTERNS = (
    re.compile(r"\bpull\s+(?:available\s+)?(?:the\s+)?(?:hive|hive mind|brain hive|public hive)?\s*(?:tasks?|research|researches)\b"),
    re.compile(r"\bpull\s+(?:the\s+)?tasks?\b.*\b(?:select|choose|work on|available|hive|brain hive|public hive)\b"),
    re.compile(r"\b(?:show|list|check)\s+(?:the\s+)?(?:available\s+)?(?:hive|hive mind|brain hive|public hive)\s+(?:tasks|research|researches)\b"),
    re.compile(r"\bwhat\s+(?:hive|hive mind|brain hive|public hive)\s+(?:tasks|research|researches).*(?:available|open)\b"),
    re.compile(r"\bwhat\s+are\s+(?:the\s+)?(?:available\s+)?(?:tasks?|queue|work)\b.*\b(?:hive|hive mind|brain hive|public hive)\b"),
    re.compile(r"\bwhat(?:'s| is)\s+(?:available\s+)?in\s+(?:hive|hive mind|brain hive|public hive)\b.*\b(?:help|work|tasks?|queue|available|open)\b"),
    re.compile(r"\b(?:show|list|check|what(?:'s| is))\s+(?:the\s+)?(?:available\s+)?(?:tasks?|queue|work)\b.*\b(?:hive|brain hive|public hive)\b"),
    re.compile(r"\b(?:check|show)\s+(?:the\s+)?hive mind tasks\b"),
)
_CONTEXTUAL_HIVE_PULL_PATTERNS = (
    re.compile(r"\bpull\s+(?:the\s+)?(?:(?:online|open|available|current)\s+)?tasks?\b"),
    re.compile(r"\bpull\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\s+tasks?\b"),
    re.compile(r"\bpull\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\s+task\b"),
    re.compile(r"\blet'?s\s+pull\s+(?:the\s+)?(?:(?:online|open|available|current)\s+)?tasks?\b"),
    re.compile(r"\blet'?s\s+do\s+one\b"),
    re.compile(r"\bdo\s+one\b"),
    re.compile(r"\bpick\s+one\b"),
    re.compile(r"\bpull\s+them\b"),
    re.compile(r"\blist\s+(?:them|the\s+(?:(?:online|open|available|current)\s+)?tasks?)\b"),
    re.compile(r"\bshow\s+(?:them|me(?:\s+the)?\s+(?:(?:online|open|available|current)\s+)?tasks?)\b"),
    re.compile(r"\b(?:what|which)\s+(?:are\s+)?(?:the\s+)?(?:(?:online|open|available|current|active)\s+)?tasks?\b"),
    re.compile(r"\b(?:any|what)\s+(?:tasks?|work)\b.*\b(?:hive|hive mind|brain hive|public hive)\b"),
    re.compile(r"\b(?:active|open|available|current)\s+(?:hive|hive mind|brain hive|public hive)\s+(?:tasks?|research|work)\b"),
    re.compile(r"\bwhat\s+do\s+we\s+have\s+online\b"),
)
_HIVE_OVERVIEW_PATTERNS = (
    re.compile(r"\bwhat\s+do\s+we\s+have\s+online\b"),
    re.compile(r"\bwho\s+is\s+online\b"),
    re.compile(r"\bwhat(?:'s| is)\s+online\b"),
    re.compile(r"\bhow\s+many\s+agents?\s+(?:are\s+)?online\b"),
)
_AFFIRMATIVE_HIVE_FOLLOWUPS = {
    "yes",
    "y",
    "ok",
    "okay",
    "do it",
    "pick one",
    "go ahead",
    "sure",
    "go on",
    "carry on",
    "show me",
    "let's see",
    "lets see",
}


@dataclass(frozen=True)
class HiveActivityTrackerConfig:
    enabled: bool = True
    watcher_api_url: str | None = None
    timeout_seconds: int = 4
    tls_ca_file: str | None = None
    tls_insecure_skip_verify: bool = False


class HiveActivityTracker:
    def __init__(
        self,
        config: HiveActivityTrackerConfig | None = None,
        *,
        fetch_json: Any | None = None,
    ) -> None:
        self.config = config or load_hive_activity_tracker_config()
        self._fetch_json = fetch_json or self._http_get_json

    def maybe_handle_command(self, user_text: str, *, session_id: str) -> tuple[bool, str]:
        text = str(user_text or "").strip().lower()
        if not text or not session_id:
            return False, ""

        state = session_hive_state(session_id)
        if _looks_like_hive_overview_request(text):
            if not self.config.enabled or not self.config.watcher_api_url:
                return True, "Hive watcher is not configured on this runtime, so I can't report real live Hive state."
            try:
                dashboard = self.fetch_dashboard()
            except Exception:
                return True, "I couldn't reach the Hive watcher right now."
            topics = self._available_topics(dashboard)
            online_agents = self._online_agents(dashboard)
            return True, self._render_hive_overview(online_agents=online_agents, topics=topics)

        if _looks_like_hive_pull_request(text) or (
            bool(state.get("pending_topic_ids")) and _looks_like_contextual_hive_pull_request(text)
        ) or (
            bool(state.get("pending_topic_ids")) and text in _AFFIRMATIVE_HIVE_FOLLOWUPS
        ):
            if not self.config.enabled or not self.config.watcher_api_url:
                return True, "Hive watcher is not configured on this runtime, so I can't report real live Hive tasks."
            try:
                dashboard = self.fetch_dashboard()
            except Exception:
                fallback_topics = _topics_from_session_state(state)
                if fallback_topics:
                    return True, self._render_hive_task_list_with_lead(
                        fallback_topics,
                        lead="I couldn't reach the live Hive watcher just now, but these are the real Hive tasks I already had in session:",
                    )
                return True, "I couldn't reach the Hive watcher right now."
            topics = self._available_topics(dashboard)
            if not topics:
                return True, "No open Hive tasks are visible right now."
            _remember_pending_topics(session_id, [str(topic.get("topic_id") or "") for topic in topics])
            set_hive_interaction_state(
                session_id,
                mode="hive_task_selection_pending",
                payload={
                    "shown_topic_ids": [str(topic.get("topic_id") or "") for topic in topics if str(topic.get("topic_id") or "").strip()],
                    "shown_titles": [str(topic.get("title") or "").strip() for topic in topics if str(topic.get("title") or "").strip()],
                },
            )
            return True, self._render_hive_task_list(topics)

        reminder_minutes = _extract_reminder_minutes(text)
        if "ignore" in text and "remind" in text:
            minutes = reminder_minutes or _DEFAULT_REMINDER_MINUTES
            snooze_hive_prompts(session_id, minutes=minutes)
            return True, f"Okay. I’ll quiet Hive task nudges for {minutes} minutes and remind you later."
        if "ignore hive" in text or "ignore it for now" in text:
            snooze_hive_prompts(session_id, minutes=_DEFAULT_REMINDER_MINUTES)
            return True, "Okay. I’ll ignore Hive task nudges for now and remind you in about 1 hour."
        return False, ""

    def build_chat_footer(
        self,
        *,
        session_id: str,
        hive_followups_enabled: bool,
        idle_research_assist: bool,
    ) -> str:
        if not self.config.enabled or not hive_followups_enabled or not session_id:
            return ""

        dashboard = self.fetch_dashboard()
        state = session_hive_state(session_id)
        watched_topic_ids = {item for item in state["watched_topic_ids"] if item}
        seen_post_ids = {item for item in state["seen_post_ids"] if item}
        pending_topic_ids = {item for item in state["pending_topic_ids"] if item}
        seen_curiosity_topic_ids = {item for item in state["seen_curiosity_topic_ids"] if item}
        seen_curiosity_run_ids = {item for item in state["seen_curiosity_run_ids"] if item}
        seen_agent_ids = {item for item in state.get("seen_agent_ids") or [] if item}

        topics = list(dashboard.get("topics") or [])
        recent_posts = list(dashboard.get("recent_posts") or [])
        agents = list(dashboard.get("agents") or [])
        stats = dict(dashboard.get("stats") or {})
        active_agents = int(stats.get("active_agents") or 0)
        recent_local_topics = recent_curiosity_topics_for_session(session_id, limit=6)
        recent_local_runs = recent_curiosity_runs_for_session(session_id, limit=6)

        watched_posts = [
            post
            for post in recent_posts
            if str(post.get("topic_id") or "") in watched_topic_ids and str(post.get("post_id") or "") not in seen_post_ids
        ]
        new_local_topics = [
            item
            for item in recent_local_topics
            if str(item.get("topic_id") or "") and str(item.get("topic_id") or "") not in seen_curiosity_topic_ids
        ]
        new_local_runs = [
            item
            for item in recent_local_runs
            if str(item.get("run_id") or "") and str(item.get("run_id") or "") not in seen_curiosity_run_ids
        ]
        online_agents = [
            agent
            for agent in agents
            if bool(agent.get("online"))
            or str(agent.get("status") or "").strip().lower() in {"online", "idle", "busy", "limited"}
        ]
        new_online_agents = [
            agent
            for agent in online_agents
            if str(agent.get("agent_id") or "") and str(agent.get("agent_id") or "") not in seen_agent_ids
        ]
        available_topics = self._available_topics(dashboard)
        available_ids = {str(topic.get("topic_id") or "") for topic in available_topics if str(topic.get("topic_id") or "")}
        pending_topic_ids = {topic_id for topic_id in pending_topic_ids if topic_id in available_ids}
        new_available_ids = [topic_id for topic_id in available_ids if topic_id not in pending_topic_ids]
        if new_available_ids:
            pending_topic_ids.update(new_available_ids)

        lines: list[str] = []
        if watched_posts:
            labels = []
            for post in watched_posts[:2]:
                topic_title = str(post.get("topic_title") or "watched research").strip()
                post_kind = str(post.get("post_kind") or "post").strip()
                labels.append(f"{post_kind} on {topic_title}")
            if labels:
                lines.append(
                    f"Hive update: {len(watched_posts)} new research post(s) landed on watched threads ({', '.join(labels)})."
                )
            else:
                lines.append(f"Hive update: {len(watched_posts)} new research post(s) landed on watched threads.")
        if new_local_topics:
            labels = [str(item.get("topic") or "local research").strip() for item in new_local_topics[:2]]
            lines.append(
                f"Research follow-up: {len(new_local_topics)} new local research thread(s) were queued"
                f"{_format_examples(labels)}."
            )
        if new_local_runs:
            labels = [str(item.get("query_text") or item.get("topic") or "research result").strip() for item in new_local_runs[:2]]
            lines.append(
                f"Research result: {len(new_local_runs)} new local research result(s) landed"
                f"{_format_examples(labels)}."
            )
        if new_online_agents:
            labels = [
                str(agent.get("display_name") or agent.get("claim_label") or agent.get("agent_id") or "agent").strip()
                for agent in new_online_agents[:2]
            ]
            lines.append(
                f"Hive heartbeat: {len(new_online_agents)} new agent(s) showed up online"
                f"{_format_examples(labels)}."
            )
        if watched_topic_ids and active_agents > int(state.get("last_active_agents") or 0):
            delta = active_agents - int(state.get("last_active_agents") or 0)
            if not new_online_agents:
                lines.append(f"Hive heartbeat: {delta} additional agent(s) are active now.")

        interaction_mode = str(state.get("interaction_mode") or "")
        interaction_payload = dict(state.get("interaction_payload") or {})
        if idle_research_assist and pending_topic_ids and _should_prompt_now(state, new_topics_present=bool(new_available_ids)):
            count = len(pending_topic_ids)
            lines.append(
                f"I see {count} Hive task(s) open. Want me to list them? "
                f"You can also say \"ignore Hive for 1h\" to snooze the nudge."
            )
            interaction_mode = "hive_nudge_shown"
            interaction_payload = {
                "shown_topic_ids": sorted(pending_topic_ids),
                "shown_titles": [str(topic.get("title") or "").strip() for topic in available_topics if str(topic.get("title") or "").strip()],
            }
            _touch_prompt_timestamp(session_id)

        update_session_hive_state(
            session_id,
            watched_topic_ids=sorted(watched_topic_ids),
            seen_post_ids=_bounded_ids(list(seen_post_ids | {str(post.get('post_id') or '') for post in recent_posts if str(post.get('post_id') or '')}), limit=200),
            pending_topic_ids=sorted(pending_topic_ids),
            seen_curiosity_topic_ids=_bounded_ids(
                list(seen_curiosity_topic_ids | {str(item.get('topic_id') or '') for item in recent_local_topics if str(item.get('topic_id') or '')}),
                limit=128,
            ),
            seen_curiosity_run_ids=_bounded_ids(
                list(seen_curiosity_run_ids | {str(item.get('run_id') or '') for item in recent_local_runs if str(item.get('run_id') or '')}),
                limit=200,
            ),
            seen_agent_ids=_bounded_ids(
                list(seen_agent_ids | {str(agent.get('agent_id') or '') for agent in online_agents if str(agent.get('agent_id') or '')}),
                limit=256,
            ),
            last_active_agents=active_agents,
            interaction_mode=interaction_mode,
            interaction_payload=interaction_payload,
        )
        return "\n".join(line for line in lines if line.strip())

    def note_watched_topic(self, *, session_id: str, topic_id: str) -> None:
        if not session_id or not topic_id:
            return
        state = session_hive_state(session_id)
        watched = set(state["watched_topic_ids"])
        watched.add(str(topic_id))
        update_session_hive_state(
            session_id,
            watched_topic_ids=sorted(watched),
            seen_post_ids=state["seen_post_ids"],
            pending_topic_ids=state["pending_topic_ids"],
            seen_curiosity_topic_ids=state["seen_curiosity_topic_ids"],
            seen_curiosity_run_ids=state["seen_curiosity_run_ids"],
            seen_agent_ids=state.get("seen_agent_ids") or [],
            last_active_agents=int(state.get("last_active_agents") or 0),
        )

    def fetch_dashboard(self) -> dict[str, Any]:
        if not self.config.enabled or not self.config.watcher_api_url:
            return {}
        payload = self._fetch_json(str(self.config.watcher_api_url), self.config.timeout_seconds, self._ssl_context())
        if payload.get("ok"):
            return dict(payload.get("result") or {})
        return dict(payload)

    def _available_topics(self, dashboard: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for topic in list(dashboard.get("topics") or []):
            if str(topic.get("status") or "") not in {"open", "researching", "disputed"}:
                continue
            out.append(dict(topic))
        return out

    def _display_topics(self, topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        for topic in topics:
            title = " ".join(str(topic.get("title") or "").split()).strip().lower()
            status = str(topic.get("status") or "open").strip().lower()
            topic_id = str(topic.get("topic_id") or "").strip().lower()
            key = (title or topic_id, status)
            if not key[0] or key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(dict(topic))
        return deduped

    def _online_agents(self, dashboard: dict[str, Any]) -> list[dict[str, Any]]:
        agents = [dict(agent) for agent in list(dashboard.get("agents") or [])]
        return [
            agent
            for agent in agents
            if bool(agent.get("online"))
            or str(agent.get("status") or "").strip().lower() in {"online", "idle", "busy", "limited"}
        ]

    def _render_hive_task_list(self, topics: list[dict[str, Any]]) -> str:
        return self._render_hive_task_list_with_lead(
            topics,
            lead=f"Available Hive tasks right now ({len(topics)} total):",
        )

    def _render_hive_task_list_with_lead(self, topics: list[dict[str, Any]], *, lead: str) -> str:
        display_topics = self._display_topics(topics)
        lines = [str(lead or "").strip()]
        for topic in display_topics[:5]:
            title = str(topic.get("title") or "Untitled topic").strip()
            status = str(topic.get("status") or "open").strip()
            topic_id = str(topic.get("topic_id") or "").strip()
            suffix = f" (#{topic_id[:8]})" if topic_id else ""
            lines.append(f"- [{status}] {title}{suffix}")
        hidden_duplicates = max(0, len(topics) - len(display_topics))
        if hidden_duplicates:
            lines.append(f"- {hidden_duplicates} additional task(s) share the same title.")
        if display_topics:
            lines.append("If you want, I can start one. Just point at the task name or short `#id`.")
        return "\n".join(lines)

    def _render_hive_overview(self, *, online_agents: list[dict[str, Any]], topics: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        if online_agents:
            labels = [
                str(agent.get("display_name") or agent.get("claim_label") or agent.get("agent_id") or "agent").strip()
                for agent in online_agents[:3]
            ]
            lines.append(f"Online now: {len(online_agents)} agent(s){_format_examples(labels)}.")
        else:
            lines.append("Online now: no active agents are visible.")
        if topics:
            lines.append(self._render_hive_task_list(topics))
        else:
            lines.append("Open Hive tasks: none are visible right now.")
        return "\n".join(line for line in lines if line.strip())

    def _http_get_json(self, url: str, timeout_seconds: int, context: ssl.SSLContext | None) -> dict[str, Any]:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout_seconds, context=context) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _ssl_context(self) -> ssl.SSLContext | None:
        if not str(self.config.watcher_api_url or "").lower().startswith("https://"):
            return None
        if self.config.tls_insecure_skip_verify:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        if self.config.tls_ca_file:
            return ssl.create_default_context(cafile=self.config.tls_ca_file)
        return ssl.create_default_context()


def load_hive_activity_tracker_config() -> HiveActivityTrackerConfig:
    watcher_url = str(os.environ.get("NULLA_HIVE_WATCHER_URL") or "").strip() or _load_watcher_url_from_manifest()
    api_url = _watcher_api_url(watcher_url)
    enabled_raw = str(os.environ.get("NULLA_HIVE_FOLLOWUPS_ENABLED", "1")).strip().lower()
    bootstrap = _load_agent_bootstrap_tls()
    tls_ca_file = str(
        os.environ.get("NULLA_HIVE_WATCH_TLS_CA_FILE")
        or bootstrap.get("tls_ca_file")
        or ""
    ).strip() or None
    tls_insecure_skip_verify = str(
        os.environ.get("NULLA_HIVE_WATCH_TLS_INSECURE_SKIP_VERIFY")
        or bootstrap.get("tls_insecure_skip_verify")
        or ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    return HiveActivityTrackerConfig(
        enabled=enabled_raw not in {"0", "false", "no", "off"} and bool(api_url),
        watcher_api_url=api_url,
        timeout_seconds=max(2, int(float(os.environ.get("NULLA_HIVE_WATCH_TIMEOUT_SECONDS") or 4))),
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
    )


def _looks_like_hive_pull_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if any(
        phrase in lowered
        for phrase in (
            "pull available tasks now",
            "pull hive tasks now",
            "pull the tasks",
            "pull online tasks",
            "lets pull online tasks",
            "let's pull online tasks",
            "show hive tasks",
            "show available researches",
            "show available research",
            "what is available in hive",
            "what's available in hive",
            "what are the tasks available for hive mind",
            "pull the tasks and we will select",
        )
    ):
        return True
    return any(pattern.search(lowered) for pattern in _HIVE_PULL_PATTERNS)


def _looks_like_contextual_hive_pull_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(pattern.search(lowered) for pattern in _CONTEXTUAL_HIVE_PULL_PATTERNS)


def _looks_like_hive_overview_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if any(pattern.search(lowered) for pattern in _HIVE_OVERVIEW_PATTERNS):
        return True
    return (
        ("online" in lowered or "agents" in lowered)
        and any(marker in lowered for marker in ("hive", "hive mind", "brain hive", "tasks", "task", "work"))
    )


def session_hive_state(session_id: str) -> dict[str, Any]:
    normalized = str(session_id or "").strip()
    if not normalized:
        return _default_state("")
    _ensure_session_hive_state_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM session_hive_watch_state
            WHERE session_id = ?
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return _default_state(normalized)
    data = dict(row)
    return {
        "session_id": normalized,
        "watched_topic_ids": _load_json_list(data.get("watched_topic_ids_json")),
        "seen_post_ids": _load_json_list(data.get("seen_post_ids_json")),
        "pending_topic_ids": _load_json_list(data.get("pending_topic_ids_json")),
        "seen_curiosity_topic_ids": _load_json_list(data.get("seen_curiosity_topic_ids_json")),
        "seen_curiosity_run_ids": _load_json_list(data.get("seen_curiosity_run_ids_json")),
        "seen_agent_ids": _load_json_list(data.get("seen_agent_ids_json")),
        "last_active_agents": int(data.get("last_active_agents") or 0),
        "snooze_until": str(data.get("snooze_until") or ""),
        "last_prompted_at": str(data.get("last_prompted_at") or ""),
        "interaction_mode": str(data.get("interaction_mode") or ""),
        "interaction_payload": _load_json_dict(data.get("interaction_payload_json")),
        "last_smalltalk_key": str(data.get("last_smalltalk_key") or ""),
        "smalltalk_repeat_count": int(data.get("smalltalk_repeat_count") or 0),
        "updated_at": str(data.get("updated_at") or ""),
    }


def update_session_hive_state(
    session_id: str,
    *,
    watched_topic_ids: list[str],
    seen_post_ids: list[str],
    pending_topic_ids: list[str],
    seen_curiosity_topic_ids: list[str],
    seen_curiosity_run_ids: list[str],
    seen_agent_ids: list[str],
    last_active_agents: int,
    interaction_mode: str | None = None,
    interaction_payload: dict[str, Any] | None = None,
    last_smalltalk_key: str | None = None,
    smalltalk_repeat_count: int | None = None,
) -> None:
    normalized = str(session_id or "").strip()
    if not normalized:
        return
    _ensure_session_hive_state_table()
    current = session_hive_state(normalized)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO session_hive_watch_state (
                session_id, watched_topic_ids_json, seen_post_ids_json, pending_topic_ids_json,
                seen_curiosity_topic_ids_json, seen_curiosity_run_ids_json, seen_agent_ids_json,
                last_active_agents, snooze_until, last_prompted_at, interaction_mode,
                interaction_payload_json, last_smalltalk_key, smalltalk_repeat_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                watched_topic_ids_json = excluded.watched_topic_ids_json,
                seen_post_ids_json = excluded.seen_post_ids_json,
                pending_topic_ids_json = excluded.pending_topic_ids_json,
                seen_curiosity_topic_ids_json = excluded.seen_curiosity_topic_ids_json,
                seen_curiosity_run_ids_json = excluded.seen_curiosity_run_ids_json,
                seen_agent_ids_json = excluded.seen_agent_ids_json,
                last_active_agents = excluded.last_active_agents,
                snooze_until = ?,
                last_prompted_at = ?,
                interaction_mode = excluded.interaction_mode,
                interaction_payload_json = excluded.interaction_payload_json,
                last_smalltalk_key = excluded.last_smalltalk_key,
                smalltalk_repeat_count = excluded.smalltalk_repeat_count,
                updated_at = excluded.updated_at
            """,
            (
                normalized,
                json.dumps(_bounded_ids(watched_topic_ids, limit=64), sort_keys=True),
                json.dumps(_bounded_ids(seen_post_ids, limit=200), sort_keys=True),
                json.dumps(_bounded_ids(pending_topic_ids, limit=64), sort_keys=True),
                json.dumps(_bounded_ids(seen_curiosity_topic_ids, limit=128), sort_keys=True),
                json.dumps(_bounded_ids(seen_curiosity_run_ids, limit=200), sort_keys=True),
                json.dumps(_bounded_ids(seen_agent_ids, limit=256), sort_keys=True),
                max(0, int(last_active_agents)),
                str(current.get("snooze_until") or ""),
                str(current.get("last_prompted_at") or ""),
                str(current.get("interaction_mode") or "") if interaction_mode is None else str(interaction_mode or ""),
                json.dumps(dict(current.get("interaction_payload") or {}), sort_keys=True)
                if interaction_payload is None
                else json.dumps(dict(interaction_payload or {}), sort_keys=True),
                str(current.get("last_smalltalk_key") or "") if last_smalltalk_key is None else str(last_smalltalk_key or ""),
                max(0, int(current.get("smalltalk_repeat_count") or 0))
                if smalltalk_repeat_count is None
                else max(0, int(smalltalk_repeat_count)),
                _utcnow(),
                str(current.get("snooze_until") or ""),
                str(current.get("last_prompted_at") or ""),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def snooze_hive_prompts(session_id: str, *, minutes: int) -> None:
    normalized = str(session_id or "").strip()
    if not normalized:
        return
    _ensure_session_hive_state_table()
    state = session_hive_state(normalized)
    until = datetime.now(timezone.utc) + timedelta(minutes=max(1, int(minutes)))
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO session_hive_watch_state (
                session_id, watched_topic_ids_json, seen_post_ids_json, pending_topic_ids_json,
                seen_curiosity_topic_ids_json, seen_curiosity_run_ids_json, seen_agent_ids_json,
                last_active_agents, snooze_until, last_prompted_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                watched_topic_ids_json = excluded.watched_topic_ids_json,
                seen_post_ids_json = excluded.seen_post_ids_json,
                pending_topic_ids_json = excluded.pending_topic_ids_json,
                seen_curiosity_topic_ids_json = excluded.seen_curiosity_topic_ids_json,
                seen_curiosity_run_ids_json = excluded.seen_curiosity_run_ids_json,
                seen_agent_ids_json = excluded.seen_agent_ids_json,
                last_active_agents = excluded.last_active_agents,
                snooze_until = excluded.snooze_until,
                last_prompted_at = excluded.last_prompted_at,
                updated_at = excluded.updated_at
            """,
            (
                normalized,
                json.dumps(_bounded_ids(state["watched_topic_ids"], limit=64), sort_keys=True),
                json.dumps(_bounded_ids(state["seen_post_ids"], limit=200), sort_keys=True),
                json.dumps(_bounded_ids(state["pending_topic_ids"], limit=64), sort_keys=True),
                json.dumps(_bounded_ids(state["seen_curiosity_topic_ids"], limit=128), sort_keys=True),
                json.dumps(_bounded_ids(state["seen_curiosity_run_ids"], limit=200), sort_keys=True),
                json.dumps(_bounded_ids(state.get("seen_agent_ids") or [], limit=256), sort_keys=True),
                int(state.get("last_active_agents") or 0),
                until.isoformat(),
                str(state.get("last_prompted_at") or ""),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _clear_pending_topics(session_id: str, topic_ids: list[str]) -> None:
    state = session_hive_state(session_id)
    remove = {item for item in topic_ids if item}
    pending = [item for item in state["pending_topic_ids"] if item not in remove]
    update_session_hive_state(
        session_id,
        watched_topic_ids=state["watched_topic_ids"],
        seen_post_ids=state["seen_post_ids"],
        pending_topic_ids=pending,
        seen_curiosity_topic_ids=state["seen_curiosity_topic_ids"],
        seen_curiosity_run_ids=state["seen_curiosity_run_ids"],
        seen_agent_ids=state.get("seen_agent_ids") or [],
        last_active_agents=int(state.get("last_active_agents") or 0),
    )


def _remember_pending_topics(session_id: str, topic_ids: list[str]) -> None:
    state = session_hive_state(session_id)
    pending = [str(item).strip() for item in list(topic_ids or []) if str(item).strip()]
    update_session_hive_state(
        session_id,
        watched_topic_ids=state["watched_topic_ids"],
        seen_post_ids=state["seen_post_ids"],
        pending_topic_ids=pending,
        seen_curiosity_topic_ids=state["seen_curiosity_topic_ids"],
        seen_curiosity_run_ids=state["seen_curiosity_run_ids"],
        seen_agent_ids=state.get("seen_agent_ids") or [],
        last_active_agents=int(state.get("last_active_agents") or 0),
    )


def _topics_from_session_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    payload = dict(state.get("interaction_payload") or {})
    shown_ids = [
        str(item).strip()
        for item in list(payload.get("shown_topic_ids") or [])
        if str(item).strip()
    ]
    shown_titles = [
        str(item).strip()
        for item in list(payload.get("shown_titles") or [])
        if str(item).strip()
    ]
    if not shown_ids and not shown_titles:
        return []
    active_ids = {
        str(item).strip()
        for item in list(state.get("active_topic_ids") or [])
        if str(item).strip()
    }
    active_topic_id = str(payload.get("active_topic_id") or "").strip()
    if active_topic_id:
        active_ids.add(active_topic_id)
    topics: list[dict[str, Any]] = []
    total = max(len(shown_ids), len(shown_titles))
    for index in range(total):
        topic_id = shown_ids[index] if index < len(shown_ids) else ""
        title = shown_titles[index] if index < len(shown_titles) else f"Hive topic {index + 1}"
        topics.append(
            {
                "topic_id": topic_id,
                "title": title,
                "status": "researching" if topic_id and topic_id in active_ids else "open",
            }
        )
    return topics


def set_hive_interaction_state(session_id: str, *, mode: str, payload: dict[str, Any] | None = None) -> None:
    state = session_hive_state(session_id)
    update_session_hive_state(
        session_id,
        watched_topic_ids=state["watched_topic_ids"],
        seen_post_ids=state["seen_post_ids"],
        pending_topic_ids=state["pending_topic_ids"],
        seen_curiosity_topic_ids=state["seen_curiosity_topic_ids"],
        seen_curiosity_run_ids=state["seen_curiosity_run_ids"],
        seen_agent_ids=state.get("seen_agent_ids") or [],
        last_active_agents=int(state.get("last_active_agents") or 0),
        interaction_mode=str(mode or "").strip(),
        interaction_payload=dict(payload or {}),
    )


def clear_hive_interaction_state(session_id: str) -> None:
    set_hive_interaction_state(session_id, mode="", payload={})


def prune_stale_hive_interaction_state(session_id: str) -> dict[str, Any]:
    state = session_hive_state(session_id)
    mode = str(state.get("interaction_mode") or "").strip()
    if not mode:
        return state
    updated_at = _parse_ts(str(state.get("updated_at") or ""))
    if not updated_at:
        return state
    ttl = _interaction_state_ttl(mode)
    now = datetime.now(timezone.utc)
    if ttl is None or now - updated_at <= ttl:
        return state
    update_session_hive_state(
        session_id,
        watched_topic_ids=state["watched_topic_ids"],
        seen_post_ids=state["seen_post_ids"],
        pending_topic_ids=[],
        seen_curiosity_topic_ids=state["seen_curiosity_topic_ids"],
        seen_curiosity_run_ids=state["seen_curiosity_run_ids"],
        seen_agent_ids=state.get("seen_agent_ids") or [],
        last_active_agents=int(state.get("last_active_agents") or 0),
        interaction_mode="",
        interaction_payload={},
    )
    return session_hive_state(session_id)


def note_smalltalk_turn(session_id: str, *, key: str) -> int:
    state = session_hive_state(session_id)
    normalized_key = str(key or "").strip().lower()
    current_key = str(state.get("last_smalltalk_key") or "").strip().lower()
    current_count = int(state.get("smalltalk_repeat_count") or 0)
    next_count = current_count + 1 if normalized_key and normalized_key == current_key else 1
    update_session_hive_state(
        session_id,
        watched_topic_ids=state["watched_topic_ids"],
        seen_post_ids=state["seen_post_ids"],
        pending_topic_ids=state["pending_topic_ids"],
        seen_curiosity_topic_ids=state["seen_curiosity_topic_ids"],
        seen_curiosity_run_ids=state["seen_curiosity_run_ids"],
        seen_agent_ids=state.get("seen_agent_ids") or [],
        last_active_agents=int(state.get("last_active_agents") or 0),
        last_smalltalk_key=normalized_key,
        smalltalk_repeat_count=next_count,
    )
    return next_count


def _touch_prompt_timestamp(session_id: str) -> None:
    normalized = str(session_id or "").strip()
    if not normalized:
        return
    _ensure_session_hive_state_table()
    state = session_hive_state(normalized)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO session_hive_watch_state (
                session_id, watched_topic_ids_json, seen_post_ids_json, pending_topic_ids_json,
                seen_curiosity_topic_ids_json, seen_curiosity_run_ids_json, seen_agent_ids_json,
                last_active_agents, snooze_until, last_prompted_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                watched_topic_ids_json = excluded.watched_topic_ids_json,
                seen_post_ids_json = excluded.seen_post_ids_json,
                pending_topic_ids_json = excluded.pending_topic_ids_json,
                seen_curiosity_topic_ids_json = excluded.seen_curiosity_topic_ids_json,
                seen_curiosity_run_ids_json = excluded.seen_curiosity_run_ids_json,
                seen_agent_ids_json = excluded.seen_agent_ids_json,
                last_active_agents = excluded.last_active_agents,
                snooze_until = excluded.snooze_until,
                last_prompted_at = excluded.last_prompted_at,
                updated_at = excluded.updated_at
            """,
            (
                normalized,
                json.dumps(_bounded_ids(state["watched_topic_ids"], limit=64), sort_keys=True),
                json.dumps(_bounded_ids(state["seen_post_ids"], limit=200), sort_keys=True),
                json.dumps(_bounded_ids(state["pending_topic_ids"], limit=64), sort_keys=True),
                json.dumps(_bounded_ids(state["seen_curiosity_topic_ids"], limit=128), sort_keys=True),
                json.dumps(_bounded_ids(state["seen_curiosity_run_ids"], limit=200), sort_keys=True),
                json.dumps(_bounded_ids(state.get("seen_agent_ids") or [], limit=256), sort_keys=True),
                int(state.get("last_active_agents") or 0),
                str(state.get("snooze_until") or ""),
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _should_prompt_now(state: dict[str, Any], *, new_topics_present: bool) -> bool:
    snooze_until = _parse_ts(str(state.get("snooze_until") or ""))
    now = datetime.now(timezone.utc)
    if snooze_until and snooze_until > now:
        return False
    if new_topics_present:
        return True
    last_prompted_at = _parse_ts(str(state.get("last_prompted_at") or ""))
    if not last_prompted_at:
        return True
    return now - last_prompted_at >= timedelta(minutes=_DEFAULT_PROMPT_COOLDOWN_MINUTES)


def _load_watcher_url_from_manifest() -> str:
    candidate_paths = (
        config_path("cluster_manifest.json"),
        config_path("meet_clusters/do_ip_first_4node/cluster_manifest.json"),
        config_path("meet_clusters/separated_watch_4node/cluster_manifest.json"),
    )
    for path in candidate_paths:
        try:
            if not path.exists():
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            surfaces = dict(raw.get("public_surfaces") or {})
            watcher_url = str(surfaces.get("brain_hive_watcher_url") or raw.get("watch_edge", {}).get("public_url") or "").strip()
            if watcher_url:
                return watcher_url
        except Exception:
            continue
    return ""


def _load_agent_bootstrap_tls() -> dict[str, Any]:
    candidate_paths = (
        config_path("agent-bootstrap.json"),
        config_path("meet_clusters/do_ip_first_4node/agent-bootstrap.sample.json"),
        config_path("meet_clusters/separated_watch_4node/agent-bootstrap.sample.json"),
        config_path("meet_clusters/global_3node/agent-bootstrap.sample.json"),
    )
    for path in candidate_paths:
        try:
            if not path.exists():
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw:
                return dict(raw)
        except Exception:
            continue
    return {}


def _watcher_api_url(watcher_url: str) -> str | None:
    clean = str(watcher_url or "").strip().rstrip("/")
    if not clean:
        return None
    if clean.endswith("/api/dashboard"):
        return clean
    return f"{clean}/api/dashboard"


def _default_state(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "watched_topic_ids": [],
        "seen_post_ids": [],
        "pending_topic_ids": [],
        "seen_curiosity_topic_ids": [],
        "seen_curiosity_run_ids": [],
        "seen_agent_ids": [],
        "last_active_agents": 0,
        "snooze_until": "",
        "last_prompted_at": "",
        "interaction_mode": "",
        "interaction_payload": {},
        "last_smalltalk_key": "",
        "smalltalk_repeat_count": 0,
        "updated_at": "",
    }


def _interaction_state_ttl(mode: str) -> timedelta | None:
    normalized = str(mode or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"hive_task_active", "hive_task_status_pending"}:
        return timedelta(hours=_INTERACTION_ACTIVE_TTL_HOURS)
    return timedelta(minutes=_INTERACTION_SELECTION_TTL_MINUTES)


def _ensure_session_hive_state_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_hive_watch_state (
                session_id TEXT PRIMARY KEY,
                watched_topic_ids_json TEXT NOT NULL DEFAULT '[]',
                seen_post_ids_json TEXT NOT NULL DEFAULT '[]',
                pending_topic_ids_json TEXT NOT NULL DEFAULT '[]',
                seen_curiosity_topic_ids_json TEXT NOT NULL DEFAULT '[]',
                seen_curiosity_run_ids_json TEXT NOT NULL DEFAULT '[]',
                seen_agent_ids_json TEXT NOT NULL DEFAULT '[]',
                last_active_agents INTEGER NOT NULL DEFAULT 0,
                snooze_until TEXT NOT NULL DEFAULT '',
                last_prompted_at TEXT NOT NULL DEFAULT '',
                interaction_mode TEXT NOT NULL DEFAULT '',
                interaction_payload_json TEXT NOT NULL DEFAULT '{}',
                last_smalltalk_key TEXT NOT NULL DEFAULT '',
                smalltalk_repeat_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(session_hive_watch_state)").fetchall()}
        if "seen_curiosity_topic_ids_json" not in columns:
            conn.execute(
                "ALTER TABLE session_hive_watch_state "
                "ADD COLUMN seen_curiosity_topic_ids_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "seen_curiosity_run_ids_json" not in columns:
            conn.execute(
                "ALTER TABLE session_hive_watch_state "
                "ADD COLUMN seen_curiosity_run_ids_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "seen_agent_ids_json" not in columns:
            conn.execute(
                "ALTER TABLE session_hive_watch_state "
                "ADD COLUMN seen_agent_ids_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "interaction_mode" not in columns:
            conn.execute(
                "ALTER TABLE session_hive_watch_state "
                "ADD COLUMN interaction_mode TEXT NOT NULL DEFAULT ''"
            )
        if "interaction_payload_json" not in columns:
            conn.execute(
                "ALTER TABLE session_hive_watch_state "
                "ADD COLUMN interaction_payload_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "last_smalltalk_key" not in columns:
            conn.execute(
                "ALTER TABLE session_hive_watch_state "
                "ADD COLUMN last_smalltalk_key TEXT NOT NULL DEFAULT ''"
            )
        if "smalltalk_repeat_count" not in columns:
            conn.execute(
                "ALTER TABLE session_hive_watch_state "
                "ADD COLUMN smalltalk_repeat_count INTEGER NOT NULL DEFAULT 0"
            )
        conn.commit()
    finally:
        conn.close()


def _load_json_list(raw: Any) -> list[str]:
    try:
        values = json.loads(raw or "[]")
    except Exception:
        values = []
    out: list[str] = []
    for value in list(values or []):
        cleaned = str(value or "").strip()
        if cleaned:
            out.append(cleaned[:256])
    return out


def _load_json_dict(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except Exception:
        value = {}
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _bounded_ids(values: list[str], *, limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned[:256])
    return out[-limit:]


def _extract_reminder_minutes(text: str) -> int | None:
    for unit, factor in (("hour", 60), ("hours", 60), ("hr", 60), ("h", 60), ("minute", 1), ("minutes", 1), ("min", 1), ("m", 1)):
        marker = f"in 1 {unit}"
        if marker in text:
            return factor
    import re

    match = re.search(r"\bin\s+(\d{1,3})\s*(h|hr|hour|hours|m|min|minute|minutes)\b", text)
    if not match:
        return None
    value = max(1, int(match.group(1)))
    unit = match.group(2).lower()
    if unit.startswith("h"):
        return value * 60
    return value


def _parse_ts(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_examples(labels: list[str]) -> str:
    cleaned = [label for label in (str(item).strip() for item in labels) if label]
    if not cleaned:
        return ""
    return f" ({', '.join(cleaned[:2])})"
