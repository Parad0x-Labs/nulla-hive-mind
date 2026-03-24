from __future__ import annotations

import re
from typing import Any


def wants_hive_create_auto_start(text: str) -> bool:
    compact = " ".join(str(text or "").split()).strip().lower()
    if not compact:
        return False
    return any(
        phrase in compact
        for phrase in (
            "start working on it",
            "start working on this",
            "start on it",
            "start on this",
            "start researching",
            "start research",
            "work on it",
            "work on this",
            "research it",
            "research this",
            "go ahead and start",
            "create it and start",
            "post it and start",
            "start there",
        )
    )


def looks_like_hive_topic_create_request(agent: Any, lowered: str) -> bool:
    text = str(lowered or "").strip().lower()
    if not text:
        return False
    if agent._looks_like_hive_topic_drafting_request(text):
        return False
    has_create = bool(
        re.search(r"\b(?:create|make|start)\b", text)
        or "new task" in text
        or "new topic" in text
        or "open a" in text
        or "open new" in text
    )
    has_target = any(marker in text for marker in ("task", "topic", "thread"))
    if not (has_create and has_target):
        return False
    if "hive" not in text and "topic" not in text and "create" not in text:
        return False
    return not any(
        marker in text
        for marker in (
            "claim task",
            "pull hive tasks",
            "open hive tasks",
            "open tasks",
            "show me",
            "what do we have",
            "any tasks",
            "list tasks",
            "ignore hive",
            "research complete",
            "status",
        )
    )


def looks_like_hive_topic_drafting_request(_: Any, lowered: str) -> bool:
    text = " ".join(str(lowered or "").split()).strip().lower()
    if not text:
        return False
    strong_drafting_markers = (
        "give me the perfect script",
        "create extensive script first",
        "write the script first",
        "draft it first",
        "before i push",
        "before i post",
        "before i send",
        "then i decide if i want to push",
        "then i check and decide",
        "if i want to push that to the hive",
        "if i want to send that to the hive",
        "improve the task first",
        "improve this task first",
    )
    if any(marker in text for marker in strong_drafting_markers):
        return True
    if any(token in text for token in ("script", "prompt", "outline", "template")):
        explicit_send_markers = (
            "create hive mind task",
            "create hive task",
            "create new hive task",
            "create task in hive",
            "add this to the hive",
            "post this to the hive",
            "send this to the hive",
            "push this to the hive",
            "put this on the hive",
        )
        if not any(marker in text for marker in explicit_send_markers):
            if any(
                marker in text
                for marker in (
                    "give me",
                    "write me",
                    "draft",
                    "improve",
                    "polish",
                    "rewrite",
                    "fix typos",
                    "help me",
                )
            ):
                return True
    return False
