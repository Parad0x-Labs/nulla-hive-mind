from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.privacy_guard import (
    SHARE_SCOPES,
    keyword_tokens,
    normalize_share_scope,
    parse_restricted_terms,
    share_scope_label,
    tokenize_restricted_terms,
)
from core.runtime_paths import data_path, project_path
from storage.dialogue_memory import record_dialogue_turn
from storage.db import get_connection


_MEMORY_FILE = "MEMORY.md"
_CONVERSATION_LOG_FILE = "conversation_log.jsonl"
_MEMORY_ENTRIES_FILE = "memory_entries.jsonl"
_SESSION_SUMMARIES_FILE = "session_summaries.jsonl"
_USER_HEURISTICS_FILE = "user_heuristics.jsonl"

_MAX_CONVERSATION_LOG_BYTES = 8 * 1024 * 1024
_MAX_MEMORY_INDEX_BYTES = 2 * 1024 * 1024
_MAX_SESSION_SUMMARY_BYTES = 2 * 1024 * 1024
_MAX_USER_HEURISTICS_BYTES = 512 * 1024

_REMEMBER_RE = re.compile(r"^(?:remember(?: that)?|note(?: that)?|store(?: this)?)\s+(.+)$", re.IGNORECASE)
_FORGET_RE = re.compile(r"^(?:forget|erase)\s+(.+)$", re.IGNORECASE)
_PRIVACY_STATUS_RE = re.compile(
    r"^(?:/privacy|show privacy|show share scope|show memory scope|what(?:'s| is) (?:the )?(?:privacy|share|memory) (?:scope|mode|setting)|is this (?:private|private vault|public|public commons|hive mind|shared pack))\??$",
    re.IGNORECASE,
)
_SCOPE_EXCEPT_RE = re.compile(r"\b(?:except|but keep|but preserve)\b(.+)$", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?:[\n\r]+|(?<=[.!?;])\s+)")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_'\-]+", re.IGNORECASE)
_HIVE_TASK_QUERY_HINT_RE = re.compile(
    r"\b(?:tasks?|queue|work|available|open|help|research|researches)\b",
    re.IGNORECASE,
)

_NAME_PATTERNS = [
    re.compile(r"\bmy name is\s+([a-z0-9][a-z0-9 '\-]{1,40})", re.IGNORECASE),
    re.compile(r"\bcall me\s+([a-z0-9][a-z0-9 '\-]{1,40})", re.IGNORECASE),
    re.compile(r"\bi go by\s+([a-z0-9][a-z0-9 '\-]{1,40})", re.IGNORECASE),
]
_STYLE_PATTERNS = [
    re.compile(r"\b(?:be|stay)\s+(?:more\s+)?(?:concise|brief|direct|blunt|honest|clear)\b", re.IGNORECASE),
    re.compile(r"\bkeep\s+(?:the\s+)?(?:answers|responses|reply|tone|style)\s+(?:concise|brief|direct|blunt|honest|clear)\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:fluff|hopium|copium|bullshit)\b", re.IGNORECASE),
    re.compile(r"\bbrutal(?:ly)?\s+honest\b", re.IGNORECASE),
]
_PREFERENCE_PATTERNS = [
    re.compile(r"\bi prefer\b", re.IGNORECASE),
    re.compile(r"\bi like\b", re.IGNORECASE),
    re.compile(r"\bi want you to\b", re.IGNORECASE),
    re.compile(r"\bfrom now on\b", re.IGNORECASE),
    re.compile(r"\balways\b.{0,60}\b(?:answer|respond|use|remember|be)\b", re.IGNORECASE),
    re.compile(r"\bnever\b.{0,60}\b(?:answer|respond|use|forget|be)\b", re.IGNORECASE),
]
_FACT_PATTERNS = [
    re.compile(r"\bi use\b", re.IGNORECASE),
    re.compile(r"\bi work on\b", re.IGNORECASE),
    re.compile(r"\bi(?:'m| am)\s+building\b", re.IGNORECASE),
    re.compile(r"\bmy project\b", re.IGNORECASE),
    re.compile(r"\bmy setup\b", re.IGNORECASE),
    re.compile(r"\bi maintain\b", re.IGNORECASE),
    re.compile(r"\bi live in\b", re.IGNORECASE),
    re.compile(r"\bi work in\b", re.IGNORECASE),
]
_EPHEMERAL_HINTS = (
    "fix this",
    "build this",
    "replace this",
    "change this",
    "do this now",
    "today",
    "right now",
    "currently",
)
_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "always",
    "another",
    "because",
    "before",
    "being",
    "between",
    "brief",
    "build",
    "change",
    "clear",
    "could",
    "direct",
    "every",
    "from",
    "have",
    "help",
    "honest",
    "keep",
    "like",
    "make",
    "need",
    "please",
    "project",
    "reply",
    "response",
    "responses",
    "should",
    "something",
    "still",
    "that",
    "their",
    "them",
    "there",
    "these",
    "they",
    "thing",
    "this",
    "those",
    "through",
    "want",
    "with",
    "would",
    "your",
}

_HEURISTIC_STYLE_MARKERS = {
    "concise_direct": ("concise", "brief", "direct", "clear", "blunt"),
    "brutal_honest": ("brutal", "brutally honest", "honest", "no fluff", "no hopium", "no copium", "no bullshit"),
}
_HEURISTIC_SOURCE_MARKERS = {
    "official_docs": ("official docs", "official documentation", "official sources"),
    "github_repos": ("github", "repo", "repos", "repository", "repositories"),
    "reputable_sources": ("reputable sources", "trusted sources", "public websites", "good sources"),
}
_HEURISTIC_STACK_MARKERS = {
    "python": ("python", "py"),
    "typescript": ("typescript",),
    "javascript": ("javascript", "node", "nodejs"),
    "rust": ("rust",),
    "go": ("golang", "go"),
}
_HEURISTIC_PROJECT_MARKERS = {
    "telegram_bot": ("telegram", "telegram bot", "tg bot", "bot api"),
    "discord_bot": ("discord", "discord bot"),
    "hive_swarm": ("hive", "swarm", "hive mind", "mesh"),
    "openclaw_runtime": ("openclaw", "nulla", "runtime"),
}
_HEURISTIC_ALWAYS_INCLUDE = {"response_style", "autonomy_preference", "source_preference", "preferred_stack"}
_HEURISTIC_BUILD_MARKERS = ("build", "building", "create", "making", "write", "implement", "scaffold", "work on", "project")
_HEURISTIC_AUTONOMY_MARKERS = (
    "do all this",
    "do it",
    "just do it",
    "start fixing",
    "carry on",
    "don't ask",
    "dont ask",
    "no micro approval",
    "test all",
)


