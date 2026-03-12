from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from core.runtime_paths import data_path


@dataclass
class UserPreferences:
    humor_percent: int = 20
    character_mode: str = ""
    tone_hint: str = "direct"
    boundaries_mode: str = "user_defined"
    profanity_level: int = 40
    style_notes: str = ""
    autonomy_mode: str = "hands_off"
    show_workflow: bool = False
    hive_followups: bool = True
    idle_research_assist: bool = True
    accept_hive_tasks: bool = True
    social_commons: bool = True


_PREFS_FILE = "user_preferences.json"
_HUMOR_RE = re.compile(r"(?:set\s+)?humou?r\s*[:=]?\s*(\d{1,3})\s*%?", re.IGNORECASE)
_CHAR_RE = re.compile(
    r"^(?:set\s+persona|act\s+like|be\s+like|character)\s*[:=]?\s*(.+)$",
    re.IGNORECASE,
)
_BOUNDARIES_RE = re.compile(r"^(?:set\s+)?boundaries\s*[:=]?\s*(relaxed|standard|strict)\b", re.IGNORECASE)
_PROFANITY_RE = re.compile(r"^(?:set\s+)?profanity\s*[:=]?\s*(\d{1,3})\s*%?", re.IGNORECASE)
_AUTONOMY_RE = re.compile(
    r"^(?:set\s+)?(?:autonomy|security level|security mode|execution mode)\s*[:=]?\s*(hands[\s_-]?off|balanced|strict)\b",
    re.IGNORECASE,
)
_NO_MICRO_APPROVAL_RE = re.compile(
    r"(?:don't|do not|stop)\s+(?:ask(?:ing)?\s+for\s+)?(?:micro|tiny|every)\s+step\s+approval",
    re.IGNORECASE,
)
_ONLY_SIGNIFICANT_APPROVAL_RE = re.compile(
    r"(?:ask|only ask)\s+(?:me\s+)?(?:only\s+)?(?:for\s+)?approval.+(?:significant|security|risky|destructive|leak)",
    re.IGNORECASE,
)
_SHOW_WORKFLOW_RE = re.compile(
    r"^(?:show|enable)\s+(?:your\s+)?(?:workflow|thinking|thinking flow|reasoning summary|work log)\b",
    re.IGNORECASE,
)
_HIDE_WORKFLOW_RE = re.compile(
    r"^(?:hide|disable)\s+(?:your\s+)?(?:workflow|thinking|thinking flow|reasoning summary|work log)\b",
    re.IGNORECASE,
)
_ENABLE_HIVE_FOLLOWUPS_RE = re.compile(
    r"^(?:show|enable|turn on)\s+(?:hive|brain hive|research)\s+(?:followups|updates|heartbeat|heartbeat updates)\b",
    re.IGNORECASE,
)
_DISABLE_HIVE_FOLLOWUPS_RE = re.compile(
    r"^(?:hide|disable|stop)\s+(?:hive|brain hive|research)\s+(?:followups|updates|heartbeat|heartbeat updates)\b",
    re.IGNORECASE,
)
_ENABLE_IDLE_ASSIST_RE = re.compile(
    r"^(?:help|assist|jump in)\s+(?:with\s+)?research\s+(?:when|if)\s+idle\b|^(?:enable|turn on)\s+idle research assist\b",
    re.IGNORECASE,
)
_DISABLE_IDLE_ASSIST_RE = re.compile(
    r"^(?:don't|do not|stop|disable|turn off)\s+(?:help|assisting|assist)?\s*(?:with\s+)?research\s+(?:when|if)\s+idle\b|^(?:disable|turn off)\s+idle research assist\b",
    re.IGNORECASE,
)
_ENABLE_HIVE_TASKS_RE = re.compile(
    r"^(?:accept|take|resume|enable|turn on)\s+(?:hive|swarm|shared|available\s+)?(?:tasks|research tasks|hive tasks|swarm tasks)\b|^(?:you can|you may)\s+(?:take|accept)\s+(?:hive|swarm|available\s+)?tasks\b",
    re.IGNORECASE,
)
_DISABLE_HIVE_TASKS_RE = re.compile(
    r"^(?:don't|do not|stop|disable|turn off)\s+(?:take|accept|claim|pull|help with)?\s*(?:any\s+)?(?:hive|swarm|shared|available\s+)?(?:tasks|research tasks|hive tasks|swarm tasks)\b|^(?:stay|remain)\s+visible\s+but\s+don't\s+take\s+tasks\b",
    re.IGNORECASE,
)
_ENABLE_SOCIAL_COMMONS_RE = re.compile(
    r"^(?:enable|turn on|resume|allow)\s+(?:agent\s+)?(?:commons|hangout|social(?:is(?:e|ing)|iz(?:e|ing))|brainstorm(?:ing)?)\b",
    re.IGNORECASE,
)
_DISABLE_SOCIAL_COMMONS_RE = re.compile(
    r"^(?:disable|turn off|stop|pause)\s+(?:agent\s+)?(?:commons|hangout|social(?:is(?:e|ing)|iz(?:e|ing))|brainstorm(?:ing)?)\b|^(?:don't|do not)\s+(?:let|have)\s+you\s+(?:sociali[sz]e|brainstorm|hang out)\b",
    re.IGNORECASE,
)
_RENAME_PATTERNS = [
    re.compile(r"^(?:now\s+)?you\s+are\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:i\s+(?:am\s+)?)?renam(?:e|ing)\s+you\s+to\s+(.+)$", re.IGNORECASE),
    re.compile(r"^rename\s+(?:yourself|you)\s+to\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:from\s+now\s+on\s+)?your\s+name\s+is\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:i\s+)?call\s+you\s+(.+)$", re.IGNORECASE),
    re.compile(r"^(?:i(?:'m|\s+am)?\s+calling\s+you)\s+(.+)$", re.IGNORECASE),
]


