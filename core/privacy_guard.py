from __future__ import annotations

import re
from typing import Any


SHARE_SCOPES = {"local_only", "hive_mind", "public_knowledge"}
_SHARE_SCOPE_ALIASES = {
    "local_only": "local_only",
    "private": "local_only",
    "private vault": "local_only",
    "locked": "local_only",
    "private_locked": "local_only",
    "hive_mind": "hive_mind",
    "hive mind": "hive_mind",
    "shared pack": "hive_mind",
    "friend-swarm": "hive_mind",
    "friend swarm": "hive_mind",
    "swarm": "hive_mind",
    "public_knowledge": "public_knowledge",
    "public knowledge": "public_knowledge",
    "public commons": "public_knowledge",
    "hive/public commons": "public_knowledge",
    "commons": "public_knowledge",
}
_SHARE_SCOPE_LABELS = {
    "local_only": "PRIVATE VAULT",
    "hive_mind": "SHARED PACK",
    "public_knowledge": "HIVE/PUBLIC COMMONS",
}

_EMAIL_RE = re.compile(r"\b[\w.\-+]+@[\w.\-]+\.\w+\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+?\d[\d\-\(\) ]{8,}\d)")
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z0-9.'-]+\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct)\b",
    re.IGNORECASE,
)
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s]+")
_UNIX_PATH_RE = re.compile(r"(?:^|[\s(])/(?:Users|home|etc|var|tmp|opt|srv|private|mnt)/[^\s)]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(?:password|passphrase|api[_ -]?key|secret|token|bearer|access[_ -]?token|refresh[_ -]?token|private[_ -]?key)\b\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")
_GITHUB_TOKEN_RE = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")
_GENERIC_SECRET_LABEL_RE = re.compile(
    r"\b(?:my|the|operator|owner)\s+(?:real\s+)?(?:name|full name|address|email|phone|cell|mobile)\b",
    re.IGNORECASE,
)
_NAME_DISCLOSURE_RE = re.compile(
    r"\b(?:my name is|call me|i go by|operator name is|owner name is)\b",
    re.IGNORECASE,
)
_LOCATION_DISCLOSURE_RE = re.compile(r"\b(?:i live in|i work in|my address is)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_\-]{1,}", re.IGNORECASE)


def normalize_share_scope(value: str | None, *, default: str = "local_only") -> str:
    scope = str(value or "").strip().lower()
    if scope in SHARE_SCOPES:
        return scope
    if scope in _SHARE_SCOPE_ALIASES:
        return _SHARE_SCOPE_ALIASES[scope]
    return default


def share_scope_is_public(scope: str | None) -> bool:
    return normalize_share_scope(scope) in {"hive_mind", "public_knowledge"}


def share_scope_label(scope: str | None) -> str:
    return _SHARE_SCOPE_LABELS.get(normalize_share_scope(scope), "PRIVATE VAULT")


def tokenize_restricted_terms(terms: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in list(terms or []):
        cleaned = " ".join(str(raw or "").split()).strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned[:80])
    return out


def text_privacy_risks(text: str, *, restricted_terms: list[str] | None = None) -> list[str]:
    content = str(text or "").strip()
    if not content:
        return []

    lowered = content.lower()
    reasons: list[str] = []
    if _SECRET_ASSIGNMENT_RE.search(content):
        reasons.append("secret_assignment")
    if _OPENAI_KEY_RE.search(content):
        reasons.append("openai_key")
    if _GITHUB_TOKEN_RE.search(content):
        reasons.append("github_token")
    if _AWS_ACCESS_KEY_RE.search(content):
        reasons.append("aws_access_key")
    if _SLACK_TOKEN_RE.search(content):
        reasons.append("slack_token")
    if _EMAIL_RE.search(content):
        reasons.append("email")
    if _PHONE_RE.search(content):
        reasons.append("phone_number")
    if _ADDRESS_RE.search(content):
        reasons.append("postal_address")
    if _WINDOWS_PATH_RE.search(content) or _UNIX_PATH_RE.search(content):
        reasons.append("filesystem_path")
    if _GENERIC_SECRET_LABEL_RE.search(content):
        reasons.append("identity_marker")
    if _NAME_DISCLOSURE_RE.search(content):
        reasons.append("name_disclosure")
    if _LOCATION_DISCLOSURE_RE.search(content):
        reasons.append("location_disclosure")
    for term in tokenize_restricted_terms(restricted_terms):
        if term and term in lowered:
            reasons.append(f"restricted_term:{term}")
    return list(dict.fromkeys(reasons))


def shard_privacy_risks(
    shard: dict[str, Any],
    *,
    restricted_terms: list[str] | None = None,
) -> list[str]:
    if not isinstance(shard, dict):
        return ["invalid_shard"]

    texts: list[str] = []
    for key in ("summary", "problem_signature", "problem_class"):
        value = shard.get(key)
        if value:
            texts.append(str(value))
    for step in list(shard.get("resolution_pattern") or []):
        if isinstance(step, str):
            texts.append(step)
    joined = "\n".join(texts).strip()
    return text_privacy_risks(joined, restricted_terms=restricted_terms)


def parse_restricted_terms(text: str) -> list[str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    parts = re.split(r"\s*(?:,|;|\band\b)\s*", cleaned, flags=re.IGNORECASE)
    out: list[str] = []
    for part in parts:
        value = " ".join(part.split()).strip(" .")
        if not value:
            continue
        out.append(value[:80])
    return tokenize_restricted_terms(out)


def keyword_tokens(text: str, *, limit: int = 16) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in _WORD_RE.findall(str(text or "").lower()):
        if len(raw) < 3 or raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
        if len(out) >= limit:
            break
    return out