def memory_path() -> Path:
    return data_path(_MEMORY_FILE)


def conversation_log_path() -> Path:
    return data_path(_CONVERSATION_LOG_FILE)


def memory_entries_path() -> Path:
    return data_path(_MEMORY_ENTRIES_FILE)


def session_summaries_path() -> Path:
    return data_path(_SESSION_SUMMARIES_FILE)


def user_heuristics_path() -> Path:
    return data_path(_USER_HEURISTICS_FILE)


def ensure_memory_files() -> None:
    path = memory_path()
    if not path.exists():
        template = _default_memory_template()
        path.write_text(template, encoding="utf-8")
    for extra_path in (conversation_log_path(), memory_entries_path(), session_summaries_path(), user_heuristics_path()):
        if not extra_path.exists():
            extra_path.write_text("", encoding="utf-8")
    _ensure_session_policy_table()


def _ensure_session_policy_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_memory_policies (
                session_id TEXT PRIMARY KEY,
                share_scope TEXT NOT NULL DEFAULT 'local_only',
                restricted_terms_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def session_memory_policy(session_id: str | None) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return {
            "session_id": "",
            "share_scope": "local_only",
            "realm_label": share_scope_label("local_only"),
            "restricted_terms": [],
            "updated_at": "",
        }
    _ensure_session_policy_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT session_id, share_scope, restricted_terms_json, updated_at
            FROM session_memory_policies
            WHERE session_id = ?
            LIMIT 1
            """,
            (normalized_session_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {
            "session_id": normalized_session_id,
            "share_scope": "local_only",
            "realm_label": share_scope_label("local_only"),
            "restricted_terms": [],
            "updated_at": "",
        }
    data = dict(row)
    try:
        restricted_terms = json.loads(data.get("restricted_terms_json") or "[]")
    except json.JSONDecodeError:
        restricted_terms = []
    return {
        "session_id": normalized_session_id,
        "share_scope": normalize_share_scope(str(data.get("share_scope") or "local_only")),
        "realm_label": share_scope_label(str(data.get("share_scope") or "local_only")),
        "restricted_terms": tokenize_restricted_terms(list(restricted_terms or [])),
        "updated_at": str(data.get("updated_at") or ""),
    }


def describe_session_memory_policy(session_id: str | None) -> str:
    policy = session_memory_policy(session_id)
    scope = policy["share_scope"]
    restricted_terms = list(policy.get("restricted_terms") or [])
    if scope == "hive_mind":
        base = "Session sharing: SHARED PACK. Generalized learned procedures may sync to the mesh after secret screening. Raw chat, memory extracts, and session summaries stay in the PRIVATE VAULT."
    elif scope == "public_knowledge":
        base = "Session sharing: HIVE/PUBLIC COMMONS. Generalized learned procedures may be published as public claims after secret screening. Raw chat, memory extracts, and session summaries stay in the PRIVATE VAULT."
    else:
        base = "Session sharing: PRIVATE VAULT. Chat history, extracted memory, summaries, and learned procedures stay on this node unless you explicitly reclassify the session."
    if restricted_terms:
        base += " Protected exceptions: " + ", ".join(restricted_terms[:6]) + "."
    return base


def set_session_memory_policy(
    session_id: str,
    *,
    share_scope: str,
    restricted_terms: list[str] | None = None,
) -> dict[str, Any]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        raise ValueError("session_id is required to set memory scope")
    normalized_scope = normalize_share_scope(share_scope)
    normalized_terms = tokenize_restricted_terms(restricted_terms)

    _ensure_session_policy_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO session_memory_policies (
                session_id, share_scope, restricted_terms_json, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                share_scope = excluded.share_scope,
                restricted_terms_json = excluded.restricted_terms_json,
                updated_at = excluded.updated_at
            """,
            (
                normalized_session_id,
                normalized_scope,
                json.dumps(normalized_terms, sort_keys=True),
                _utcnow(),
            ),
        )
        if _table_exists(conn, "local_tasks"):
            try:
                conn.execute(
                    """
                    UPDATE local_tasks
                    SET share_scope = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE session_id = ?
                    """,
                    (normalized_scope, normalized_session_id),
                )
            except Exception:
                pass
        if _table_exists(conn, "learning_shards"):
            try:
                conn.execute(
                    """
                    UPDATE learning_shards
                    SET share_scope = ?,
                        restricted_terms_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE origin_session_id = ?
                      AND source_type = 'local_generated'
                    """,
                    (
                        normalized_scope,
                        json.dumps(normalized_terms, sort_keys=True),
                        normalized_session_id,
                    ),
                )
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()

    stats = _reconcile_session_learning_scope(
        normalized_session_id,
        requested_scope=normalized_scope,
        restricted_terms=normalized_terms,
    )
    return {
        "session_id": normalized_session_id,
        "share_scope": normalized_scope,
        "realm_label": share_scope_label(normalized_scope),
        "restricted_terms": normalized_terms,
        **stats,
    }


def load_memory_excerpt(*, max_chars: int = 2200) -> str:
    ensure_memory_files()
    text = memory_path().read_text(encoding="utf-8", errors="replace").strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def append_conversation_event(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
    source_context: dict[str, Any] | None = None,
) -> None:
    ensure_memory_files()
    history = _normalized_history((source_context or {}).get("conversation_history"))
    policy = session_memory_policy(session_id)
    assistant_text = str(assistant_output or "").strip()
    payload = {
        "ts": _utcnow(),
        "session_id": session_id,
        "surface": str((source_context or {}).get("surface", "")),
        "platform": str((source_context or {}).get("platform", "")),
        "user": str(user_input or "")[:4000],
        "assistant": assistant_text[:8000],
        "history_message_count": len(history),
        "share_scope": policy["share_scope"],
        "realm_label": policy.get("realm_label") or share_scope_label(policy["share_scope"]),
        "restricted_terms": list(policy.get("restricted_terms") or []),
    }
    path = conversation_log_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _trim_jsonl_file(path, max_bytes=_MAX_CONVERSATION_LOG_BYTES)
    _record_assistant_dialogue_turn(
        session_id=session_id,
        assistant_output=assistant_text,
    )
    _auto_capture_memory(session_id=session_id, user_input=user_input)
    _update_user_heuristics(session_id=session_id, user_input=user_input)
    _detect_implicit_feedback(
        session_id=session_id,
        user_input=user_input,
        assistant_output=assistant_output,
    )
    _update_session_summary(
        session_id=session_id,
        user_input=user_input,
        assistant_output=assistant_output,
    )