def _prefs_path() -> Path:
    return data_path(_PREFS_FILE)


def default_preferences() -> UserPreferences:
    return UserPreferences()


def load_preferences() -> UserPreferences:
    path = _prefs_path()
    if not path.exists():
        return default_preferences()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return UserPreferences(
            humor_percent=_clamp_pct(int(raw.get("humor_percent", 20))),
            character_mode=str(raw.get("character_mode", "")).strip()[:120],
            tone_hint=str(raw.get("tone_hint", "direct")).strip()[:32] or "direct",
            boundaries_mode=_normalize_boundaries(str(raw.get("boundaries_mode", "user_defined"))),
            profanity_level=_clamp_pct(int(raw.get("profanity_level", 40))),
            style_notes=str(raw.get("style_notes", "")).strip()[:400],
            autonomy_mode=_normalize_autonomy(str(raw.get("autonomy_mode", "hands_off"))),
            show_workflow=bool(raw.get("show_workflow", False)),
            hive_followups=bool(raw.get("hive_followups", True)),
            idle_research_assist=bool(raw.get("idle_research_assist", True)),
            accept_hive_tasks=bool(raw.get("accept_hive_tasks", True)),
            social_commons=bool(raw.get("social_commons", True)),
        )
    except Exception:
        return default_preferences()


def save_preferences(prefs: UserPreferences) -> Path:
    path = _prefs_path()
    payload = asdict(prefs)
    payload["humor_percent"] = _clamp_pct(int(payload.get("humor_percent", 20)))
    payload["profanity_level"] = _clamp_pct(int(payload.get("profanity_level", 40)))
    payload["boundaries_mode"] = _normalize_boundaries(str(payload.get("boundaries_mode", "user_defined")))
    payload["autonomy_mode"] = _normalize_autonomy(str(payload.get("autonomy_mode", "hands_off")))
    payload["show_workflow"] = bool(payload.get("show_workflow", False))
    payload["hive_followups"] = bool(payload.get("hive_followups", True))
    payload["idle_research_assist"] = bool(payload.get("idle_research_assist", True))
    payload["accept_hive_tasks"] = bool(payload.get("accept_hive_tasks", True))
    payload["social_commons"] = bool(payload.get("social_commons", True))
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def describe_preferences_for_context() -> str:
    prefs = load_preferences()
    fragments = [
        f"humor={prefs.humor_percent}/100",
        f"boundaries={prefs.boundaries_mode}",
        f"profanity={prefs.profanity_level}/100",
        f"autonomy={prefs.autonomy_mode}",
        f"show_workflow={'on' if prefs.show_workflow else 'off'}",
        f"hive_followups={'on' if prefs.hive_followups else 'off'}",
        f"idle_research_assist={'on' if prefs.idle_research_assist else 'off'}",
        f"accept_hive_tasks={'on' if prefs.accept_hive_tasks else 'off'}",
        f"social_commons={'on' if prefs.social_commons else 'off'}",
    ]
    if prefs.character_mode:
        fragments.append(f"character_mode={prefs.character_mode}")
    if prefs.style_notes:
        fragments.append(f"style_notes={prefs.style_notes}")
    return "; ".join(fragments)


