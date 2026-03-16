from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from core import policy_engine
from core.source_credibility import evaluate_source_domain
from core.source_reputation import SourceProfile, allowed_domains_for_topic, profiles_for_topic, render_query
from storage.db import execute_query, get_connection
from tools.web.web_research import ResearchResult, WebHit, web_research


class WebAdapter:
    """
    Bounded web-research adapter.

    Search results remain candidate-only low-confidence hints. The adapter keeps
    the legacy `search_query()` note shape so existing curiosity code keeps
    working while the provider stack underneath moves to SearXNG/DDG/browser.
    """

    @staticmethod
    def research_query(
        query_text: str,
        *,
        limit: int = 3,
    ) -> ResearchResult | None:
        if not policy_engine.allow_web_fallback():
            return None
        text = (query_text or "").strip()
        if not text:
            return None
        return web_research(
            text,
            max_hits=max(1, int(limit)),
            max_pages=min(max(1, int(limit)), 3),
        )

    @staticmethod
    def search_query(
        query_text: str,
        *,
        task_id: str | None = None,
        limit: int = 3,
        source_label: str = "web.search",
        allowed_domains: tuple[str, ...] = (),
        blocked_domains: tuple[str, ...] = (),
    ) -> list[dict]:
        research = WebAdapter.research_query(query_text, limit=limit)
        if research is None:
            return []
        return _notes_from_research(
            research,
            query_text=query_text,
            task_id=task_id,
            source_label=source_label,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
            max_notes=limit,
        )

    @staticmethod
    def planned_search_query(
        query_text: str,
        *,
        task_id: str | None = None,
        limit: int = 3,
        task_class: str = "unknown",
        topic_kind: str | None = None,
        topic_hints: list[str] | tuple[str, ...] = (),
        max_profiles: int = 3,
        per_profile_limit: int = 2,
        source_label: str = "web.search",
    ) -> list[dict]:
        text = str(query_text or "").strip()
        if not text:
            return []

        effective_kind = (
            str(topic_kind or "").strip().lower()
            or _infer_topic_kind(text, task_class=task_class, topic_hints=list(topic_hints or []))
        )
        selected_profiles = profiles_for_topic(effective_kind, text)[: max(1, int(max_profiles))]
        if not selected_profiles:
            return WebAdapter.search_query(
                text,
                task_id=task_id,
                limit=limit,
                source_label=source_label,
            )

        ranked_notes: list[dict[str, Any]] = []
        per_profile = max(1, min(int(per_profile_limit), max(1, int(limit))))
        for profile in selected_profiles:
            effective_allow_domains = allowed_domains_for_topic(profile, text)
            research = WebAdapter.research_query(render_query(profile, text), limit=max(per_profile, 2))
            if research is None:
                continue
            ranked_notes.extend(
                _notes_from_research(
                    research,
                    query_text=text,
                    task_id=task_id,
                    source_label=source_label,
                    allowed_domains=effective_allow_domains,
                    blocked_domains=profile.deny_domains,
                    max_notes=per_profile,
                    source_profile=profile,
                )
            )

        deduped = _dedupe_ranked_notes(ranked_notes, limit=limit)
        if deduped:
            return deduped
        return WebAdapter.search_query(
            text,
            task_id=task_id,
            limit=limit,
            source_label=source_label,
        )

    @staticmethod
    def search_and_summarize(task: dict, classification: dict) -> list[dict]:
        task_summary = str(task.get("task_summary", "") or "")
        task_class = str(classification.get("task_class", "unknown"))
        if task_class in {"research", "system_design", "integration_orchestration"}:
            return WebAdapter.planned_search_query(
                task_summary,
                task_id=task.get("task_id"),
                limit=3,
                task_class=task_class,
            )
        return WebAdapter.search_query(
            task.get("task_summary", ""),
            task_id=task.get("task_id"),
            limit=3,
            source_label="web.search",
        )


def _page_for_hit(research: ResearchResult, url: str):
    for page in research.pages:
        if page.url == url or page.final_url == url:
            return page
    return None


def _best_summary(hit: WebHit, page) -> str:
    if page is not None:
        text = str(getattr(page, "text", "") or "").strip()
        if text and not _looks_like_navigation_noise(text):
            return text[:280]
    snippet = str(hit.snippet or "").strip()
    if snippet:
        return snippet[:280]
    if page is not None:
        text = str(getattr(page, "text", "") or "").strip()
        if text:
            return text[:280]
    title = str(hit.title or "").strip()
    return title[:280]


