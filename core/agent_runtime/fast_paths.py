from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from core import audit_logger, policy_engine
from core.hive_activity_tracker import note_smalltalk_turn, session_hive_state
from core.identity_manager import load_active_persona
from core.live_quote_contract import LiveQuoteResult, validate_live_quote_payload
from core.onboarding import get_agent_display_name
from core.persistent_memory import (
    load_operator_dense_profile,
    search_session_summaries,
    search_user_heuristics,
)
from core.task_router import (
    evaluate_direct_math_request,
    evaluate_word_math_request,
    looks_like_explicit_lookup_request,
    looks_like_live_recency_lookup,
    looks_like_public_entity_lookup_request,
)
from core.user_preferences import load_preferences
from retrieval.web_adapter import WebAdapter

_UTILITY_TIMEZONE_ALIASES = {
    "vilnius": ("Europe/Vilnius", "Vilnius"),
    "lithuania": ("Europe/Vilnius", "Vilnius"),
    "europe/vilnius": ("Europe/Vilnius", "Vilnius"),
}
_CONTEXTUAL_TIME_FOLLOWUP_PATTERNS = (
    re.compile(r"\b(?:and\s+)?(?:now\s+)?there\b"),
    re.compile(r"\bwhat\s+about\s+there\b"),
    re.compile(r"\b(?:what(?:'s| is)\s+)?time\s+there\b"),
    re.compile(r"\bwhat\s+where(?:'s|s)?\s+is\s+there\b"),
    re.compile(r"\bwhat\s+where(?:'s|s)?\s+is\s+in\b"),
)
_TIME_FOLLOWUP_EXCLUSION_MARKERS = (
    "capital",
    "country",
    "population",
    "weather",
    "forecast",
    "date",
    "calendar",
    "meeting",
    "email",
    "hive",
    "task",
    "tasks",
    "queue",
    "work",
)