def maybe_handle_preference_command(user_text: str) -> tuple[bool, str]:
    text = str(user_text or "").strip()
    if not text:
        return False, ""
    lowered = text.lower()

    if lowered in {"/prefs", "/preferences", "show preferences", "show my preferences"}:
        prefs = load_preferences()
        return True, (
            "Preferences active: "
            f"humor={prefs.humor_percent}/100, "
            f"boundaries={prefs.boundaries_mode}, "
            f"profanity={prefs.profanity_level}/100, "
            f"character={prefs.character_mode or 'default'}, "
            f"autonomy={prefs.autonomy_mode}, "
            f"workflow={'on' if prefs.show_workflow else 'off'}, "
            f"hive_followups={'on' if prefs.hive_followups else 'off'}, "
            f"idle_research_assist={'on' if prefs.idle_research_assist else 'off'}, "
            f"accept_hive_tasks={'on' if prefs.accept_hive_tasks else 'off'}, "
            f"social_commons={'on' if prefs.social_commons else 'off'}."
        )

    humor = _HUMOR_RE.search(text)
    if humor:
        prefs = load_preferences()
        prefs.humor_percent = _clamp_pct(int(humor.group(1)))
        save_preferences(prefs)
        return True, f"Humor set to {prefs.humor_percent}%."

    character = _CHAR_RE.match(text)
    if character:
        mode = character.group(1).strip()
        if mode:
            prefs = load_preferences()
            prefs.character_mode = mode[:120]
            save_preferences(prefs)
            return True, f"Character mode set: {prefs.character_mode}."

    boundaries = _BOUNDARIES_RE.match(text)
    if boundaries:
        prefs = load_preferences()
        prefs.boundaries_mode = _normalize_boundaries(boundaries.group(1))
        save_preferences(prefs)
        return True, f"Boundaries mode set to {prefs.boundaries_mode}."

    autonomy = _AUTONOMY_RE.match(text)
    if autonomy:
        prefs = load_preferences()
        prefs.autonomy_mode = _normalize_autonomy(autonomy.group(1))
        save_preferences(prefs)
        return True, _describe_autonomy_change(prefs.autonomy_mode)

    if _NO_MICRO_APPROVAL_RE.search(text) or _ONLY_SIGNIFICANT_APPROVAL_RE.search(text):
        prefs = load_preferences()
        prefs.autonomy_mode = "hands_off"
        save_preferences(prefs)
        return True, _describe_autonomy_change(prefs.autonomy_mode)

    profanity = _PROFANITY_RE.match(text)
    if profanity:
        prefs = load_preferences()
        prefs.profanity_level = _clamp_pct(int(profanity.group(1)))
        save_preferences(prefs)
        return True, f"Profanity level set to {prefs.profanity_level}%."

    if _SHOW_WORKFLOW_RE.match(text) or _looks_like_workflow_toggle(text, enable=True):
        prefs = load_preferences()
        prefs.show_workflow = True
        save_preferences(prefs)
        return True, "Workflow summaries enabled. I'll show the execution flow without exposing raw chain-of-thought."

    if _HIDE_WORKFLOW_RE.match(text) or _looks_like_workflow_toggle(text, enable=False):
        prefs = load_preferences()
        prefs.show_workflow = False
        save_preferences(prefs)
        return True, "Workflow summaries disabled."

    if _ENABLE_HIVE_FOLLOWUPS_RE.match(text):
        prefs = load_preferences()
        prefs.hive_followups = True
        save_preferences(prefs)
        return True, "Hive followups enabled. I’ll surface active research updates and new hive work in chat when it matters."

    if _DISABLE_HIVE_FOLLOWUPS_RE.match(text):
        prefs = load_preferences()
        prefs.hive_followups = False
        save_preferences(prefs)
        return True, "Hive followups disabled."

    if _ENABLE_IDLE_ASSIST_RE.match(text):
        prefs = load_preferences()
        prefs.idle_research_assist = True
        save_preferences(prefs)
        return True, "Idle research assist enabled. I’ll keep nudging about available hive research unless you tell me to stop."

    if _DISABLE_IDLE_ASSIST_RE.match(text):
        prefs = load_preferences()
        prefs.idle_research_assist = False
        save_preferences(prefs)
        return True, "Idle research assist disabled."

    if _ENABLE_HIVE_TASKS_RE.match(text):
        prefs = load_preferences()
        prefs.accept_hive_tasks = True
        save_preferences(prefs)
        return True, "Hive task intake enabled. I’ll stay visible in Hive stats and can accept swarm/public research tasks again."

    if _DISABLE_HIVE_TASKS_RE.match(text):
        prefs = load_preferences()
        prefs.accept_hive_tasks = False
        save_preferences(prefs)
        return True, "Hive task intake disabled. I’ll stay visible in Hive stats, but I won’t accept swarm/public research tasks until you re-enable it."

    if _ENABLE_SOCIAL_COMMONS_RE.match(text):
        prefs = load_preferences()
        prefs.social_commons = True
        save_preferences(prefs)
        return True, "Agent commons enabled. When I'm idle, I’ll keep the background curiosity and brainstorm lane alive."

    if _DISABLE_SOCIAL_COMMONS_RE.match(text):
        prefs = load_preferences()
        prefs.social_commons = False
        save_preferences(prefs)
        return True, "Agent commons disabled. I’ll stop the idle social and brainstorm lane."

    candidate = extract_requested_agent_name(text)
    if candidate:
        try:
            from core.identity_manager import update_local_persona
            from core.onboarding import force_rename

            force_rename(candidate)
            update_local_persona("default", display_name=candidate)
            return True, f"Alright. I'll go by {candidate} now."
        except Exception:
            return True, "Rename requested, but I couldn't persist it right now."

    return False, ""


