from __future__ import annotations

import re
from typing import Any


def maybe_handle_hive_topic_mutation_request(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    clean = " ".join(str(user_input or "").split()).strip()
    lowered = clean.lower()
    if agent._looks_like_hive_topic_update_request(lowered):
        return agent._handle_hive_topic_update_request(
            clean,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )
    if agent._looks_like_hive_topic_delete_request(lowered):
        return agent._handle_hive_topic_delete_request(
            clean,
            task=task,
            session_id=session_id,
            source_context=source_context,
        )
    return None


def looks_like_hive_topic_update_request(agent: Any, lowered: str) -> bool:
    compact = " ".join(str(lowered or "").split()).strip().lower()
    if not compact or agent._looks_like_hive_topic_create_request(compact):
        return False
    if "update my twitter handle" in compact:
        return False
    if not any(marker in compact for marker in ("update", "edit", "change")):
        return False
    return (
        any(marker in compact for marker in ("task", "topic", "thread", "hive mind", "brain hive"))
        or "the one you created" in compact
        or "the one you just created" in compact
    )


def looks_like_hive_topic_delete_request(agent: Any, lowered: str) -> bool:
    compact = " ".join(str(lowered or "").split()).strip().lower()
    if not compact or agent._looks_like_hive_topic_create_request(compact):
        return False
    if not any(marker in compact for marker in ("delete", "remove", "cancel", "close")):
        return False
    return (
        any(marker in compact for marker in ("task", "topic", "thread", "hive mind", "brain hive"))
        or "the one you created" in compact
        or "the one you just created" in compact
    )


def extract_hive_topic_update_draft(agent: Any, text: str) -> dict[str, Any] | None:
    structured = agent._extract_hive_topic_create_draft(text)
    if structured is not None:
        return structured
    raw = agent._strip_context_subject_suffix(text)
    tail = re.sub(
        r"^.*?\b(?:update|edit|change)\b\s+(?:the\s+|my\s+)?(?:(?:current|last|latest|existing)\s+)?"
        r"(?:(?:hive|hive mind|brain hive)\s+)?(?:task|topic|thread|one\s+you\s+created(?:\s+already)?)\b"
        r"(?:\s+(?:#?[a-z0-9-]{6,64}))?"
        r"(?:\s+(?:with|to))?(?:\s+the)?(?:\s+following)?\s*[:\-]?\s*",
        "",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    tail = agent._strip_wrapping_quotes(" ".join(tail.split()).strip())
    if not tail or tail == "already":
        return None
    return {
        "title": "",
        "summary": tail[:4000],
        "topic_tags": [],
        "auto_start_research": False,
    }