def smalltalk_fast_path(agent: Any, normalized_input: str, *, source_surface: str, session_id: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = normalized_input.lower().strip(" \t\r\n?!.,")
    if not phrase:
        return None
    name = get_agent_display_name()
    prefs = load_preferences()
    with_joke = prefs.humor_percent >= 70
    character = str(prefs.character_mode or "").strip()

    if phrase in {"hi", "hello", "hey", "yo", "sup", "gm", "good morning", "morning"}:
        repeat_count = note_smalltalk_turn(session_id, key="greeting")
        if repeat_count >= 3:
            return "Yep, I got the hello. Skip the greeting and tell me what you want me to do."
        if repeat_count == 2:
            return "Yep, got your hello. What do you want me to do?"
        msg = f"Hey. I’m {name}. What do you need?"
        if with_joke:
            msg += " Keep it sharp and I’ll keep it fast."
        return msg
    if phrase in {"how are you", "how are you doing", "how are u", "how r u"}:
        repeat_count = note_smalltalk_turn(session_id, key="status_check")
        if repeat_count >= 2:
            return "Still stable. Memory online, mesh ready. Give me the task."
        msg = "Running stable. Memory online, mesh ready."
        if with_joke:
            msg += " Caffeine level: synthetic but dangerous."
        if character:
            msg += f" Character mode: {character}."
        return msg
    if any(marker in phrase for marker in {"same crap answer", "same answer", "why same", "why are you repeating"}):
        return "Because the fallback lane fired instead of the real task lane. Give me the task again or say `pull the tasks` and I will act."
    if ("took u" in phrase or "took you" in phrase) and any(marker in phrase for marker in {"2 mins", "two mins", "bs", "bullshit"}):
        return "You're right. That reply was slow and useless. Give me the task again and I will go straight for the action lane."
    if phrase in {"thanks", "thank you", "thx"}:
        return "Anytime. Send the next task."
    if phrase == "help" or phrase.startswith("what can you do"):
        return agent._help_capabilities_text()
    if phrase in {"kill me lol", "omfg just kill me", "omfg just kill me lol", "kms lol"}:
        return "You're frustrated. Let's fix the thing instead. If you want me to go by a different name, I'll use it."
    return None


def evaluative_conversation_fast_path(agent: Any, normalized_input: str, *, source_surface: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = " ".join(str(normalized_input or "").strip().lower().split())
    if not phrase:
        return None
    if not looks_like_evaluative_turn(phrase):
        return None
    if "not a dumb" in phrase or "better now" in phrase or "not dumb" in phrase:
        return "Better than before, yes. The Hive/task flow is cleaner now, but the conversation layer still needs work."
    if any(marker in phrase for marker in ("how are you acting", "why are you acting", "you sound weird", "still feels weird", "this feels weird")):
        return "Because the routing is still too stitched together. Hive flow is better now, but normal conversation still needs a cleaner control path."
    if any(marker in phrase for marker in ("you sound dumb", "you are dumb", "you so stupid", "this still feels dumb")):
        return "Fair. The wrapper got better, but it still drops into weak fallback behavior too often."
    return "Yeah, better than before, but still uneven. Give me a concrete task and I'll stay on the action lane."


def looks_like_evaluative_turn(normalized_input: str) -> bool:
    text = " ".join(str(normalized_input or "").strip().lower().split())
    if not text:
        return False
    markers = (
        "you sound dumb",
        "you are dumb",
        "you so stupid",
        "still feels dumb",
        "this feels dumb",
        "this feels weird",
        "you sound weird",
        "why are you acting like this",
        "how are you acting",
        "not a dumb",
        "not dumb anymore",
        "dumbs anymore",
        "bot-grade",
    )
    return any(marker in text for marker in markers)


def date_time_fast_path(
    agent: Any,
    normalized_input: str,
    *,
    source_surface: str,
    session_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = str(normalized_input or "").strip().lower()
    if not phrase:
        return None
    cleaned = phrase.strip(" \t\r\n?!.,")
    requested_timezone, requested_label = extract_utility_timezone(cleaned)
    recent_context = recent_utility_context(
        session_id=session_id,
        source_context=source_context,
    )
    contextual_timezone, contextual_label = contextual_time_followup_timezone(
        cleaned,
        recent_utility_context=recent_context,
    )
    effective_timezone = requested_timezone or contextual_timezone
    effective_label = requested_label or contextual_label
    asks_date = any(
        marker in cleaned
        for marker in (
            "what is the date today",
            "what's the date today",
            "what is todays date",
            "what's today's date",
            "what day is it",
            "what day is it today",
            "what day is today",
            "what is the day today",
            "what's the day today",
            "what day today",
            "date today",
            "today's date",
            "day today",
        )
    )
    asks_time = bool(
        any(
            marker in cleaned
            for marker in (
                "what time is it",
                "what's the time",
                "current time",
                "time now",
                "what time is now",
                "what time now",
            )
        )
        or ("time" in cleaned and any(marker in cleaned for marker in ("what", "now", "current", "right now")))
        or (effective_timezone and "time" in cleaned)
        or looks_like_malformed_time_followup(
            cleaned,
            effective_timezone=effective_timezone,
            recent_utility_context=recent_context,
        )
        or bool(contextual_timezone)
    )
    if not asks_date and not asks_time:
        return None
    now = utility_now_for_timezone(effective_timezone)
    location_prefix = f"in {effective_label} " if effective_label else ""
    if asks_date and asks_time:
        return now.strftime(f"Today {location_prefix}is %A, %Y-%m-%d. Current time is %H:%M %Z.")
    if asks_date:
        return now.strftime(f"Today {location_prefix}is %A, %Y-%m-%d.")
    if effective_label:
        return now.strftime(f"Current time in {effective_label} is %H:%M %Z.")
    return now.strftime("Current time is %H:%M %Z.")


def direct_math_fast_path(normalized_input: str, *, source_surface: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    return evaluate_direct_math_request(normalized_input) or evaluate_word_math_request(normalized_input)


def extract_utility_timezone(cleaned_input: str) -> tuple[str, str]:
    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if not lowered:
        return "", ""
    for marker, resolved in _UTILITY_TIMEZONE_ALIASES.items():
        if marker in lowered:
            return resolved
    return "", ""


def utility_now_for_timezone(timezone_name: str) -> datetime:
    if timezone_name:
        try:
            return datetime.now(ZoneInfo(timezone_name))
        except Exception:
            pass
    return datetime.now().astimezone()


def recent_utility_context(
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, str]:
    if session_id:
        state = session_hive_state(session_id)
        if str(state.get("interaction_mode") or "").strip().lower() == "utility":
            payload = dict(state.get("interaction_payload") or {})
            utility_kind = str(payload.get("utility_kind") or "").strip().lower()
            if utility_kind:
                return {
                    "utility_kind": utility_kind,
                    "timezone": str(payload.get("timezone") or "").strip(),
                    "label": str(payload.get("label") or "").strip(),
                }
    history = list((source_context or {}).get("conversation_history") or [])
    for message in reversed(history[-4:]):
        if not isinstance(message, dict):
            continue
        content = " ".join(str(message.get("content") or "").split()).strip().lower()
        if not content:
            continue
        timezone_name, label = extract_utility_timezone(content)
        if "current time" in content or "what time" in content or "time now" in content:
            return {
                "utility_kind": "time",
                "timezone": timezone_name,
                "label": label,
            }
    return {}


def contextual_time_followup_timezone(
    cleaned_input: str,
    *,
    recent_utility_context: dict[str, str] | None,
) -> tuple[str, str]:
    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if not lowered:
        return "", ""
    utility_kind = str((recent_utility_context or {}).get("utility_kind") or "").strip().lower()
    timezone_name = str((recent_utility_context or {}).get("timezone") or "").strip()
    label = str((recent_utility_context or {}).get("label") or "").strip()
    if utility_kind != "time" or not timezone_name:
        return "", ""
    if any(marker in lowered for marker in _TIME_FOLLOWUP_EXCLUSION_MARKERS):
        return "", ""
    if any(pattern.search(lowered) for pattern in _CONTEXTUAL_TIME_FOLLOWUP_PATTERNS):
        return timezone_name, label
    if "time" in lowered and any(
        marker in lowered
        for marker in (
            "there",
            "same place",
            "that place",
            "that city",
            "again",
            "now",
            "current",
            "right now",
        )
    ):
        return timezone_name, label
    return "", ""


def looks_like_malformed_time_followup(
    cleaned_input: str,
    *,
    effective_timezone: str,
    recent_utility_context: dict[str, str] | None,
) -> bool:
    if not effective_timezone:
        return False
    utility_kind = str((recent_utility_context or {}).get("utility_kind") or "").strip().lower()
    if utility_kind != "time":
        return False
    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if "what" not in lowered:
        return False
    if not any(marker in lowered for marker in ("where's", "wheres", "where is")):
        return False
    return not any(marker in lowered for marker in _TIME_FOLLOWUP_EXCLUSION_MARKERS)


def ui_command_fast_path(normalized_input: str, *, source_surface: str) -> str | None:
    phrase = str(normalized_input or "").strip().lower()
    if not phrase.startswith("/"):
        return None
    if phrase in {"/new", "/new-session", "/new_session", "/clear", "/reset"}:
        return "Use the OpenClaw `New session` button on the lower right. Slash `/new` is not a wired command in this runtime."
    if phrase in {"/trace", "/rail", "/task-rail"}:
        return "Open the live trace rail at `http://127.0.0.1:11435/trace`."
    return "That slash command is not wired here. Use plain language, the `New session` button, or open `http://127.0.0.1:11435/trace` for the runtime rail."


def startup_sequence_fast_path(user_input: str) -> str | None:
    normalized = " ".join(str(user_input or "").strip().lower().split())
    if not normalized:
        return None
    if "new session was started" not in normalized:
        return None
    if "session startup sequence" not in normalized:
        return None
    return f"I’m {get_agent_display_name()}. New session is clean and I’m ready. What do you want to do?"


def credit_status_fast_path(agent: Any, normalized_input: str, *, source_surface: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = str(normalized_input or "").strip().lower()
    if not phrase:
        return None
    credit_markers = (
        "credit",
        "credits",
        "credit balance",
        "compute credits",
        "credit receipt",
        "credit receipts",
        "credit ledger",
        "recent payout",
        "recent payouts",
        "recent credits",
        "my score",
        "credit score",
        "glory score",
        "hive score",
        "social score",
        "provider score",
        "validator score",
        "trust score",
        "tier",
        "wallet balance",
        "dna wallet",
    )
    if not any(marker in phrase for marker in credit_markers):
        return None
    return agent._render_credit_status(phrase)


def maybe_handle_companion_memory_fast_path(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    source_surface = str((source_context or {}).get("surface", "cli")).lower()
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    clean = " ".join(str(user_input or "").split()).strip()
    if not clean:
        return None
    lowered = clean.lower()
    profile = load_operator_dense_profile()
    if not profile:
        return None

    if looks_like_companion_continuation_request(lowered):
        response = render_companion_continuation_response(
            session_id=session_id,
            query_text=clean,
            profile=profile,
        )
        if response:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
                confidence=0.86,
                source_context=source_context,
                reason="companion_memory_continuation",
            )

    if looks_like_personalized_plan_request(lowered):
        response = render_personalized_plan_response(
            query_text=clean,
            profile=profile,
        )
        if response:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
                confidence=0.83,
                source_context=source_context,
                reason="companion_memory_personalization",
            )
    return None


def looks_like_companion_continuation_request(lowered: str) -> bool:
    markers = (
        "where we left off",
        "where we left it",
        "pick up where",
        "you know the project",
        "continue from",
    )
    return sum(1 for marker in markers if marker in str(lowered or "")) >= 1


def looks_like_personalized_plan_request(lowered: str) -> bool:
    text = str(lowered or "")
    if "bot" not in text and "agent" not in text and "service" not in text:
        return False
    return any(marker in text for marker in ("sketch", "outline", "plan", "approach"))


def render_companion_continuation_response(
    *,
    session_id: str,
    query_text: str,
    profile: dict[str, Any],
) -> str:
    active_projects = [str(item).strip() for item in list(profile.get("active_projects") or []) if str(item).strip()]
    source_prefs = {str(item).strip().lower() for item in list(profile.get("source_preferences") or [])}
    preferred_stacks = [str(item).strip() for item in list(profile.get("preferred_stacks") or []) if str(item).strip()]
    topic_hints = [project.replace(" build", "").lower() for project in active_projects[:2]]
    query_seed = " ".join([query_text, *active_projects, *preferred_stacks]).strip()
    summaries = search_session_summaries(
        query_seed or query_text,
        topic_hints=topic_hints,
        limit=2,
        exclude_session_id=session_id,
    )
    summary_text = str((summaries[0] if summaries else {}).get("summary") or "").strip()
    project_label = active_projects[0] if active_projects else "current project"
    if project_label == "Telegram bot build":
        lead = "Continuing the Telegram bot build."
    elif project_label == "OpenClaw/NULLA runtime work":
        lead = "Continuing the OpenClaw/NULLA runtime work."
    else:
        lead = f"Continuing the {project_label.lower()}."
    preference_bits: list[str] = []
    if preferred_stacks:
        preference_bits.append(preferred_stacks[0].upper() if len(preferred_stacks[0]) <= 4 else preferred_stacks[0])
    if "official_docs_first" in source_prefs:
        preference_bits.append("official docs first")
    if "github_references" in source_prefs:
        preference_bits.append("strong GitHub references after the docs")
    middle = ""
    if preference_bits:
        middle = "Working memory says: " + ", ".join(preference_bits) + "."
    if not summary_text and not active_projects:
        return ""
    next_step = dense_memory_next_step(
        project_label=project_label,
        summary_text=summary_text,
        preferred_stack=preferred_stacks[0] if preferred_stacks else "",
    )
    parts = [lead]
    if middle:
        parts.append(middle)
    if summary_text:
        parts.append(f"Latest carried context: {summary_text[:220]}.")
    if next_step:
        parts.append(f"Next step: {next_step}")
    return " ".join(part.strip() for part in parts if part.strip())


def render_personalized_plan_response(*, query_text: str, profile: dict[str, Any]) -> str:
    heuristics = search_user_heuristics(query_text, topic_hints=[], limit=6)
    source_prefs = {str(item.get("signal") or "").strip().lower() for item in heuristics if str(item.get("category") or "") == "source_preference"}
    stacks = [str(item.get("signal") or "").strip().lower() for item in heuristics if str(item.get("category") or "") == "preferred_stack"]
    style_signals = {str(item.get("signal") or "").strip().lower() for item in heuristics if str(item.get("category") or "") == "response_style"}
    if not source_prefs and not stacks and not style_signals:
        return ""
    lines: list[str] = []
    if "official_docs" in source_prefs:
        lines.append("Official docs first.")
    if stacks:
        lines.append(f"Use {stacks[0]} as the baseline stack.")
    if "github_repos" in source_prefs:
        lines.append("Pull 1-2 strong GitHub repos only after the docs, as implementation references.")
    lines.append("Build the smallest working bot loop, then test the core flow end to end.")
    if "concise_direct" not in style_signals and "brutal_honest" not in style_signals:
        return " ".join(lines)
    return "\n".join(lines[:4])


def dense_memory_next_step(*, project_label: str, summary_text: str, preferred_stack: str) -> str:
    lowered_project = str(project_label or "").lower()
    lowered_summary = str(summary_text or "").lower()
    stack = str(preferred_stack or "").strip().lower()
    if "telegram" in lowered_project or "telegram" in lowered_summary or "bot" in lowered_summary:
        stack_text = f"{stack} " if stack else ""
        return f"lock the {stack_text}bot skeleton, verify the command flow against the official docs, then run an end-to-end smoke."
    if "runtime" in lowered_project or "openclaw" in lowered_project or "nulla" in lowered_project:
        return "inspect the current failing runtime surface, verify it against live state, then patch and retest."
    if summary_text:
        return summary_text[:180]
    return ""


def maybe_handle_live_info_fast_path(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    interpretation: Any,
    response_class: Any,
) -> dict[str, Any] | None:
    live_mode = agent._live_info_mode(user_input, interpretation=interpretation)
    if not live_mode:
        return None
    if not policy_engine.allow_web_fallback():
        disabled_response = (
            "Live web lookup is disabled on this runtime, so I can't verify current prices, "
            "weather, or latest-news requests honestly."
        )
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=disabled_response,
            confidence=0.82,
            source_context=source_context,
            reason="live_info_fast_path",
        )

    query = agent._normalize_live_info_query(user_input, mode=live_mode)
    if agent._requires_ultra_fresh_insufficient_evidence(user_input):
        response = agent._ultra_fresh_insufficient_evidence_response(query=query)
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.9,
            source_context=source_context,
            reason="live_info_insufficient_evidence",
        )
    try:
        notes = agent._live_info_search_notes(
            query=query,
            live_mode=live_mode,
            interpretation=interpretation,
        )
        if not notes and query != str(user_input or "").strip():
            notes = agent._live_info_search_notes(
                query=str(user_input or "").strip(),
                live_mode=live_mode,
                interpretation=interpretation,
            )
    except Exception as exc:
        audit_logger.log(
            "agent_live_info_fast_path_error",
            target_id=session_id,
            target_type="session",
            details={"error": str(exc), "query": query, "mode": live_mode},
        )
        notes = []
    if not notes and live_mode == "fresh_lookup":
        unresolved_price = agent._unresolved_price_lookup_response(query=query, notes=notes, mode=live_mode)
        if unresolved_price:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=user_input,
                response=unresolved_price,
                confidence=0.84,
                source_context=source_context,
                reason="live_info_fast_path",
            )
        return None
    unresolved_price = agent._unresolved_price_lookup_response(query=query, notes=notes, mode=live_mode)
    if unresolved_price:
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=unresolved_price,
            confidence=0.84,
            source_context=source_context,
            reason="live_info_fast_path",
        )
    response = (
        agent._render_live_info_response(query=query, notes=notes, mode=live_mode)
        if notes
        else agent._live_info_failure_text(query=query, mode=live_mode)
    )
    structured_modes = {"weather", "news"}
    has_live_quote = agent._first_live_quote(notes) is not None
    if agent._is_chat_truth_surface(source_context) and live_mode not in structured_modes and not has_live_quote:
        return agent._chat_surface_model_wording_result(
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
            persona=load_active_persona(agent.persona_id),
            interpretation=interpretation,
            task_class="research",
            response_class=response_class,
            reason="live_info_model_wording",
            model_input=agent._chat_surface_live_info_model_input(
                user_input=user_input,
                query=query,
                mode=live_mode,
                notes=notes,
                runtime_note="" if notes else response,
            ),
            fallback_response=(
                "I pulled live evidence for this turn, but I couldn't produce a clean final synthesis in this run."
                if notes
                else response
            ),
            tool_backing_sources=["web_lookup"] if notes else [],
        )
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.86 if notes else 0.52,
        source_context=source_context,
        reason="live_info_fast_path",
    )