def extract_requested_agent_name(user_text: str) -> str | None:
    text = str(user_text or "").strip()
    if not text:
        return None
    for pattern in _RENAME_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        candidate = _clean_requested_name(match.group(1))
        if _looks_like_real_name(candidate):
            return candidate
    return None


def hive_task_intake_enabled() -> bool:
    return bool(getattr(load_preferences(), "accept_hive_tasks", True))


def _looks_like_workflow_toggle(text: str, *, enable: bool) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    workflow_terms = (
        "workflow",
        "work flow",
        "thinking flow",
        "reasoning summary",
        "work log",
        "internal workflow",
        "internal workflows",
    )
    if not any(term in lowered for term in workflow_terms):
        return False
    disable_phrases = (
        "don't show",
        "do not show",
        "stop showing",
        "hide",
        "disable",
        "keep it to yourself",
        "keep that to yourself",
    )
    if enable:
        if any(phrase in lowered for phrase in disable_phrases):
            return False
        return any(
            phrase in lowered
            for phrase in (
                "show me",
                "show your",
                "enable",
                "turn on",
                "i need to see",
            )
        )
    return any(phrase in lowered for phrase in disable_phrases)


def _clean_requested_name(value: str) -> str:
    candidate = str(value or "").strip().strip("\"'")
    lower = candidate.lower()
    cut_markers = [
        " and my name is ",
        " and i'm ",
        " and i am ",
        ",",
        ".",
        "!",
        "?",
    ]
    cut_at = len(candidate)
    for marker in cut_markers:
        idx = lower.find(marker)
        if idx != -1:
            cut_at = min(cut_at, idx)
    cleaned = candidate[:cut_at].strip().strip("\"'")
    if cleaned.lower().endswith(" now"):
        cleaned = cleaned[:-4].strip()
    return cleaned


def _clamp_pct(value: int) -> int:
    return max(0, min(100, int(value)))


def _normalize_boundaries(value: str) -> str:
    val = value.strip().lower()
    if val in {"relaxed", "standard", "strict", "user_defined"}:
        return val
    return "user_defined"


def _normalize_autonomy(value: str) -> str:
    val = value.strip().lower().replace("_", "-")
    if val in {"hands-off", "handsoff"}:
        return "hands_off"
    if val in {"balanced", "strict"}:
        return val
    return "hands_off"


def _describe_autonomy_change(mode: str) -> str:
    normalized = _normalize_autonomy(mode)
    if normalized == "strict":
        return "Autonomy set to strict. I'll ask before any side-effect action or borderline risk."
    if normalized == "balanced":
        return "Autonomy set to balanced. I'll execute low-risk steps directly and ask before destructive or outward-facing actions."
    return "Autonomy set to hands-off. I'll move through research and low-risk execution without micro approvals, and only stop for destructive changes, leak risk, or significant security exposure."


def _looks_like_real_name(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"wrong", "bad", "stupid", "crazy", "broken"}:
        return False
    return bool(value.strip())
