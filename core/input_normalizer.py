from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"[A-Za-z0-9_'\-]+|[^\w\s]")

_DEFAULT_REWRITES = {
    "u": "you",
    "ur": "your",
    "pls": "please",
    "pls.": "please",
    "plz": "please",
    "hlp": "help",
    "im": "i am",
    "ive": "i have",
    "idk": "i do not know",
    "wanna": "want to",
    "gonna": "going to",
    "kinda": "kind of",
    "sorta": "sort of",
    "tho": "though",
    "cuz": "because",
    "bc": "because",
    "btw": "by the way",
    "ya": "you",
    "tho?": "though",
    "tg": "telegram",
    "msg": "message",
    "cmd": "command",
    "cfg": "config",
    "db": "database",
    "pwd": "password",
    "pwds": "passwords",
    "cred": "credit",
    "creds": "credits",
}

_TYPO_REWRITES = {
    "passwrods": "passwords",
    "teh": "the",
    "instal": "install",
}

_DOMAIN_VOCAB = {
    "agent",
    "assistant",
    "bot",
    "capability",
    "chunk",
    "cluster",
    "config",
    "consensus",
    "credit",
    "credits",
    "daemon",
    "decentralized",
    "dialogue",
    "dispute",
    "fetch",
    "freshness",
    "greet",
    "help",
    "harden",
    "heartbeat",
    "helper",
    "identity",
    "knowledge",
    "lease",
    "liquefy",
    "lore",
    "mesh",
    "message",
    "node",
    "onboarding",
    "password",
    "passwords",
    "persona",
    "presence",
    "protect",
    "replica",
    "replication",
    "route",
    "security",
    "server",
    "setup",
    "shard",
    "solana",
    "standalone",
    "storage",
    "swarm",
    "telegram",
    "timeout",
    "transport",
    "validation",
}


@dataclass
class NormalizationResult:
    raw_text: str
    normalized_text: str
    replacements: dict[str, str] = field(default_factory=dict)
    quality_flags: list[str] = field(default_factory=list)


def _join_tokens(tokens: list[str]) -> str:
    out: list[str] = []
    for token in tokens:
        if not out:
            out.append(token)
            continue
        if token in {".", ",", "!", "?", ":", ";", ")"}:
            out[-1] = out[-1].rstrip()
            out.append(token)
            continue
        if token in {"(", "[", "{"}:
            out.append(token)
            continue
        if out[-1] in {"(", "[", "{", "/", "-", "->"}:
            out.append(token)
            continue
        out.append(f" {token}")
    return "".join(out).strip()


def _fuzzy_domain_match(token: str) -> str | None:
    if len(token) < 4 or not token.isalpha():
        return None
    matches = difflib.get_close_matches(token, _DOMAIN_VOCAB, n=1, cutoff=0.84)
    if not matches:
        return None
    match = matches[0]
    if match == token:
        return None
    return match


def normalize_user_text(text: str, *, session_lexicon: dict[str, str] | None = None) -> NormalizationResult:
    raw_text = text or ""
    value = raw_text.strip()
    value = re.sub(r"[\u2018\u2019]", "'", value)
    value = re.sub(r"[\u201c\u201d]", '"', value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[!?]{2,}", "?", value)
    value = re.sub(r"\.{3,}", ".", value)

    rewrites = dict(_DEFAULT_REWRITES)
    typo_rewrites = dict(_TYPO_REWRITES)
    if session_lexicon:
        rewrites.update({str(k).lower(): str(v).lower() for k, v in session_lexicon.items()})

    output_tokens: list[str] = []
    replacements: dict[str, str] = {}
    shorthand_count = 0
    typo_count = 0
    ambiguous_noise = 0

    for token in _TOKEN_RE.findall(value):
        lower = token.lower()
        replacement = token
        if re.fullmatch(r"[A-Za-z0-9_'\-]+", token):
            if lower in rewrites:
                replacement = rewrites[lower]
                replacements[token] = replacement
                shorthand_count += 1
            elif lower in typo_rewrites:
                replacement = typo_rewrites[lower]
                replacements[token] = replacement
                typo_count += 1
            else:
                fuzzy = _fuzzy_domain_match(lower)
                if fuzzy:
                    replacement = fuzzy
                    replacements[token] = replacement
                    typo_count += 1
        elif token not in {".", ",", "!", "?", ":", ";", "(", ")"}:
            ambiguous_noise += 1
        output_tokens.extend(replacement.split(" "))

    normalized = _join_tokens(output_tokens)
    quality_flags: list[str] = []
    if shorthand_count >= 2:
        quality_flags.append("shorthand_heavy")
    if typo_count >= 1:
        quality_flags.append("typo_heavy")
    if ambiguous_noise >= 2:
        quality_flags.append("noisy_punctuation")
    if len(normalized.split()) <= 5:
        quality_flags.append("short_input")
    if normalized and not re.search(r"[.?!]$", normalized):
        quality_flags.append("fragmented")

    return NormalizationResult(
        raw_text=raw_text,
        normalized_text=normalized,
        replacements=replacements,
        quality_flags=quality_flags,
    )