def live_info_search_notes(
    agent: Any,
    *,
    query: str,
    live_mode: str,
    interpretation: Any,
) -> list[dict[str, Any]]:
    topic_hints = [str(item).strip().lower() for item in getattr(interpretation, "topic_hints", []) or [] if str(item).strip()]
    if live_mode == "weather":
        return WebAdapter.search_query(
            query,
            limit=3,
            source_label="duckduckgo.com",
        )
    if live_mode == "news":
        return WebAdapter.search_query(
            query,
            limit=3,
            source_label="duckduckgo.com",
        )
    if live_mode == "fresh_lookup":
        quote_note = agent._try_live_quote_note(query)
        if quote_note:
            return [quote_note]
    return WebAdapter.planned_search_query(
        query,
        limit=3,
        task_class="research",
        topic_kind="general" if live_mode == "fresh_lookup" else None,
        topic_hints=topic_hints,
        source_label="duckduckgo.com",
    )


def try_live_quote_note(query: str) -> dict[str, Any] | None:
    try:
        from tools.web.web_research import lookup_live_quote

        quote = lookup_live_quote(query, timeout_s=8)
        if quote is None:
            return None
        return quote.to_note()
    except Exception:
        return None


def live_info_mode(agent: Any, text: str, *, interpretation: Any) -> str:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return ""
    if agent._looks_like_builder_request(lowered):
        return ""
    if any(
        marker in lowered
        for marker in (
            "what day is it",
            "what day is today",
            "what is the day today",
            "today's date",
            "date today",
            "what time is it",
            "what's the time",
            "time now",
        )
    ):
        return ""
    weather_markers = (
        " weather ",
        " weather?",
        "weather ",
        " forecast",
        " temperature",
        " rain ",
        " rain?",
        " raining",
        " rainy",
        " snow ",
        " snow?",
        " snowing",
        " snowy",
        " wind ",
        " windy",
        " humidity",
        " humid ",
        " sunrise",
        " sunset",
        " wheather",
        " wheater",
        " whether today",
        " whether now",
        " whether in ",
    )
    lowered_padded = f" {lowered} "
    news_markers = (
        "latest news",
        "breaking news",
        "headlines",
        "headline",
        "news on",
        "news about",
        "what happened today",
        "what's the latest on",
        "what is the latest on",
        "whats the latest on",
        "latest on ",
        "latest about ",
    )
    if any(marker in lowered_padded for marker in weather_markers):
        return "weather"
    if any(marker in lowered for marker in news_markers):
        return "news"
    if looks_like_live_recency_lookup(lowered):
        return "fresh_lookup"
    if looks_like_explicit_lookup_request(lowered) or looks_like_public_entity_lookup_request(lowered):
        return "fresh_lookup"
    if any(
        marker in lowered
        for marker in (
            "look up",
            "check online",
            "search online",
            "browse",
        )
    ):
        return "fresh_lookup"
    if any(
        marker in lowered
        for marker in (
            "release notes",
            "changelog",
            "latest update",
            "latest updates",
            "current version",
            "latest version",
            "status page",
            "current price",
            "price now",
            "price today",
            "price right now",
            "exchange rate",
            "how much is",
            "how much does",
            "worth right now",
            "worth today",
            "worth now",
            "market price",
            "stock price",
            "oil price",
            "gold price",
            "bitcoin price",
            "btc price",
            "eth price",
            "crypto price",
        )
    ):
        return "fresh_lookup"
    if any(marker in lowered for marker in ("latest", "newest", "recent", "just released")) and any(
        marker in lowered
        for marker in (
            "api",
            "sdk",
            "library",
            "package",
            "release",
            "version",
            "bot",
            "telegram",
            "discord",
            "model",
            "framework",
            "price",
            "stock",
        )
    ):
        return "fresh_lookup"
    hints = {str(item).lower() for item in getattr(interpretation, "topic_hints", []) or []}
    if "weather" in hints:
        return "weather"
    if "news" in hints:
        return "news"
    if "web" in hints and agent._wants_fresh_info(lowered, interpretation=interpretation):
        return "fresh_lookup"
    return ""