def maybe_handle_memory_command(user_text: str, *, session_id: str | None = None) -> tuple[bool, str]:
    text = str(user_text or "").strip()
    if not text:
        return False, ""
    lowered = text.lower()
    scope_command = _parse_session_scope_command(text)
    if scope_command is not None:
        action = str(scope_command.get("action") or "")
        if action == "show":
            return True, describe_session_memory_policy(session_id)
        if not session_id:
            return True, "I need an active session before I can change the share scope."
        result = set_session_memory_policy(
            str(session_id),
            share_scope=str(scope_command.get("share_scope") or "local_only"),
            restricted_terms=list(scope_command.get("restricted_terms") or []),
        )
        scope = result["share_scope"]
        if scope == "hive_mind":
            label = "SHARED PACK"
            scope_note = "Generalized learnings from this session can sync to the mesh after secret screening. Raw chat remains in the PRIVATE VAULT."
        elif scope == "public_knowledge":
            label = "HIVE/PUBLIC COMMONS"
            scope_note = "Generalized learnings from this session can be published as public claims after secret screening. Raw chat remains in the PRIVATE VAULT."
        else:
            label = "PRIVATE VAULT"
            scope_note = "Everything from this session stays on this node unless you reclassify it."
        protected = ""
        if result.get("restricted_terms"):
            protected = " Protected exceptions: " + ", ".join(list(result["restricted_terms"])[:6]) + "."
        stats = (
            f" Existing session shards updated: {int(result.get('updated_shards') or 0)}"
            f", shared now: {int(result.get('registered_shards') or 0)}"
            f", forced local by privacy guard: {int(result.get('blocked_shards') or 0)}."
        )
        return True, f"Session scope set to {label}. {scope_note}{protected}{stats}"
    if lowered in {"/memory", "what do you remember", "show memory"}:
        summary = summarize_memory(limit=8)
        if not summary:
            return True, "Memory is currently empty. Tell me what to remember."
        return True, "What I remember:\n" + "\n".join(f"- {line}" for line in summary)

    remember = _REMEMBER_RE.match(text)
    if remember:
        fact = remember.group(1).strip()
        if len(fact) < 3:
            return True, "Memory update skipped: fact is too short."
        added = add_memory_fact(fact)
        if added:
            return True, "Locked in. I’ll remember that."
        return True, "I already had that in memory."

    forget = _FORGET_RE.match(text)
    if forget:
        token = forget.group(1).strip()
        if len(token) < 2:
            return True, "Forget command skipped: provide a clearer keyword."
        removed = forget_memory(token)
        return True, f"Forget applied. Removed {removed} memory entr{'y' if removed == 1 else 'ies'}."

    return False, ""


def add_memory_fact(
    fact: str,
    *,
    category: str = "fact",
    session_id: str | None = None,
    source: str = "manual",
    confidence: float = 0.85,
    keywords: list[str] | None = None,
    share_scope: str | None = None,
) -> bool:
    ensure_memory_files()
    clean = _sanitize_fact(fact)
    if not clean:
        return False
    normalized_share_scope = normalize_share_scope(
        share_scope or session_memory_policy(session_id).get("share_scope"),
        default="local_only",
    )

    if str(category or "").strip().lower() == "name":
        _replace_name_memory(clean)

    path = memory_path()
    text = path.read_text(encoding="utf-8", errors="replace")
    line = f"- [{_today_iso()}] {clean}"
    if line in text:
        return False
    if clean.lower() in text.lower():
        return False

    marker = "## Learned Knowledge"
    if marker not in text:
        text = text.rstrip() + "\n\n" + marker + "\n\n"
    new_text = text.rstrip() + "\n" + line + "\n"
    path.write_text(new_text, encoding="utf-8")
    _record_memory_entry(
        clean,
        category=category,
        session_id=session_id,
        source=source,
        confidence=confidence,
        keywords=keywords,
        share_scope=normalized_share_scope,
    )
    return True


def forget_memory(keyword: str) -> int:
    ensure_memory_files()
    token = keyword.strip().lower()
    if not token:
        return 0
    path = memory_path()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip().lower()
        if stripped.startswith("- [") and token in stripped:
            removed += 1
            continue
        kept.append(line)
    path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")
    _forget_structured_memory(token)
    return removed


def summarize_memory(*, limit: int = 10) -> list[str]:
    ensure_memory_files()
    lines = memory_path().read_text(encoding="utf-8", errors="replace").splitlines()
    learned = [ln.strip()[2:] for ln in lines if ln.strip().startswith("- ")]
    return learned[-max(1, int(limit)) :]


def recent_conversation_events(session_id: str, *, limit: int = 6) -> list[dict[str, Any]]:
    ensure_memory_files()
    rows = _load_jsonl(conversation_log_path())
    out: list[dict[str, Any]] = []
    for row in reversed(rows):
        if str(row.get("session_id") or "") != str(session_id):
            continue
        out.append(row)
        if len(out) >= max(1, int(limit)):
            break
    return list(reversed(out))


