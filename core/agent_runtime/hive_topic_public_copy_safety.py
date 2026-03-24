from __future__ import annotations

import re
from typing import Any

from core.agent_runtime.hive_topic_public_copy_transcript import (
    has_structured_hive_public_brief,
    looks_like_raw_chat_transcript,
)
from core.privacy_guard import text_privacy_risks
from core.task_router import redact_text

HIVE_CREATE_HARD_PRIVACY_RISKS = {
    "identity_marker",
    "name_disclosure",
    "location_disclosure",
    "phone_number",
    "postal_address",
}


def prepare_public_hive_topic_copy(
    agent: Any,
    *,
    raw_input: str,
    title: str,
    summary: str,
    mode: str = "improved",
) -> dict[str, Any]:
    clean_title = " ".join(str(title or "").split()).strip()
    clean_summary = " ".join(str(summary or "").split()).strip() or clean_title
    if looks_like_raw_chat_transcript(raw_input) and not has_structured_hive_public_brief(raw_input):
        return {
            "ok": False,
            "reason": "hive_topic_create_transcript_blocked",
            "privacy_risks": ["raw_chat_transcript"],
            "response": (
                "That looks like a raw chat log/transcript. I won't dump private chat into the public Hive. "
                "Give me a public-safe brief in plain language, or mark the shareable parts with `Task:` and optional `Goal:`. "
                "I can still keep the raw chat local."
            ),
        }

    if mode == "original":
        original_risks = text_privacy_risks(f"{clean_title}\n{clean_summary}")
        if original_risks:
            risk_labels = ", ".join(list(original_risks)[:4])
            return {
                "ok": False,
                "reason": "hive_topic_create_original_blocked",
                "privacy_risks": original_risks,
                "response": (
                    "The original Hive draft still looks private "
                    f"({risk_labels}). I can send the improved public-safe draft instead."
                ),
            }
        return {
            "ok": True,
            "title": clean_title[:180],
            "summary": clean_summary[:4000],
            "preview_note": "",
            "privacy_risks": [],
        }

    original_risks = text_privacy_risks(f"{clean_title}\n{clean_summary}")
    sanitized_title = sanitize_public_hive_text(clean_title)
    sanitized_summary = sanitize_public_hive_text(clean_summary) or sanitized_title
    sanitized_title, sanitized_summary, admission_note = shape_public_hive_admission_safe_copy(
        title=sanitized_title,
        summary=sanitized_summary,
    )
    remaining_risks = text_privacy_risks(f"{sanitized_title}\n{sanitized_summary}")
    hard_risks = [
        risk
        for risk in list(original_risks or [])
        if risk.startswith("restricted_term:") or risk in HIVE_CREATE_HARD_PRIVACY_RISKS
    ]
    unresolved_risks = [
        risk
        for risk in list(remaining_risks or [])
        if risk.startswith("restricted_term:")
        or risk in HIVE_CREATE_HARD_PRIVACY_RISKS
        or risk in {
            "email",
            "filesystem_path",
            "secret_assignment",
            "openai_key",
            "github_token",
            "aws_access_key",
            "slack_token",
        }
    ]
    if hard_risks or unresolved_risks:
        risk_labels = ", ".join((hard_risks or unresolved_risks)[:4])
        return {
            "ok": False,
            "reason": "hive_topic_create_privacy_blocked",
            "privacy_risks": hard_risks or unresolved_risks,
            "response": (
                "I won't create that Hive task because the public brief still looks private "
                f"({risk_labels}). I can help rewrite it into a public-safe research brief."
            ),
        }

    preview_note = ""
    if sanitized_title != clean_title or sanitized_summary != clean_summary:
        redacted_labels = [
            risk
            for risk in list(original_risks or [])
            if risk not in HIVE_CREATE_HARD_PRIVACY_RISKS and not risk.startswith("restricted_term:")
        ]
        if not redacted_labels:
            redacted_labels = ["private_fields"]
        preview_note = (
            "\n\nSafety: I redacted private-looking fields before preview "
            f"({', '.join(redacted_labels[:4])})."
        )
    if admission_note:
        preview_note = f"{preview_note}{admission_note}" if preview_note else admission_note

    return {
        "ok": True,
        "title": sanitized_title[:180],
        "summary": sanitized_summary[:4000],
        "preview_note": preview_note,
        "privacy_risks": original_risks,
    }


def sanitize_public_hive_text(text: str) -> str:
    sanitized = redact_text(str(text or ""))
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized


def shape_public_hive_admission_safe_copy(
    *,
    title: str,
    summary: str,
    force: bool = False,
) -> tuple[str, str, str]:
    clean_title = " ".join(str(title or "").split()).strip()
    clean_summary = " ".join(str(summary or "").split()).strip() or clean_title
    combined = f"{clean_title} {clean_summary}".strip().lower()
    command_like = bool(
        re.match(
            r"^(?:research|check(?:\s+out)?|look\s+into|analy[sz]e|review|verify|investigate|find\s+out|tell\s+me|scan|go\s+check)\b",
            clean_title.lower(),
        )
    )
    has_analysis_framing = any(
        marker in combined
        for marker in (
            "analysis",
            "compare",
            "tradeoff",
            "evidence",
            "security",
            "docs",
            "source",
            "tests",
            "official",
            "why",
            "risk",
        )
    )
    if not force and not (command_like and not has_analysis_framing):
        return clean_title, clean_summary, ""

    subject = re.sub(
        r"^(?:research|check(?:\s+out)?|look\s+into|analy[sz]e|review|verify|investigate|find\s+out|tell\s+me|scan|go\s+check)\s+",
        "",
        clean_title,
        flags=re.IGNORECASE,
    ).strip(" :-")
    subject = subject or clean_title or "this topic"
    reframed_summary = (
        "Agent analysis brief comparing architecture, security, implementation tradeoffs, docs, and evidence for "
        f"{subject}. Requested scope: {clean_summary.rstrip('.')}."
    )
    preview_note = (
        "\n\nAdmission: I reframed the improved copy as agent analysis so the public Hive will accept it."
    )
    return clean_title, reframed_summary[:4000], preview_note
