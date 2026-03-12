from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from core import policy_engine
from core.source_credibility import evaluate_source_domain
from core.social_source_policy import evaluate_social_source
from storage.media_evidence_log import record_media_evidence
from tools.browser.browser_render import browser_render
from tools.web.http_fetch import http_fetch_text


_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


@dataclass
class MediaEvidence:
    reference: str
    media_kind: str
    source_kind: str
    source_domain: str
    credibility: dict[str, Any]
    social_policy: dict[str, Any]
    text: str = ""
    caption: str = ""
    transcript: str = ""
    blocked: bool = False
    requires_multimodal: bool = False
    metadata: dict[str, Any] = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metadata"] = dict(self.metadata or {})
        return payload


def ingest_media_evidence(
    *,
    task_id: str,
    trace_id: str,
    user_input: str,
    source_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    source_context = dict(source_context or {})
    fetch_text_references = bool(source_context.get("fetch_text_references")) and policy_engine.allow_web_fallback()
    raw_items = list(source_context.get("external_evidence") or [])
    for url in _URL_RE.findall(user_input or ""):
        raw_items.append({"kind": _infer_kind_from_url(url), "url": url})

    ingested: list[dict[str, Any]] = []
    for item in raw_items:
        evidence = _normalize_item(item, fetch_text_reference=fetch_text_references)
        if not evidence:
            continue
        record_media_evidence(
            task_id=task_id,
            trace_id=trace_id,
            source_kind=evidence.source_kind,
            source_domain=evidence.source_domain,
            media_kind=evidence.media_kind,
            reference=evidence.reference,
            credibility_score=float(evidence.credibility.get("score") or 0.0),
            blocked=bool(evidence.blocked),
            metadata={
                "caption": evidence.caption,
                "has_text": bool(evidence.text),
                "has_transcript": bool(evidence.transcript),
                "requires_multimodal": bool(evidence.requires_multimodal),
                **dict(evidence.metadata or {}),
            },
        )
        ingested.append(evidence.to_dict())
    return ingested


def build_media_context_snippets(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    for item in items:
        if item.get("blocked"):
            continue
        domain = item.get("source_domain") or "unknown"
        confidence = float(dict(item.get("credibility") or {}).get("score") or 0.0)
        social_reason = str(dict(item.get("social_policy") or {}).get("reason") or "").strip()
        summary = item.get("text") or item.get("caption") or item.get("transcript") or f"{item.get('media_kind', 'media')} reference"
        details = f"Source {domain}. {social_reason}".strip()
        snippets.append(
            {
                "title": f"External {item.get('media_kind', 'media')} evidence",
                "source_type": f"external_{item.get('media_kind', 'media')}",
                "summary": f"{summary[:240]} {details}".strip(),
                "confidence": confidence,
                "metadata": {
                    "reference": item.get("reference"),
                    "source_domain": domain,
                    "requires_multimodal": bool(item.get("requires_multimodal")),
                    "blocked": bool(item.get("blocked")),
                },
            }
        )
    return snippets


def build_multimodal_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for item in items:
        if item.get("blocked"):
            continue
        media_kind = str(item.get("media_kind") or "")
        if media_kind not in {"image", "video"}:
            continue
        attachments.append(
            {
                "kind": media_kind,
                "url": item.get("reference"),
                "caption": item.get("caption") or item.get("text") or "",
                "transcript": item.get("transcript") or "",
                "label": f"{media_kind} evidence from {item.get('source_domain') or 'unknown'}",
            }
        )
    return attachments


def _normalize_item(item: dict[str, Any], *, fetch_text_reference: bool = False) -> MediaEvidence | None:
    reference = str(item.get("url") or item.get("path") or item.get("reference") or "").strip()
    text = str(item.get("text") or item.get("post_text") or "").strip()
    caption = str(item.get("caption") or "").strip()
    transcript = str(item.get("transcript") or "").strip()
    media_kind = str(item.get("kind") or _infer_kind_from_url(reference)).strip().lower() or "text"
    domain = _domain_from_reference(reference)
    credibility = evaluate_source_domain(domain).to_dict()
    social_policy = evaluate_social_source(domain).to_dict()
    blocked = bool(credibility.get("blocked")) or (social_policy.get("platform") != "unknown" and not bool(social_policy.get("allowed_for_orientation", True)))
    requires_multimodal = media_kind in {"image", "video"} and not transcript and not text
    source_kind = "social" if social_policy.get("platform") not in {"unknown", "social"} else "web"
    if media_kind == "social_post":
        source_kind = "social"
    if not reference and not text and not caption and not transcript:
        return None
    metadata = {k: v for k, v in dict(item).items() if k not in {"url", "path", "reference", "text", "post_text", "caption", "transcript", "kind"}}
    if fetch_text_reference and reference and media_kind == "text" and not text and not caption and not transcript and not blocked:
        fetched = _fetch_reference_text(reference)
        text = fetched.get("text", text)
        metadata.update(
            {
                "fetch_status": fetched.get("status", "fetch_error"),
                "used_browser": bool(fetched.get("used_browser")),
                "final_url": fetched.get("final_url"),
            }
        )
    return MediaEvidence(
        reference=reference or f"inline:{media_kind}",
        media_kind=media_kind,
        source_kind=source_kind,
        source_domain=domain,
        credibility=credibility,
        social_policy=social_policy,
        text=text,
        caption=caption,
        transcript=transcript,
        blocked=blocked,
        requires_multimodal=requires_multimodal,
        metadata=metadata,
    )


def _infer_kind_from_url(url: str) -> str:
    lower = (url or "").lower()
    if any(token in lower for token in ("x.com/", "twitter.com/", "facebook.com/", "instagram.com/", "reddit.com/")):
        return "social_post"
    if any(lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return "image"
    if any(lower.endswith(ext) for ext in (".mp4", ".mov", ".webm", ".mkv")) or "youtube.com/" in lower or "youtu.be/" in lower:
        return "video"
    return "text"


def _domain_from_reference(reference: str) -> str:
    if not reference or reference.startswith("inline:"):
        return ""
    parsed = urlparse(reference)
    netloc = (parsed.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _fetch_reference_text(reference: str) -> dict[str, Any]:
    try:
        fetched = http_fetch_text(reference)
    except Exception as exc:
        return {"status": f"fetch_error:{type(exc).__name__}", "text": "", "used_browser": False, "final_url": reference}

    status = str(fetched.get("status") or "fetch_error")
    text = str(fetched.get("text") or "")
    if status == "ok" and len(text.strip()) >= 600:
        return {"status": status, "text": text[:200000], "used_browser": False, "final_url": reference}
    if not policy_engine.allow_browser_fallback():
        return {"status": status, "text": text[:200000], "used_browser": False, "final_url": reference}

    rendered = browser_render(reference, engine=policy_engine.browser_engine())
    rendered_status = str(rendered.get("status") or "fetch_error")
    if rendered_status == "ok":
        return {
            "status": rendered_status,
            "text": str(rendered.get("text") or "")[:200000],
            "used_browser": True,
            "final_url": rendered.get("final_url") or reference,
        }
    return {"status": rendered_status, "text": text[:200000], "used_browser": rendered_status != "disabled", "final_url": rendered.get("final_url") or reference}
