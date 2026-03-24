from __future__ import annotations

import re
from typing import Any


def infer_hive_topic_tags(agent: Any, title: str) -> list[str]:
    stopwords = {
        "a",
        "about",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "best",
        "better",
        "build",
        "building",
        "but",
        "by",
        "can",
        "could",
        "create",
        "do",
        "does",
        "doing",
        "each",
        "fast",
        "fastest",
        "find",
        "for",
        "from",
        "future",
        "get",
        "good",
        "got",
        "had",
        "has",
        "have",
        "her",
        "here",
        "him",
        "his",
        "how",
        "human",
        "if",
        "improving",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "know",
        "let",
        "lets",
        "like",
        "look",
        "make",
        "more",
        "most",
        "much",
        "my",
        "need",
        "new",
        "not",
        "now",
        "of",
        "on",
        "one",
        "only",
        "or",
        "other",
        "our",
        "out",
        "over",
        "own",
        "preserving",
        "pure",
        "put",
        "really",
        "reuse",
        "self",
        "she",
        "should",
        "so",
        "some",
        "such",
        "task",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "thing",
        "this",
        "those",
        "to",
        "too",
        "try",
        "up",
        "us",
        "use",
        "very",
        "want",
        "was",
        "way",
        "we",
        "well",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
    raw_tokens = re.findall(r"[a-z0-9]+", str(title or "").lower())
    tags: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        if len(token) < 3 and token not in {"ai", "ux", "ui", "vm", "os"}:
            continue
        if token in stopwords:
            continue
        normalized = agent._normalize_hive_topic_tag(token)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        tags.append(normalized)
        if len(tags) >= 6:
            break
    return tags


def normalize_hive_topic_tag(raw: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", str(raw or "").strip().lower()).strip("-")
    if len(clean) < 2 or len(clean) > 32:
        return ""
    return clean