def _looks_like_navigation_noise(text: str) -> bool:
    sample = " ".join(str(text or "").split())[:320].lower()
    if not sample:
        return False
    direct_markers = (
        "skip to content",
        "skip to main content",
        "accessibility help",
        "open menu",
        "search site",
        "sign in",
        "account notifications",
        "privacy settings",
        "download browser",
    )
    if any(marker in sample for marker in direct_markers):
        return True
    token_hits = sum(sample.count(token) for token in (" home ", " menu ", " search ", " settings ", " privacy ", " account ", " login "))
    if token_hits >= 4:
        return True
    return bool("home news sport weather" in sample or "home news sport business" in sample)


def _notes_from_research(
    research: ResearchResult,
    *,
    query_text: str,
    task_id: str | None,
    source_label: str,
    allowed_domains: tuple[str, ...],
    blocked_domains: tuple[str, ...],
    max_notes: int,
    source_profile: SourceProfile | None = None,
) -> list[dict]:
    notes: list[dict] = []
    for hit in list(research.hits or []):
        page = _page_for_hit(research, hit.url)
        resolved_url = str(getattr(page, "final_url", "") or hit.url or "").strip()
        origin_domain = _domain_from_url(resolved_url)
        if blocked_domains and _domain_matches(origin_domain, blocked_domains):
            continue
        if allowed_domains and not _domain_matches(origin_domain, allowed_domains):
            continue

        verdict = evaluate_source_domain(origin_domain)
        if verdict.blocked:
            continue

        summary = _best_summary(hit, page)
        if not summary:
            continue

        profile_id = str(source_profile.profile_id if source_profile else "").strip()
        profile_label = str(source_profile.label if source_profile else "").strip()
        github_root = _github_repo_root(resolved_url)
        rank_score = _rank_source_note(
            hit=hit,
            page=page,
            summary=summary,
            verdict_score=float(verdict.score),
            source_profile=source_profile,
            github_root=github_root,
        )
        confidence = _note_confidence(
            verdict_score=float(verdict.score),
            source_profile=source_profile,
            page=page,
            github_root=github_root,
            title=str(hit.title or ""),
            summary=summary,
        )

        note_id = _store_web_note(
            query_text=query_text,
            summary=summary,
            confidence=confidence,
            task_id=task_id,
            source_label=_provider_source_label(research.provider, hit, source_label),
            url=resolved_url,
        )
        notes.append(
            {
                "source_type": "web_derived",
                "summary": summary,
                "note_id": note_id,
                "query_text": str(query_text or "").strip(),
                "confidence": confidence,
                "source_label": _provider_source_label(research.provider, hit, source_label),
                "search_provider": research.provider,
                "result_url": resolved_url,
                "origin_domain": origin_domain,
                "result_title": hit.title,
                "fetch_status": getattr(page, "status", "no_page"),
                "used_browser": bool(getattr(page, "used_browser", False)),
                "source_profile_id": profile_id,
                "source_profile_label": profile_label,
                "source_credibility": verdict.to_dict(),
                "source_rank_score": rank_score,
                "github_repo_root": github_root,
            }
        )
        if len(notes) >= max(1, int(max_notes)):
            break
    return notes


