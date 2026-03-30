from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from core.memory.files import (
    MAX_MEMORY_INDEX_BYTES,
    append_jsonl,
    conversation_log_path,
    ensure_memory_files,
    load_jsonl,
    memory_entries_path,
    memory_path,
    rewrite_jsonl,
    session_summaries_path,
    today_iso,
    trim_jsonl_file,
    user_heuristics_path,
    utcnow,
)
from core.memory.policies import ensure_session_policy_table, session_memory_policy
from core.privacy_guard import keyword_tokens, normalize_share_scope

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
_HEURISTIC_ALWAYS_INCLUDE = {"response_style", "autonomy_preference", "source_preference", "preferred_stack"}


def load_memory_excerpt(*, max_chars: int = 2200) -> str:
    _ensure_memory_files()
    text = memory_path().read_text(encoding="utf-8", errors="replace").strip()
    if len(text) <= max_chars:
        return text
    head_chars = max(160, min(max_chars // 2, 800))
    tail_chars = max(160, max_chars - head_chars - 7)
    return text[:head_chars].rstrip() + "\n...\n" + text[-tail_chars:].lstrip()


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
    _ensure_memory_files()
    clean = sanitize_fact(fact)
    if not clean:
        return False
    normalized_share_scope = normalize_share_scope(
        share_scope or session_memory_policy(session_id).get("share_scope"),
        default="local_only",
    )

    if str(category or "").strip().lower() == "name":
        replace_name_memory(clean)

    path = memory_path()
    text = path.read_text(encoding="utf-8", errors="replace")
    line = f"- [{today_iso()}] {clean}"
    if line in text or clean.lower() in text.lower():
        return False

    marker = "## Learned Knowledge"
    if marker not in text:
        text = text.rstrip() + "\n\n" + marker + "\n\n"
    path.write_text(text.rstrip() + "\n" + line + "\n", encoding="utf-8")
    record_memory_entry(
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
    _ensure_memory_files()
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
    forget_structured_memory(token)
    return removed


def summarize_memory(*, limit: int = 10) -> list[str]:
    _ensure_memory_files()
    lines = memory_path().read_text(encoding="utf-8", errors="replace").splitlines()
    learned = [line.strip()[2:] for line in lines if line.strip().startswith("- ")]
    return learned[-max(1, int(limit)) :]


def recent_conversation_events(session_id: str, *, limit: int = 6) -> list[dict[str, Any]]:
    _ensure_memory_files()
    rows = load_jsonl(conversation_log_path())
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
    query_tokens = set(keyword_tokens_filtered(" ".join([query_text, *(topic_hints or [])])))
    rows = combined_memory_entries()
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        item_tokens = set(row.get("keywords") or keyword_tokens_filtered(str(row.get("text") or "")))
        overlap = len(query_tokens & item_tokens) if query_tokens else 0
        if query_tokens and overlap <= 0:
            continue
        semantic = overlap / max(1, len(query_tokens)) if query_tokens else 0.0
        recency = recency_score(str(row.get("created_at") or ""))
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
    query_tokens = set(keyword_tokens_filtered(" ".join([query_text, *(topic_hints or [])])))
    latest_by_session: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(session_summaries_path()):
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
        item_tokens = set(row.get("keywords") or keyword_tokens_filtered(str(row.get("summary") or "")))
        overlap = len(query_tokens & item_tokens) if query_tokens else 0
        if query_tokens and overlap <= 0:
            continue
        semantic = overlap / max(1, len(query_tokens)) if query_tokens else 0.0
        recency = recency_score(str(row.get("created_at") or ""))
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
    query_tokens = set(keyword_tokens_filtered(" ".join([query_text, *(topic_hints or [])])))
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in load_jsonl(user_heuristics_path()):
        category = str(row.get("category") or "").strip().lower()
        item_tokens = set(row.get("keywords") or keyword_tokens_filtered(str(row.get("text") or "")))
        overlap = len(query_tokens & item_tokens) if query_tokens else 0
        semantic = overlap / max(1, len(query_tokens)) if query_tokens else 0.0
        if query_tokens and overlap <= 0 and category not in _HEURISTIC_ALWAYS_INCLUDE:
            continue
        mentions = max(1, int(row.get("mentions") or 1))
        strength = min(1.0, mentions / 3.0)
        recency = recency_score(str(row.get("updated_at") or row.get("created_at") or ""))
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


def sanitize_fact(text: str) -> str:
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return ""
    return clean[:320]


def record_memory_entry(
    text: str,
    *,
    category: str,
    session_id: str | None,
    source: str,
    confidence: float,
    keywords: list[str] | None,
    share_scope: str | None,
) -> None:
    _ensure_memory_files()
    clean = sanitize_fact(text)
    if not clean:
        return
    normalized = clean.lower()
    existing = load_jsonl(memory_entries_path())
    if any(str(row.get("text") or "").strip().lower() == normalized for row in reversed(existing[-200:])):
        return
    payload = {
        "created_at": utcnow(),
        "text": clean,
        "category": str(category or "fact"),
        "session_id": str(session_id or ""),
        "source": str(source or "manual"),
        "confidence": max(0.2, min(1.0, float(confidence))),
        "keywords": list(keywords or keyword_tokens_filtered(clean))[:16],
        "share_scope": normalize_share_scope(share_scope, default="local_only"),
    }
    append_jsonl(memory_entries_path(), payload)
    trim_jsonl_file(memory_entries_path(), max_bytes=MAX_MEMORY_INDEX_BYTES)


def forget_structured_memory(token: str) -> None:
    for path in (memory_entries_path(), session_summaries_path()):
        rows = load_jsonl(path)
        kept = [row for row in rows if token not in str(row).lower()]
        rewrite_jsonl(path, kept)


def combined_memory_entries() -> list[dict[str, Any]]:
    _ensure_memory_files()
    entries = load_jsonl(memory_entries_path())
    seen = {str(row.get("text") or "").strip().lower() for row in entries}
    for row in legacy_memory_entries():
        text_key = str(row.get("text") or "").strip().lower()
        if text_key and text_key not in seen:
            entries.append(row)
            seen.add(text_key)
    return entries


def legacy_memory_entries() -> list[dict[str, Any]]:
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
        clean = sanitize_fact(text)
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
                "keywords": keyword_tokens_filtered(clean),
                "share_scope": "local_only",
            }
        )
    return out


def replace_name_memory(new_name_fact: str) -> None:
    _ensure_memory_files()
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
            else:
                updated.append(line)
        if not identity_updated:
            for index, line in enumerate(updated):
                if line.strip() == "## Identity":
                    updated.insert(index + 1, f"- **Owner's name**: {extracted_name}")
                    break
        kept = updated

    path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")

    rows = [
        row
        for row in load_jsonl(memory_entries_path())
        if str(row.get("category") or "") != "name" and "operator name is " not in str(row.get("text") or "").lower()
    ]
    rewrite_jsonl(memory_entries_path(), rows)


def keyword_tokens_filtered(text: str, *, limit: int = 16) -> list[str]:
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


def trim_text(text: str, max_chars: int) -> str:
    clean = " ".join(str(text or "").split()).strip()
    if len(clean) <= max_chars:
        return clean
    return clean[: max(0, max_chars - 3)].rstrip() + "..."


def recency_score(timestamp: str) -> float:
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


def _ensure_memory_files() -> None:
    ensure_memory_files(ensure_policy_table=ensure_session_policy_table)
