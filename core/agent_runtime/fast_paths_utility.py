from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from core.hive_activity_tracker import note_smalltalk_turn, session_hive_state
from core.onboarding import get_agent_display_name
from core.task_router import evaluate_direct_math_request, evaluate_word_math_request
from core.user_preferences import load_preferences

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
    if phrase in {"what can you do", "help"}:
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