def _store_web_note(
    *,
    query_text: str,
    summary: str,
    confidence: float,
    task_id: str | None,
    source_label: str,
    url: str,
) -> str:
    note_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    linked_task_id = _existing_local_task_id(task_id)
    execute_query(
        """
        INSERT INTO web_notes (note_id, query_hash, source_label, source_url_hash, summary, confidence, freshness_ts, used_in_task_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note_id,
            hashlib.sha256(str(query_text or "").encode("utf-8")).hexdigest(),
            source_label,
            hashlib.sha256(url.encode("utf-8")).hexdigest() if url else None,
            summary,
            float(confidence),
            created_at,
            linked_task_id,
            created_at,
        ),
    )
    return note_id


def _existing_local_task_id(task_id: str | None) -> str | None:
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        return None
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT task_id FROM local_tasks WHERE task_id = ? LIMIT 1",
                (clean_task_id,),
            ).fetchone()
            return clean_task_id if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _dedupe_ranked_notes(notes: list[dict[str, Any]], *, limit: int) -> list[dict]:
    ranked = sorted(
        list(notes or []),
        key=lambda item: (
            float(item.get("source_rank_score") or 0.0),
            float(item.get("confidence") or 0.0),
        ),
        reverse=True,
    )
    selected: list[dict] = []
    seen: set[str] = set()
    for note in ranked:
        key = _canonical_note_key(note)
        if key in seen:
            continue
        seen.add(key)
        selected.append(note)
        if len(selected) >= max(1, int(limit)):
            break
    return selected


def _canonical_note_key(note: dict[str, Any]) -> str:
    github_root = str(note.get("github_repo_root") or "").strip().lower()
    if github_root:
        return github_root
    result_url = str(note.get("result_url") or "").strip().rstrip("/").lower()
    if result_url:
        return result_url
    title = str(note.get("result_title") or "").strip().lower()
    summary = str(note.get("summary") or "").strip().lower()
    return f"{title}|{summary[:120]}"


def _infer_topic_kind(query_text: str, *, task_class: str, topic_hints: list[str]) -> str:
    lowered = f"{query_text} {' '.join(str(item) for item in topic_hints)}".lower()
    if any(token in lowered for token in ("news", "headline", "current events", "breaking")):
        return "news"
    if any(token in lowered for token in ("telegram", "discord", "bot", "api", "webhook", "integration")):
        return "integration"
    if any(token in lowered for token in ("design", "ux", "ui", "layout", "theme")):
        return "design"
    if task_class in {"research", "system_design", "dependency_resolution", "config", "integration_orchestration"}:
        return "technical"
    return "general"


def _rank_source_note(
    *,
    hit: WebHit,
    page: Any,
    summary: str,
    verdict_score: float,
    source_profile: SourceProfile | None,
    github_root: str,
) -> float:
    profile_weight = float(source_profile.trust_weight if source_profile else 0.42)
    github_signal = _github_result_signal(hit.url, title=str(hit.title or ""), summary=summary, github_root=github_root)
    page_signal = 0.08 if page is not None and str(getattr(page, "text", "") or "").strip() else 0.0
    profile_bonus = _profile_priority_bonus(source_profile)
    return max(
        0.0,
        min(
            1.0,
            0.45 * verdict_score
            + 0.30 * profile_weight
            + 0.10 * github_signal
            + page_signal
            + profile_bonus,
        ),
    )


def _note_confidence(
    *,
    verdict_score: float,
    source_profile: SourceProfile | None,
    page: Any,
    github_root: str,
    title: str,
    summary: str,
) -> float:
    base = 0.25
    if source_profile is not None:
        base = 0.32 + (0.16 * float(source_profile.trust_weight))
    if page is not None and str(getattr(page, "text", "") or "").strip():
        base += 0.04
    base += 0.18 * verdict_score
    base += 0.10 * _github_result_signal("", title=title, summary=summary, github_root=github_root)
    return max(0.25, min(0.78, base))


def _profile_priority_bonus(source_profile: SourceProfile | None) -> float:
    if source_profile is None:
        return 0.0
    if source_profile.profile_id in {"official_docs", "messaging_platform_docs"}:
        return 0.10
    if source_profile.profile_id == "reputable_repos":
        return 0.03
    return 0.0


def _github_repo_root(url: str) -> str:
    domain = _domain_from_url(url)
    if domain != "github.com":
        return ""
    parsed = urlparse(url)
    parts = [part for part in str(parsed.path or "").split("/") if part]
    if len(parts) < 2:
        return ""
    owner, repo = parts[0].strip(), parts[1].strip()
    if not owner or not repo:
        return ""
    return f"https://github.com/{owner}/{repo}"


def _github_result_signal(url: str, *, title: str, summary: str, github_root: str) -> float:
    if not github_root:
        return 0.0
    parsed = urlparse(url)
    parts = [part for part in str(parsed.path or "").split("/") if part]
    path_signal = 1.0
    if len(parts) >= 3:
        kind = parts[2].lower().strip()
        if kind in {"issues", "pull", "pulls", "actions", "commit", "commits", "compare"}:
            path_signal = 0.35
        elif kind in {"blob", "tree", "wiki", "releases"}:
            path_signal = 0.72
        else:
            path_signal = 0.58
    text = f"{title} {summary}".lower()
    if any(token in text for token in ("deprecated", "archived", "unmaintained")):
        path_signal = min(path_signal, 0.18)
    if any(token in text for token in ("example", "examples", "starter", "template")):
        path_signal = min(1.0, path_signal + 0.08)
    return max(0.0, min(1.0, path_signal))


def _provider_source_label(provider: str, hit: WebHit, fallback: str) -> str:
    if provider == "searxng":
        engine = str(hit.engine or "").strip()
        return engine or "searxng"
    if provider == "ddg_instant":
        return "duckduckgo.instant"
    if provider == "duckduckgo_html":
        return "duckduckgo.com"
    return fallback


def _domain_from_url(url: str) -> str:
    from urllib.parse import urlparse

    if not url:
        return ""
    parsed = urlparse(url)
    netloc = (parsed.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def _domain_matches(domain: str, patterns: tuple[str, ...]) -> bool:
    if not domain:
        return False
    for pattern in patterns:
        normalized = pattern.lower().strip()
        if normalized.startswith("www."):
            normalized = normalized[4:]
        if domain == normalized or domain.endswith(f".{normalized}"):
            return True
    return False