def search_relevant_memory(
    query_text: str,
    *,
    topic_hints: list[str] | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    query_tokens = set(_keyword_tokens(" ".join([query_text, *(topic_hints or [])])))
    rows = _combined_memory_entries()
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        item_tokens = set(row.get("keywords") or _keyword_tokens(str(row.get("text") or "")))
        overlap = len(query_tokens & item_tokens) if query_tokens else 0
        if query_tokens and overlap <= 0:
            continue
        semantic = overlap / max(1, len(query_tokens)) if query_tokens else 0.0
        recency = _recency_score(str(row.get("created_at") or ""))
        category_boost = 0.08 if str(row.get("category") or "") in {"instruction", "preference"} else 0.0
        confidence = max(0.2, min(1.0, float(row.get("confidence") or 0.7)))
        score = (0.55 * semantic) + (0.20 * confidence) + (0.17 * recency) + category_boost
        ranked.append((score, {**row, "score": round(score, 4)}))
    ranked.sort(key=lambda item: (item[0], str(item[1].get("created_at") or "")), reverse=True)
    return [row for _, row in ranked[: max(1, int(limit))]]


def search_session_summaries(
    query_text: str,
    *,
    topic_hints: list[str] | None = None,
    limit: int = 3,
    exclude_session_id: str | None = None,
) -> list[dict[str, Any]]:
    query_tokens = set(_keyword_tokens(" ".join([query_text, *(topic_hints or [])])))
    latest_by_session: dict[str, dict[str, Any]] = {}
    for row in _load_jsonl(session_summaries_path()):
        session_id = str(row.get("session_id") or "").strip()
        if not session_id:
            continue
        previous = latest_by_session.get(session_id)
        if previous is None or str(row.get("created_at") or "") > str(previous.get("created_at") or ""):
            latest_by_session[session_id] = row

    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in latest_by_session.values():
        session_id = str(row.get("session_id") or "")
        if exclude_session_id and session_id == exclude_session_id:
            continue
        item_tokens = set(row.get("keywords") or _keyword_tokens(str(row.get("summary") or "")))
        overlap = len(query_tokens & item_tokens) if query_tokens else 0
        if query_tokens and overlap <= 0:
            continue
        semantic = overlap / max(1, len(query_tokens)) if query_tokens else 0.0
        recency = _recency_score(str(row.get("created_at") or ""))
        score = (0.60 * semantic) + (0.25 * recency) + 0.15 * 0.68
        ranked.append((score, {**row, "score": round(score, 4)}))
    ranked.sort(key=lambda item: (item[0], str(item[1].get("created_at") or "")), reverse=True)
    return [row for _, row in ranked[: max(1, int(limit))]]


def search_user_heuristics(
    query_text: str,
    *,
    topic_hints: list[str] | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    query_tokens = set(_keyword_tokens(" ".join([query_text, *(topic_hints or [])])))
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in _load_jsonl(user_heuristics_path()):
        category = str(row.get("category") or "").strip().lower()
        item_tokens = set(row.get("keywords") or _keyword_tokens(str(row.get("text") or "")))
        overlap = len(query_tokens & item_tokens) if query_tokens else 0
        semantic = overlap / max(1, len(query_tokens)) if query_tokens else 0.0
        if query_tokens and overlap <= 0 and category not in _HEURISTIC_ALWAYS_INCLUDE:
            continue
        mentions = max(1, int(row.get("mentions") or 1))
        strength = min(1.0, mentions / 3.0)
        recency = _recency_score(str(row.get("updated_at") or row.get("created_at") or ""))
        confidence = max(0.35, min(1.0, float(row.get("confidence") or 0.7)))
        category_boost = 0.10 if category in _HEURISTIC_ALWAYS_INCLUDE else 0.0
        score = (0.48 * semantic) + (0.18 * strength) + (0.16 * confidence) + (0.12 * recency) + category_boost
        ranked.append((score, {**row, "score": round(score, 4)}))
    ranked.sort(
        key=lambda item: (
            item[0],
            int(item[1].get("mentions") or 0),
            str(item[1].get("updated_at") or ""),
        ),
        reverse=True,
    )
    return [row for _, row in ranked[: max(1, int(limit))]]


def _default_memory_template() -> str:
    template_path = project_path("MEMORY.md")
    if template_path.exists():
        text = template_path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return text + "\n"
    return (
        "# NULLA Persistent Memory\n\n"
        "## Identity\n\n"
        "- **My name**: NULLA\n"
        "- **Owner's name**: unknown\n\n"
        "## Privacy Pact\n\n"
        "- Not set yet.\n\n"
        "## Learned Knowledge\n\n"
        "<!-- New memories append below -->\n"
    )


def _sanitize_fact(text: str) -> str:
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return ""
    clean = clean[:320]
    return clean


def _normalized_history(history: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in list(history or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = " ".join(str(item.get("content") or "").split()).strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content[:600]})
    return normalized


def _record_assistant_dialogue_turn(*, session_id: str, assistant_output: str) -> str | None:
    clean = str(assistant_output or "").strip()
    if not clean:
        return None
    normalized = " ".join(clean.split()).strip()
    return record_dialogue_turn(
        session_id,
        raw_input=clean,
        normalized_input=normalized,
        reconstructed_input=clean,
        speaker_role="assistant",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=1.0,
        quality_flags=[],
    )


_SHORTHAND_PATTERNS = [
    re.compile(r"\bwhen i say\s+[\"']?(.+?)[\"']?\s*,?\s*(?:i mean|it means|that means)\s+[\"']?(.+?)[\"']?\s*$", re.IGNORECASE),
    re.compile(r"[\"'](.+?)[\"']\s+means\s+[\"'](.+?)[\"']", re.IGNORECASE),
    re.compile(r"\bby\s+[\"'](.+?)[\"']\s+i mean\s+[\"']?(.+?)[\"']?\s*$", re.IGNORECASE),
]


def _auto_capture_memory(*, session_id: str, user_input: str) -> None:
    if not _should_auto_extract(user_input):
        return

    _auto_learn_shorthand(session_id=session_id, user_input=user_input)

    seen: set[str] = set()
    for candidate in _extract_memory_candidates(user_input):
        text = _sanitize_fact(str(candidate.get("text") or ""))
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        add_memory_fact(
            text,
            category=str(candidate.get("category") or "fact"),
            session_id=session_id,
            source="auto_dialogue",
            confidence=float(candidate.get("confidence") or 0.78),
            keywords=list(candidate.get("keywords") or []),
        )


def _auto_learn_shorthand(*, session_id: str, user_input: str) -> None:
    """Auto-detect and learn user shorthands from natural language."""
    text = str(user_input or "").strip()
    if not text or len(text) < 10:
        return
    for pattern in _SHORTHAND_PATTERNS:
        match = pattern.search(text)
        if match:
            term = match.group(1).strip()
            canonical = match.group(2).strip()
            if term and canonical and len(term) <= 40 and len(canonical) <= 80:
                from core.human_input_adapter import learn_user_shorthand
                learn_user_shorthand(term, canonical, session_id=session_id)
                add_memory_fact(
                    f'Shorthand: "{term}" means "{canonical}".',
                    category="shorthand",
                    session_id=session_id,
                    source="auto_dialogue",
                    confidence=0.90,
                    keywords=_keyword_tokens(f"{term} {canonical}"),
                )
                break


def _update_user_heuristics(*, session_id: str, user_input: str) -> None:
    candidates = _extract_user_heuristic_candidates(user_input)
    _apply_feedback_to_heuristics(session_id=session_id)
    if not candidates:
        return
    existing = {
        str(row.get("heuristic_id") or "").strip(): row
        for row in _load_jsonl(user_heuristics_path())
        if str(row.get("heuristic_id") or "").strip()
    }
    created_at = _utcnow()
    for candidate in candidates:
        heuristic_id = str(candidate.get("heuristic_id") or "").strip()
        if not heuristic_id:
            continue
        previous = dict(existing.get(heuristic_id) or {})
        mentions = int(previous.get("mentions") or 0) + 1
        confidence = max(float(previous.get("confidence") or 0.0), float(candidate.get("confidence") or 0.72))
        merged_keywords = _keyword_tokens(
            " ".join(
                [
                    " ".join(str(item) for item in list(previous.get("keywords") or [])),
                    " ".join(str(item) for item in list(candidate.get("keywords") or [])),
                    str(candidate.get("text") or ""),
                ]
            ),
            limit=18,
        )
        existing[heuristic_id] = {
            "heuristic_id": heuristic_id,
            "category": str(candidate.get("category") or previous.get("category") or "heuristic"),
            "signal": str(candidate.get("signal") or previous.get("signal") or heuristic_id),
            "text": str(candidate.get("text") or previous.get("text") or "").strip(),
            "confidence": round(confidence, 4),
            "mentions": mentions,
            "keywords": merged_keywords,
            "session_id": session_id,
            "created_at": str(previous.get("created_at") or created_at),
            "updated_at": created_at,
        }
    rows = sorted(
        existing.values(),
        key=lambda row: (str(row.get("updated_at") or ""), int(row.get("mentions") or 0)),
        reverse=True,
    )
    _rewrite_jsonl(user_heuristics_path(), rows[:64])
    _trim_jsonl_file(user_heuristics_path(), max_bytes=_MAX_USER_HEURISTICS_BYTES)


def _apply_feedback_to_heuristics(*, session_id: str) -> None:
    """Adjust heuristic confidence based on accumulated feedback."""
    try:
        from storage.dialogue_memory import feedback_stats
        stats = feedback_stats(lookback_days=7)
        if stats["total"] < 3:
            return
        rejection_rate = 0.0
        by_type = stats.get("by_type") or {}
        rejections = by_type.get("implicit_rejection") or {}
        approvals = by_type.get("implicit_approval") or {}
        total_feedback = int(rejections.get("count") or 0) + int(approvals.get("count") or 0)
        if total_feedback > 0:
            rejection_rate = int(rejections.get("count") or 0) / total_feedback

        if rejection_rate > 0.6:
            rows = _load_jsonl(user_heuristics_path())
            for row in rows:
                if str(row.get("category") or "") == "response_style":
                    old_conf = float(row.get("confidence") or 0.5)
                    row["confidence"] = round(max(0.3, old_conf - 0.05), 4)
            _rewrite_jsonl(user_heuristics_path(), rows[:64])
    except Exception:
        pass


_NEGATIVE_FEEDBACK_PATTERNS = [
    re.compile(r"\bno,?\s+(?:that'?s?\s+)?(?:not|wrong|incorrect)\b", re.IGNORECASE),
    re.compile(r"\bthat'?s?\s+(?:not|wrong|incorrect|bad|off)\b", re.IGNORECASE),
    re.compile(r"\byou'?re\s+(?:wrong|incorrect|off|mistaken)\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:said|meant|asked|wanted)\b", re.IGNORECASE),
    re.compile(r"\bnot\s+what\s+i\s+(?:asked|meant|wanted)\b", re.IGNORECASE),
    re.compile(r"\btry\s+again\b", re.IGNORECASE),
    re.compile(r"\bwrong\s+(?:answer|response)\b", re.IGNORECASE),
]
_POSITIVE_FEEDBACK_PATTERNS = [
    re.compile(r"\b(?:perfect|exactly|correct|right|great|thanks|nice|good job|well done)\b", re.IGNORECASE),
    re.compile(r"\bthat'?s?\s+(?:it|right|correct|perfect|exactly)\b", re.IGNORECASE),
    re.compile(r"\byou'?re\s+right\b", re.IGNORECASE),
]


def _detect_implicit_feedback(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
) -> None:
    """Detect implicit approval/rejection in user responses and record as feedback."""
    text = str(user_input or "").strip()
    if not text or len(text) < 3:
        return

    from storage.dialogue_memory import record_response_feedback

    lowered = text.lower()
    is_negative = any(p.search(lowered) for p in _NEGATIVE_FEEDBACK_PATTERNS)
    is_positive = any(p.search(lowered) for p in _POSITIVE_FEEDBACK_PATTERNS)

    if is_negative:
        record_response_feedback(
            session_id,
            feedback_type="implicit_rejection",
            feedback_value=-0.6,
            user_correction=text[:500] if len(text) > 20 else None,
            context_snapshot=(assistant_output or "")[:300],
        )
    elif is_positive and len(text) < 80:
        record_response_feedback(
            session_id,
            feedback_type="implicit_approval",
            feedback_value=0.6,
            context_snapshot=(assistant_output or "")[:300],
        )


def _extract_user_heuristic_candidates(user_input: str) -> list[dict[str, Any]]:
    lowered = " ".join(str(user_input or "").split()).strip().lower()
    if not lowered or len(lowered) < 10:
        return []
    out: list[dict[str, Any]] = []

    for signal, markers in _HEURISTIC_STYLE_MARKERS.items():
        if any(marker in lowered for marker in markers):
            out.append(
                {
                    "heuristic_id": f"response_style:{signal}",
                    "category": "response_style",
                    "signal": signal,
                    "text": _heuristic_text("response_style", signal),
                    "confidence": 0.86 if signal == "brutal_honest" else 0.80,
                    "keywords": _keyword_tokens(_heuristic_text("response_style", signal)),
                }
            )

    for signal, markers in _HEURISTIC_SOURCE_MARKERS.items():
        if any(marker in lowered for marker in markers):
            out.append(
                {
                    "heuristic_id": f"source_preference:{signal}",
                    "category": "source_preference",
                    "signal": signal,
                    "text": _heuristic_text("source_preference", signal),
                    "confidence": 0.78,
                    "keywords": _keyword_tokens(_heuristic_text("source_preference", signal)),
                }
            )

    wants_build = any(marker in lowered for marker in _HEURISTIC_BUILD_MARKERS)
    for signal, markers in _HEURISTIC_STACK_MARKERS.items():
        if any(re.search(rf"\b{re.escape(marker)}\b", lowered) for marker in markers) and wants_build:
            out.append(
                {
                    "heuristic_id": f"preferred_stack:{signal}",
                    "category": "preferred_stack",
                    "signal": signal,
                    "text": _heuristic_text("preferred_stack", signal),
                    "confidence": 0.76,
                    "keywords": _keyword_tokens(_heuristic_text("preferred_stack", signal)),
                }
            )

    for signal, markers in _HEURISTIC_PROJECT_MARKERS.items():
        if any(marker in lowered for marker in markers):
            out.append(
                {
                    "heuristic_id": f"project_focus:{signal}",
                    "category": "project_focus",
                    "signal": signal,
                    "text": _heuristic_text("project_focus", signal),
                    "confidence": 0.74,
                    "keywords": _keyword_tokens(_heuristic_text("project_focus", signal)),
                }
            )

    if any(marker in lowered for marker in _HEURISTIC_AUTONOMY_MARKERS):
        out.append(
            {
                "heuristic_id": "autonomy_preference:hands_off",
                "category": "autonomy_preference",
                "signal": "hands_off",
                "text": _heuristic_text("autonomy_preference", "hands_off"),
                "confidence": 0.82,
                "keywords": _keyword_tokens(_heuristic_text("autonomy_preference", "hands_off")),
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in out:
        heuristic_id = str(item.get("heuristic_id") or "").strip()
        if not heuristic_id or heuristic_id in seen:
            continue
        seen.add(heuristic_id)
        deduped.append(item)
    return deduped


def _extract_memory_candidates(user_input: str) -> list[dict[str, Any]]:
    text = " ".join(str(user_input or "").split()).strip()
    if not text:
        return []

    out: list[dict[str, Any]] = []

    for pattern in _NAME_PATTERNS:
        for match in pattern.finditer(text):
            name = _clean_name(match.group(1))
            if not name:
                continue
            out.append(
                {
                    "text": f"Operator name is {name}.",
                    "category": "name",
                    "confidence": 0.96,
                    "keywords": _keyword_tokens(f"name {name} operator"),
                }
            )

    for sentence in _candidate_sentences(text):
        clean = _sanitize_fact(sentence)
        if not clean or _should_skip_memory_sentence(clean):
            continue
        if any(pattern.search(clean) for pattern in _STYLE_PATTERNS):
            out.append(
                {
                    "text": clean,
                    "category": "instruction",
                    "confidence": 0.88,
                    "keywords": _keyword_tokens(clean),
                }
            )
            continue
        if any(pattern.search(clean) for pattern in _PREFERENCE_PATTERNS):
            out.append(
                {
                    "text": clean,
                    "category": "preference",
                    "confidence": 0.82,
                    "keywords": _keyword_tokens(clean),
                }
            )
            continue
        if any(pattern.search(clean) for pattern in _FACT_PATTERNS):
            out.append(
                {
                    "text": clean,
                    "category": "fact",
                    "confidence": 0.76,
                    "keywords": _keyword_tokens(clean),
                }
            )
    return out


def _candidate_sentences(text: str) -> list[str]:
    rough = _SENTENCE_SPLIT_RE.split(text)
    parts: list[str] = []
    for chunk in rough:
        chunk = chunk.strip()
        if not chunk:
            continue
        parts.extend(
            piece.strip()
            for piece in re.split(
                r",\s+(?=(?:keep|be|stay|please|always|never|from now on|i prefer|i like|i want you to|i use|i work on|my project|my setup|call me|my name is))",
                chunk,
                flags=re.IGNORECASE,
            )
            if piece.strip()
        )
    return parts


def _should_auto_extract(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if lowered.startswith(("remember ", "remember that ", "forget ", "erase ", "/memory")):
        return False
    if len(lowered) < 12:
        return False
    return True


def _should_skip_memory_sentence(sentence: str) -> bool:
    lowered = sentence.lower()
    if sentence.endswith("?"):
        return True
    if len(sentence) < 12:
        return True
    if any(hint in lowered for hint in _EPHEMERAL_HINTS):
        return True
    return False


def _clean_name(value: str) -> str:
    candidate = str(value or "").strip().strip("\"'.,!?")
    if not candidate:
        return ""
    for marker in (" and ", " because ", " but ", " please ", " for "):
        idx = candidate.lower().find(marker)
        if idx != -1:
            candidate = candidate[:idx].strip()
    candidate = candidate[:48].strip()
    if not candidate or candidate.lower() in {"nulla", "assistant", "you"}:
        return ""
    parts = [piece for piece in candidate.split() if piece]
    if not parts:
        return ""
    return " ".join(part[:1].upper() + part[1:] for part in parts[:3])


def _record_memory_entry(
    text: str,
    *,
    category: str,
    session_id: str | None,
    source: str,
    confidence: float,
    keywords: list[str] | None,
    share_scope: str | None,
) -> None:
    ensure_memory_files()
    clean = _sanitize_fact(text)
    if not clean:
        return
    normalized = clean.lower()
    existing = _load_jsonl(memory_entries_path())
    if any(str(row.get("text") or "").strip().lower() == normalized for row in reversed(existing[-200:])):
        return
    payload = {
        "created_at": _utcnow(),
        "text": clean,
        "category": str(category or "fact"),
        "session_id": str(session_id or ""),
        "source": str(source or "manual"),
        "confidence": max(0.2, min(1.0, float(confidence))),
        "keywords": list(keywords or _keyword_tokens(clean))[:16],
        "share_scope": normalize_share_scope(share_scope, default="local_only"),
    }
    _append_jsonl(memory_entries_path(), payload)
    _trim_jsonl_file(memory_entries_path(), max_bytes=_MAX_MEMORY_INDEX_BYTES)


def _forget_structured_memory(token: str) -> None:
    for path in (memory_entries_path(), session_summaries_path()):
        rows = _load_jsonl(path)
        kept = [row for row in rows if token not in json.dumps(row, ensure_ascii=False).lower()]
        _rewrite_jsonl(path, kept)


def _combined_memory_entries() -> list[dict[str, Any]]:
    ensure_memory_files()
    entries = _load_jsonl(memory_entries_path())
    seen = {str(row.get("text") or "").strip().lower() for row in entries}
    for row in _legacy_memory_entries():
        text_key = str(row.get("text") or "").strip().lower()
        if text_key and text_key not in seen:
            entries.append(row)
            seen.add(text_key)
    return entries


def _legacy_memory_entries() -> list[dict[str, Any]]:
    lines = memory_path().read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        text = stripped[2:].strip()
        created_at = ""
        match = re.match(r"^\[(\d{4}-\d{2}-\d{2})\]\s+(.+)$", text)
        if match:
            created_at = match.group(1)
            text = match.group(2).strip()
        clean = _sanitize_fact(text)
        if not clean:
            continue
        out.append(
            {
                "created_at": created_at,
                "text": clean,
                "category": "fact",
                "session_id": "",
                "source": "legacy_markdown",
                "confidence": 0.74,
                "keywords": _keyword_tokens(clean),
                "share_scope": "local_only",
            }
        )
    return out


def _update_session_summary(*, session_id: str, user_input: str, assistant_output: str) -> None:
    recent = recent_conversation_events(session_id, limit=6)
    if not recent:
        return
    recent_user = [str(row.get("user") or "").strip() for row in recent if str(row.get("user") or "").strip()][-3:]
    if not recent_user:
        return
    topic_terms = _keyword_tokens(" ".join(recent_user))[:6]
    memory_hits = search_relevant_memory(" ".join(recent_user[-2:]), limit=2)
    last_assistant = _sanitize_fact(assistant_output) or _sanitize_fact(str(recent[-1].get("assistant") or ""))

    parts: list[str] = []
    if topic_terms:
        parts.append(f"Session topics: {', '.join(topic_terms)}.")
    parts.append("Recent asks: " + " | ".join(_trim_text(item, 100) for item in recent_user) + ".")
    if memory_hits:
        parts.append(
            "Relevant durable memory: "
            + "; ".join(_trim_text(str(item.get("text") or ""), 90) for item in memory_hits)
            + "."
        )
    if last_assistant:
        parts.append(f"Last assistant outcome: {_trim_text(last_assistant, 140)}.")

    summary = " ".join(part for part in parts if part.strip()).strip()
    if not summary:
        return

    latest: dict[str, Any] | None = None
    for row in reversed(_load_jsonl(session_summaries_path())):
        if str(row.get("session_id") or "") == str(session_id):
            latest = row
            break
    if latest and str(latest.get("summary") or "") == summary:
        return

    payload = {
        "created_at": _utcnow(),
        "session_id": str(session_id),
        "summary": summary,
        "keywords": _keyword_tokens(summary),
        "turn_count": len(recent),
        "share_scope": session_memory_policy(session_id)["share_scope"],
        "realm_label": share_scope_label(session_memory_policy(session_id)["share_scope"]),
        "restricted_terms": list(session_memory_policy(session_id).get("restricted_terms") or []),
    }
    _append_jsonl(session_summaries_path(), payload)
    _trim_jsonl_file(session_summaries_path(), max_bytes=_MAX_SESSION_SUMMARY_BYTES)


def _parse_session_scope_command(text: str) -> dict[str, Any] | None:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return None
    lowered = cleaned.lower().strip(" .!?")
    if _PRIVACY_STATUS_RE.match(cleaned):
        return {"action": "show"}

    restricted_terms: list[str] = []
    except_match = _SCOPE_EXCEPT_RE.search(cleaned)
    if except_match:
        restricted_terms = parse_restricted_terms(except_match.group(1))
        lowered = cleaned[: except_match.start()].strip().lower().strip(" .!?")

    if lowered in {"private", "private vault", "local only", "keep this private", "this conversation is private", "store this locally", "only local", "locked"}:
        return {"action": "set", "share_scope": "local_only", "restricted_terms": restricted_terms}
    if _looks_like_hive_task_query(lowered):
        return None
    if lowered in {"hive mind", "shared pack", "friend-swarm", "friend swarm", "mesh-share"} or any(
        phrase in lowered
        for phrase in (
            "share with hive",
            "share this with hive",
            "set shared pack",
            "set hive mind",
            "switch to shared pack",
            "switch to hive mind",
            "use shared pack",
            "use hive mind",
            "this is hive mind",
            "this is shared pack",
        )
    ):
        return {"action": "set", "share_scope": "hive_mind", "restricted_terms": restricted_terms}
    if lowered in {"public knowledge", "public commons", "hive/public commons"} or any(
        phrase in lowered
        for phrase in (
            "make this public",
            "this is public knowledge",
            "publish this knowledge",
            "set public commons",
            "switch to public commons",
            "use public commons",
        )
    ):
        return {"action": "set", "share_scope": "public_knowledge", "restricted_terms": restricted_terms}
    if any(phrase in lowered for phrase in ("private forever", "keep this local", "keep this private")):
        return {"action": "set", "share_scope": "local_only", "restricted_terms": restricted_terms}
    return None


def _looks_like_hive_task_query(lowered: str) -> bool:
    text = str(lowered or "").strip().lower()
    if "hive mind" not in text and "shared pack" not in text and "public commons" not in text:
        return False
    if not _HIVE_TASK_QUERY_HINT_RE.search(text):
        return False
    return any(
        word in text
        for word in (
            "task",
            "tasks",
            "queue",
            "available",
            "open",
            "help with",
            "help",
            "work on",
            "work",
            "research",
        )
    )


def _replace_name_memory(new_name_fact: str) -> None:
    ensure_memory_files()
    path = memory_path()
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    kept = [line for line in lines if "operator name is " not in line.lower()]

    extracted_name = ""
    name_match = re.search(r"operator name is\s+(.+?)\.?$", new_name_fact, re.IGNORECASE)
    if name_match:
        extracted_name = name_match.group(1).strip()

    if extracted_name:
        updated: list[str] = []
        identity_updated = False
        for line in kept:
            if line.strip().startswith("- **Owner's name**:"):
                updated.append(f"- **Owner's name**: {extracted_name}")
                identity_updated = True
            elif line.strip().startswith("- **My name**:"):
                updated.append(f"- **My name**: {extracted_name}")
                identity_updated = True
            else:
                updated.append(line)
        if not identity_updated:
            for i, line in enumerate(updated):
                if line.strip() == "## Identity":
                    updated.insert(i + 1, f"- **Owner's name**: {extracted_name}")
                    identity_updated = True
                    break
        kept = updated

    path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")

    rows = [
        row
        for row in _load_jsonl(memory_entries_path())
        if str(row.get("category") or "") != "name" and "operator name is " not in str(row.get("text") or "").lower()
    ]
    _rewrite_jsonl(memory_entries_path(), rows)


def _reconcile_session_learning_scope(
    session_id: str,
    *,
    requested_scope: str,
    restricted_terms: list[str],
) -> dict[str, int]:
    from core.knowledge_registry import register_local_shard, withdraw_local_shard

    conn = get_connection()
    try:
        if not _table_exists(conn, "learning_shards"):
            return {"updated_shards": 0, "registered_shards": 0, "blocked_shards": 0}
        try:
            rows = conn.execute(
                """
                SELECT shard_id, share_scope
                FROM learning_shards
                WHERE origin_session_id = ?
                  AND source_type = 'local_generated'
                ORDER BY updated_at DESC
                """,
                (session_id,),
            ).fetchall()
        except Exception:
            return {"updated_shards": 0, "registered_shards": 0, "blocked_shards": 0}
    finally:
        conn.close()

    updated = 0
    registered = 0
    blocked = 0
    normalized_scope = normalize_share_scope(requested_scope)
    for row in rows:
        shard_id = str(row["shard_id"])
        updated += 1
        if normalized_scope == "local_only":
            withdraw_local_shard(shard_id)
            continue
        manifest = register_local_shard(shard_id, restricted_terms=restricted_terms)
        if manifest:
            registered += 1
            continue
        blocked += 1
        conn = get_connection()
        try:
            if _table_exists(conn, "learning_shards"):
                conn.execute(
                    """
                    UPDATE learning_shards
                    SET share_scope = 'local_only',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE shard_id = ?
                    """,
                    (shard_id,),
                )
                conn.commit()
        finally:
            conn.close()
        withdraw_local_shard(shard_id)
    return {"updated_shards": updated, "registered_shards": registered, "blocked_shards": blocked}


def _keyword_tokens(text: str, *, limit: int = 16) -> list[str]:
    base = keyword_tokens(str(text or ""), limit=max(limit * 2, limit))
    out: list[str] = []
    for token in base:
        normalized = token.strip("'")
        if len(normalized) < 3 or normalized in _STOPWORDS:
            continue
        out.append(normalized)
        if len(out) >= limit:
            break
    return out


def _heuristic_text(category: str, signal: str) -> str:
    mapping = {
        ("response_style", "concise_direct"): "The operator prefers concise, direct answers with low filler.",
        ("response_style", "brutal_honest"): "The operator prefers brutally honest answers with no fluff or copium.",
        ("source_preference", "official_docs"): "The operator wants official documentation prioritized over generic summaries.",
        ("source_preference", "github_repos"): "The operator wants strong GitHub repos used as implementation references.",
        ("source_preference", "reputable_sources"): "The operator wants reputable public sources instead of low-signal search noise.",
        ("preferred_stack", "python"): "The operator leans toward Python for implementation work.",
        ("preferred_stack", "typescript"): "The operator leans toward TypeScript for implementation work.",
        ("preferred_stack", "javascript"): "The operator leans toward JavaScript and Node-based implementation work.",
        ("preferred_stack", "rust"): "The operator is open to Rust for implementation work.",
        ("preferred_stack", "go"): "The operator is open to Go for implementation work.",
        ("project_focus", "telegram_bot"): "The operator repeatedly works on Telegram bot projects.",
        ("project_focus", "discord_bot"): "The operator repeatedly works on Discord bot projects.",
        ("project_focus", "hive_swarm"): "The operator actively cares about Hive and swarm coordination work.",
        ("project_focus", "openclaw_runtime"): "The operator repeatedly works on NULLA and OpenClaw runtime behavior.",
        ("autonomy_preference", "hands_off"): "The operator prefers low-friction execution with minimal micro-approvals.",
    }
    return mapping.get((category, signal), f"Observed operator heuristic: {category} {signal}.")


def _trim_text(text: str, max_chars: int) -> str:
    clean = " ".join(str(text or "").split()).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max(0, max_chars - 3)].rstrip() + "..."


def _recency_score(timestamp: str) -> float:
    if not timestamp:
        return 0.35
    try:
        dt = datetime.fromisoformat(timestamp)
    except Exception:
        try:
            dt = datetime.fromisoformat(f"{timestamp}T00:00:00+00:00")
        except Exception:
            return 0.35
    age_days = max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 86400.0)
    return max(0.2, 1.0 - min(age_days / 120.0, 0.8))


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _rewrite_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows if isinstance(row, dict)).rstrip()
    path.write_text((content + "\n") if content else "", encoding="utf-8")


def _trim_jsonl_file(path: Path, *, max_bytes: int) -> None:
    try:
        if path.stat().st_size <= max_bytes:
            return
    except Exception:
        return
    rows = _load_jsonl(path)
    if len(rows) <= 2:
        return
    keep = rows[len(rows) // 2 :]
    _rewrite_jsonl(path, keep)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
