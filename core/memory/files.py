from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.runtime_paths import data_path, project_path

MEMORY_FILE = "MEMORY.md"
CONVERSATION_LOG_FILE = "conversation_log.jsonl"
MEMORY_ENTRIES_FILE = "memory_entries.jsonl"
SESSION_SUMMARIES_FILE = "session_summaries.jsonl"
USER_HEURISTICS_FILE = "user_heuristics.jsonl"
DENSE_OPERATOR_PROFILE_FILE = "operator_dense_profile.json"

MAX_CONVERSATION_LOG_BYTES = 8 * 1024 * 1024
MAX_MEMORY_INDEX_BYTES = 2 * 1024 * 1024
MAX_SESSION_SUMMARY_BYTES = 2 * 1024 * 1024
MAX_USER_HEURISTICS_BYTES = 512 * 1024
MAX_DENSE_OPERATOR_PROFILE_BYTES = 256 * 1024


def memory_path() -> Path:
    return data_path(MEMORY_FILE)


def conversation_log_path() -> Path:
    return data_path(CONVERSATION_LOG_FILE)


def memory_entries_path() -> Path:
    return data_path(MEMORY_ENTRIES_FILE)


def session_summaries_path() -> Path:
    return data_path(SESSION_SUMMARIES_FILE)


def user_heuristics_path() -> Path:
    return data_path(USER_HEURISTICS_FILE)


def operator_dense_profile_path() -> Path:
    return data_path(DENSE_OPERATOR_PROFILE_FILE)


def ensure_memory_files(*, ensure_policy_table: callable) -> None:
    path = memory_path()
    if not path.exists():
        path.write_text(default_memory_template(), encoding="utf-8")
    for extra_path in (
        conversation_log_path(),
        memory_entries_path(),
        session_summaries_path(),
        user_heuristics_path(),
        operator_dense_profile_path(),
    ):
        if not extra_path.exists():
            if extra_path.suffix == ".json":
                extra_path.write_text("{}", encoding="utf-8")
            else:
                extra_path.write_text("", encoding="utf-8")
    ensure_policy_table()


def default_memory_template() -> str:
    template_path = project_path("MEMORY.md")
    if template_path.exists():
        text = template_path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return _ensure_memory_sections(text)
    return _ensure_memory_sections(
        "# NULLA Persistent Memory\n\n"
        "## Identity\n\n"
        "- **My name**: NULLA\n"
        "- **Owner's name**: unknown\n\n"
        "## Privacy Pact\n\n"
        "- Not set yet.\n\n"
        "## Learned Knowledge\n\n"
        "<!-- New memories append below -->\n"
    )


def _ensure_memory_sections(text: str) -> str:
    normalized = str(text or "").rstrip() + "\n"
    additions: list[str] = []
    if "## Identity" not in normalized:
        additions.append("## Identity\n\n- **My name**: NULLA\n- **Owner's name**: unknown")
    if "## Privacy Pact" not in normalized:
        additions.append("## Privacy Pact\n\n- Not set yet.")
    if "## Learned Knowledge" not in normalized:
        additions.append("## Learned Knowledge\n\n<!-- New memories append below -->")
    if not additions:
        return normalized
    return normalized.rstrip() + "\n\n" + "\n\n".join(additions).rstrip() + "\n"


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
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


def rewrite_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows if isinstance(row, dict)).rstrip()
    path.write_text((content + "\n") if content else "", encoding="utf-8")


def trim_jsonl_file(path: Path, *, max_bytes: int) -> None:
    try:
        if path.stat().st_size <= max_bytes:
            return
    except Exception:
        return
    rows = load_jsonl(path)
    if len(rows) <= 2:
        return
    keep = rows[len(rows) // 2 :]
    rewrite_jsonl(path, keep)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