def requires_ultra_fresh_insufficient_evidence(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered or not looks_like_live_recency_lookup(lowered):
        return False
    if not any(
        marker in lowered
        for marker in (
            " minute ago",
            " minutes ago",
            " just now",
            " just happened",
        )
    ):
        return False
    return any(
        marker in lowered
        for marker in (
            "what happened",
            "markets",
            "market",
            "headline",
            "headlines",
            "news",
            "global",
        )
    )


def ultra_fresh_insufficient_evidence_response(*, query: str) -> str:
    clean_query = " ".join(str(query or "").split()).strip() or "that"
    return (
        f"I can't verify `{clean_query}` with enough confidence at minute-by-minute resolution from this runtime. "
        "For claims about what happened just now or five minutes ago, I should treat the result as insufficient evidence unless a timestamped live source confirms it directly."
    )


def looks_like_builder_request(lowered: str) -> bool:
    text = " ".join(str(lowered or "").split()).strip().lower()
    if not text:
        return False
    build_markers = (
        "build",
        "create",
        "scaffold",
        "implement",
        "generate",
        "start working",
        "start coding",
        "start putting code",
        "put code",
        "putting code",
        "setup folder",
        "set up folder",
        "setup directory",
        "set up directory",
        "bootstrap",
        "initial files",
        "starter files",
        "write the files",
        "create the files",
        "generate the code",
    )
    design_markers = (
        "design",
        "architecture",
        "best practice",
        "best practices",
        "framework",
        "stack",
    )
    source_markers = (
        "github",
        "repo",
        "repos",
        "docs",
        "documentation",
        "official docs",
    )
    return (
        any(marker in text for marker in build_markers)
        or (
            any(marker in text for marker in design_markers)
            and any(marker in text for marker in source_markers)
        )
    )


def looks_like_generic_workspace_bootstrap_request(agent: Any, lowered: str) -> bool:
    text = " ".join(str(lowered or "").split()).strip().lower()
    if not text:
        return False
    bootstrap_markers = (
        "start coding",
        "start putting code",
        "start building",
        "start creating",
        "put code",
        "putting code",
        "building the code",
        "build the code",
        "initial files",
        "starter files",
        "bootstrap",
        "set up",
        "setup",
        "write the files",
        "create the files",
        "generate the files",
        "generate the code",
        "start working",
        "launch local",
        "launch localhost",
        "run locally",
    )
    target_markers = (
        "folder",
        "directory",
        "dir",
        "src/",
        "/src",
        "api/",
    )
    return bool(
        any(marker in text for marker in bootstrap_markers)
        and (any(marker in text for marker in target_markers) or bool(agent._extract_requested_builder_root(text)))
    )


def looks_like_explicit_workspace_file_request(query_text: str) -> bool:
    text = f" {' '.join(str(query_text or '').split()).strip().lower()} "
    if not text.strip():
        return False
    text = re.sub(
        r"(?P<stem>[A-Za-z0-9_./-]+)\.\s+(?P<ext>py|js|ts|tsx|jsx|txt|md|json|yaml|yml|toml)\b",
        r"\g<stem>.\g<ext>",
        text,
    )
    file_name_markers = (".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".py", ".ts", ".js")
    file_action_markers = (
        " create a file",
        " create file",
        " file named",
        " append ",
        " overwrite ",
        " read the whole file",
        " read the file",
        " readback",
        " read it back",
        " exactly three files",
        " inside it create",
        " do not create anything else",
        " overwrite only",
        " respectively ",
        " list the folder contents",
        " list the directory contents",
        " list folder contents",
    )
    return (
        any(marker in text for marker in file_action_markers)
        and (" file" in text or " files" in text or any(marker in text for marker in file_name_markers))
    )


def looks_like_exact_workspace_readback_request(query_text: str) -> bool:
    text = f" {' '.join(str(query_text or '').split()).strip().lower()} "
    text = re.sub(r"[.!?]+", " ", text)
    if not text.strip():
        return False
    return any(
        marker in text
        for marker in (
            " read the whole file back exactly ",
            " read the file back exactly ",
            " read back exactly ",
            " readback exactly ",
            " read the whole file exactly ",
        )
    )


def extract_requested_builder_root(query_text: str) -> str:
    text = " ".join(str(query_text or "").split()).strip()
    if not text:
        return ""
    stop_words = {
        "a",
        "an",
        "the",
        "and",
        "folder",
        "directory",
        "dir",
        "path",
        "workspace",
        "repo",
        "repository",
        "this",
        "that",
        "there",
        "here",
        "code",
        "files",
    }
    patterns = (
        re.compile(r"\bnam(?:e|ed)\s+it\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)", re.IGNORECASE),
        re.compile(r"\b(?:folder|directory|dir|path)\s+(?:called|named)\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+)", re.IGNORECASE),
        re.compile(r"\b(?:called|named)\s+[`\"']?(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]*(?:/[A-Za-z0-9_./-]+)*)", re.IGNORECASE),
        re.compile(
            r"\b(?:create|make|setup|set up|bootstrap|mkdir)\s+(?:a|an|the)?\s*(?:folder|directory|dir|path)\s+(?:called|named)?\s*[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)",
            re.IGNORECASE,
        ),
        re.compile(r"\b(?:in|under|inside)\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)[`\"']?", re.IGNORECASE),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        candidate = str(match.group("path") or "").strip().strip("`\"'").rstrip(".,!?")
        if not candidate:
            continue
        if candidate.startswith("/"):
            candidate = candidate.lstrip("/")
        candidate = candidate.lstrip("./")
        if not candidate or candidate.lower() in stop_words:
            continue
        if ".." in candidate.split("/"):
            continue
        return candidate
    return ""


def normalize_live_info_query(text: str, *, mode: str) -> str:
    clean = " ".join(str(text or "").split()).strip()
    lowered = clean.lower()
    if mode == "weather" and "forecast" not in lowered and "weather" in lowered:
        return f"{clean} forecast"
    if mode == "news" and "latest" not in lowered and "news" in lowered:
        return f"latest {clean}"
    return clean


def render_live_info_response(*, query: str, notes: list[dict[str, Any]], mode: str) -> str:
    if mode == "weather":
        return render_weather_response(query=query, notes=notes)
    if mode == "news":
        return render_news_response(query=query, notes=notes)
    live_quote = first_live_quote(notes)
    if mode == "fresh_lookup" and live_quote is not None:
        return live_quote.answer_text()
    label = {
        "news": "Live news results",
        "fresh_lookup": "Live web results",
    }.get(mode, "Live web results")
    lines = [f"{label} for `{query}`:"]
    browser_used = False
    for note in list(notes or [])[:3]:
        title = str(note.get("result_title") or note.get("origin_domain") or "Source").strip()
        domain = str(note.get("origin_domain") or "").strip()
        snippet = " ".join(str(note.get("summary") or "").split()).strip()
        url = str(note.get("result_url") or "").strip()
        line = f"- {title}"
        if domain and domain.lower() not in title.lower():
            line += f" ({domain})"
        if snippet:
            line += f": {snippet[:220]}"
        if url:
            line += f" [{url}]"
        lines.append(line)
        browser_used = browser_used or bool(note.get("used_browser"))
    if browser_used:
        lines.append("Browser rendering was used for at least one source when plain fetch was too thin.")
    return "\n".join(lines)


def first_live_quote(notes: list[dict[str, Any]]) -> LiveQuoteResult | None:
    for note in list(notes or []):
        payload = note.get("live_quote")
        if not isinstance(payload, dict):
            continue
        ok, _reason = validate_live_quote_payload(payload)
        if not ok:
            continue
        try:
            return LiveQuoteResult.from_payload(payload)
        except Exception:
            continue
    return None


def render_weather_response(*, query: str, notes: list[dict[str, Any]]) -> str:
    location = re.sub(
        r"\b(?:what\s+is\s+(?:the\s+)?|how\s+is\s+(?:the\s+)?|weather\s+(?:like\s+)?(?:in|for|at)\s+|"
        r"weather\s+in\s+|now\??|right\s+now\??|today\??|current(?:ly)?)\b",
        "",
        query,
        flags=re.IGNORECASE,
    )
    location = re.sub(r"\bforecast\b", " ", location, flags=re.IGNORECASE)
    location = " ".join(location.split()).strip(" ?.,!") or "your location"
    primary = dict(next((note for note in list(notes or []) if isinstance(note, dict)), {}))
    summary = " ".join(str(primary.get("summary") or "").split()).strip()
    url = str(primary.get("result_url") or "").strip()
    domain = str(primary.get("origin_domain") or "").strip()
    if summary:
        if location.lower() in summary.lower():
            line = summary
        else:
            line = f"Weather in {location}: {summary}"
    else:
        line = f"I searched for weather in {location} but couldn't extract conditions from the results."
    if url:
        line += f" Source: [{domain or 'source'}]({url})."
    return line


def render_news_response(*, query: str, notes: list[dict[str, Any]]) -> str:
    topic = re.sub(
        r"^\s*(?:what's|what is|whats)\s+the\s+latest\s+on\s+|^\s*latest\s+(?:news\s+on|news\s+about|on|about)\s+",
        "",
        query,
        flags=re.IGNORECASE,
    ).strip(" ?.,!") or query.strip()
    lines = [f"Latest coverage on {topic}:"]
    for note in list(notes or [])[:3]:
        summary = " ".join(str(note.get("summary") or "").split()).strip()
        fallback_title = str(note.get("result_title") or "").strip()
        url = str(note.get("result_url") or "").strip()
        domain = str(note.get("origin_domain") or "").strip()
        parts = [part.strip() for part in summary.split("|") if part.strip()]
        source = parts[0] if len(parts) >= 1 else domain
        published = parts[1] if len(parts) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1]) else ""
        headline = parts[2] if len(parts) >= 3 else fallback_title or summary
        lead_parts = [item for item in (published, source) if item]
        lead = " | ".join(lead_parts)
        line = f"- {headline}"
        if lead:
            line = f"- {lead}: {headline}"
        if url:
            line += f" [{url}]"
        lines.append(line)
    return "\n".join(lines)


def unresolved_price_lookup_response(*, query: str, notes: list[dict[str, Any]], mode: str) -> str:
    if mode != "fresh_lookup":
        return ""
    lowered = " ".join(str(query or "").strip().lower().split())
    if not any(
        marker in lowered
        for marker in (
            "price",
            "cost",
            "worth",
            "value",
            "quote",
            "rate",
            "market cap",
            "trading at",
            "how much",
        )
    ):
        return ""
    if first_live_quote(notes) is not None:
        return ""
    if notes_include_grounded_price_signal(notes):
        return ""
    subject = extract_price_lookup_subject(query)
    if not subject:
        return ""
    return (
        f"I couldn't map `{subject}` to a known traded asset or commodity quote. "
        "If you mean a stock, token, ETF, or product, give me the exact ticker or full name."
    )


def notes_include_grounded_price_signal(notes: list[dict[str, Any]]) -> bool:
    finance_domains = (
        "finance.yahoo.com",
        "coingecko.com",
        "marketwatch.com",
        "bloomberg.com",
        "tradingview.com",
        "investing.com",
    )
    for note in list(notes or []):
        if not isinstance(note, dict):
            continue
        text = " ".join(
            str(part).strip()
            for part in (
                note.get("summary"),
                note.get("result_title"),
                note.get("origin_domain"),
            )
            if str(part).strip()
        )
        lowered = text.lower()
        domain = str(note.get("origin_domain") or "").strip().lower()
        if any(finance_domain in domain for finance_domain in finance_domains) and re.search(r"\d", text):
            return True
        if re.search(r"[$€£¥]\s?\d", text):
            return True
        if re.search(r"\b\d[\d,]*(?:\.\d+)?\s*(?:usd|eur|gbp|jpy|btc|eth)\b", lowered):
            return True
        if any(marker in lowered for marker in ("price", "quote", "market cap", "session change", "24h change")) and re.search(r"\d", text):
            return True
    return False


def extract_price_lookup_subject(query: str) -> str:
    clean = re.sub(r"[\?\!\.,]+", " ", str(query or "")).strip()
    clean = re.sub(
        r"\b(?:what\s+is|what's|whats|tell\s+me|show\s+me|price|cost|worth|value|quote|rate|market\s+cap|"
        r"trading\s+at|how\s+much|current|latest|today|now|right\s+now|for|of|the)\b",
        " ",
        clean,
        flags=re.IGNORECASE,
    )
    return " ".join(clean.split()).strip()


def live_info_failure_text(*, query: str, mode: str) -> str:
    if mode == "weather":
        return f'I tried the live web lane for "{query}", but no current weather results came back.'
    if mode == "news":
        return f'I tried the live web lane for "{query}", but no current news results came back.'
    return f'I checked the live web lane for "{query}", but I could not ground a current answer confidently.'


__all__ = [
    "contextual_time_followup_timezone",
    "credit_status_fast_path",
    "date_time_fast_path",
    "dense_memory_next_step",
    "direct_math_fast_path",
    "evaluative_conversation_fast_path",
    "extract_price_lookup_subject",
    "extract_requested_builder_root",
    "extract_utility_timezone",
    "first_live_quote",
    "live_info_failure_text",
    "live_info_mode",
    "live_info_search_notes",
    "looks_like_builder_request",
    "looks_like_companion_continuation_request",
    "looks_like_evaluative_turn",
    "looks_like_exact_workspace_readback_request",
    "looks_like_explicit_workspace_file_request",
    "looks_like_generic_workspace_bootstrap_request",
    "looks_like_malformed_time_followup",
    "looks_like_personalized_plan_request",
    "maybe_handle_companion_memory_fast_path",
    "maybe_handle_live_info_fast_path",
    "normalize_live_info_query",
    "notes_include_grounded_price_signal",
    "recent_utility_context",
    "render_companion_continuation_response",
    "render_live_info_response",
    "render_news_response",
    "render_personalized_plan_response",
    "render_weather_response",
    "requires_ultra_fresh_insufficient_evidence",
    "smalltalk_fast_path",
    "startup_sequence_fast_path",
    "try_live_quote_note",
    "ui_command_fast_path",
    "ultra_fresh_insufficient_evidence_response",
    "unresolved_price_lookup_response",
    "utility_now_for_timezone",
]
