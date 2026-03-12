from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core import audit_logger
from core.candidate_knowledge_lane import (
    build_task_hash,
    get_candidate_by_id,
    get_exact_candidate,
    record_candidate_output,
)
from core.curiosity_policy import (
    CuriosityConfig,
    curiosity_decision,
    load_curiosity_config,
    policy_snapshot,
    source_kind_limit,
)
from core.source_credibility import SourceCredibilityVerdict, evaluate_source_domain, is_domain_allowed
from core.source_reputation import SourceProfile, profiles_for_topic, render_query
from network.signer import get_local_peer_id
from retrieval.web_adapter import WebAdapter
from storage.curiosity_state import queue_curiosity_topic, record_curiosity_run, update_curiosity_topic


_IDLE_COMMONS_SEEDS: tuple[tuple[str, str, str], ...] = (
    ("integration", "OpenClaw and Liquefy integration improvements", "integration refresh"),
    ("technical", "safer self-tool creation and verification loops", "toolsmith hardening"),
    ("design", "better human-visible watcher and task-flow UX", "watcher usability"),
    ("technical", "swarm memory reuse without leaking private traces", "memory discipline"),
    ("integration", "public-hive task participation and reward proof loops", "hive ops"),
)


@dataclass(frozen=True)
class CuriosityTopic:
    topic: str
    topic_kind: str
    reason: str
    priority: float
    source_profiles: tuple[SourceProfile, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "topic_kind": self.topic_kind,
            "reason": self.reason,
            "priority": self.priority,
            "source_profiles": [profile.to_dict() for profile in self.source_profiles],
        }


@dataclass
class CuriosityResult:
    enabled: bool
    mode: str
    reason: str
    topics: list[dict[str, Any]] = field(default_factory=list)
    queued_topic_ids: list[str] = field(default_factory=list)
    executed_topic_ids: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    cached_topic_hits: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "reason": self.reason,
            "topics": list(self.topics),
            "queued_topic_ids": list(self.queued_topic_ids),
            "executed_topic_ids": list(self.executed_topic_ids),
            "candidate_ids": list(self.candidate_ids),
            "cached_topic_hits": int(self.cached_topic_hits),
        }


class CuriosityRoamer:
    def __init__(self, config: CuriosityConfig | None = None) -> None:
        self.config = config or load_curiosity_config()

    def maybe_roam(
        self,
        *,
        task: Any,
        user_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        context_result: Any,
        session_id: str,
    ) -> CuriosityResult:
        interest_score = curiosity_interest_score(
            user_input=user_input,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
        )
        decision = curiosity_decision(
            config=self.config,
            task_class=str(classification.get("task_class", "unknown")),
            understanding_confidence=float(getattr(interpretation, "understanding_confidence", 0.0) or 0.0),
            retrieval_confidence_score=float(getattr(context_result, "retrieval_confidence_score", 0.0) or 0.0),
            interest_score=interest_score,
        )
        topics = derive_curiosity_topics(
            user_input=user_input,
            classification=classification,
            interpretation=interpretation,
            config=self.config,
        )
        result = CuriosityResult(
            enabled=decision.enabled,
            mode=self.config.mode,
            reason=decision.reason,
            topics=[topic.to_dict() for topic in topics],
        )
        if not decision.enabled or not topics:
            return result

        for topic in topics:
            topic_id = queue_curiosity_topic(
                session_id=session_id,
                task_id=str(getattr(task, "task_id", "")),
                trace_id=str(getattr(task, "task_id", "")),
                topic=topic.topic,
                topic_kind=topic.topic_kind,
                reason=topic.reason,
                priority=topic.priority,
                source_profiles=[profile.to_dict() for profile in topic.source_profiles],
            )
            result.queued_topic_ids.append(topic_id)
            if decision.auto_execute:
                candidate_id, cached = self._execute_topic(
                    topic_id=topic_id,
                    topic=topic,
                    task_id=str(getattr(task, "task_id", "")),
                    trace_id=str(getattr(task, "task_id", "")),
                )
                result.executed_topic_ids.append(topic_id)
                if cached:
                    result.cached_topic_hits += 1
                if candidate_id:
                    result.candidate_ids.append(candidate_id)

        audit_logger.log(
            "curiosity_roam_completed",
            target_id=str(getattr(task, "task_id", "")),
            target_type="task",
            trace_id=str(getattr(task, "task_id", "")),
            details={
                "decision": result.reason,
                "mode": self.config.mode,
                "topics": [topic["topic"] for topic in result.topics],
                "queued_topic_ids": list(result.queued_topic_ids),
                "executed_topic_ids": list(result.executed_topic_ids),
                "candidate_ids": list(result.candidate_ids),
                "cached_topic_hits": result.cached_topic_hits,
                "policy": policy_snapshot(self.config),
            },
        )
        return result

    def run_idle_commons(
        self,
        *,
        session_id: str,
        task_id: str = "agent-commons",
        trace_id: str = "agent-commons",
        seed_index: int | None = None,
    ) -> dict[str, Any]:
        topic = _idle_commons_topic(seed_index=seed_index)
        topic_id = queue_curiosity_topic(
            session_id=session_id,
            task_id=task_id,
            trace_id=trace_id,
            topic=topic.topic,
            topic_kind=topic.topic_kind,
            reason=topic.reason,
            priority=topic.priority,
            source_profiles=[profile.to_dict() for profile in topic.source_profiles],
        )
        candidate_id, cached = self._execute_topic(
            topic_id=topic_id,
            topic=topic,
            task_id=task_id,
            trace_id=trace_id,
        )
        candidate = get_candidate_by_id(candidate_id) if candidate_id else None
        structured = dict(candidate.get("structured_output") or {}) if candidate else {}
        snippets = list(structured.get("snippets") or [])
        summary = str(candidate.get("normalized_output") or candidate.get("raw_output") or "").strip() if candidate else ""
        return {
            "topic_id": topic_id,
            "candidate_id": candidate_id,
            "cached": bool(cached),
            "topic": topic.to_dict(),
            "summary": summary,
            "snippets": snippets,
            "public_body": _commons_public_body(topic=topic, summary=summary, snippets=snippets),
            "topic_tags": ["agent_commons", "brainstorm", topic.topic_kind],
        }

    def run_external_topic(
        self,
        *,
        session_id: str,
        topic_text: str,
        topic_kind: str = "technical",
        reason: str = "external_topic",
        task_id: str = "external-research",
        trace_id: str | None = None,
        priority: float = 0.72,
    ) -> dict[str, Any]:
        clean_topic_text = " ".join(str(topic_text or "").split()).strip()
        clean_topic_kind = str(topic_kind or "technical").strip() or "technical"
        if not clean_topic_text:
            return {
                "topic_id": "",
                "candidate_id": None,
                "cached": False,
                "topic": {},
                "summary": "",
                "snippets": [],
            }
        topic = CuriosityTopic(
            topic=clean_topic_text,
            topic_kind=clean_topic_kind,
            reason=str(reason or "external_topic").strip() or "external_topic",
            priority=max(0.0, min(1.0, float(priority))),
            source_profiles=tuple(profiles_for_topic(clean_topic_kind, clean_topic_text)),
        )
        topic_id = queue_curiosity_topic(
            session_id=session_id,
            task_id=task_id,
            trace_id=str(trace_id or task_id or clean_topic_text),
            topic=topic.topic,
            topic_kind=topic.topic_kind,
            reason=topic.reason,
            priority=topic.priority,
            source_profiles=[profile.to_dict() for profile in topic.source_profiles],
        )
        candidate_id, cached = self._execute_topic(
            topic_id=topic_id,
            topic=topic,
            task_id=task_id,
            trace_id=str(trace_id or task_id or clean_topic_text),
        )
        candidate = get_candidate_by_id(candidate_id) if candidate_id else None
        structured = dict(candidate.get("structured_output") or {}) if candidate else {}
        snippets = list(structured.get("snippets") or [])
        summary = str(candidate.get("normalized_output") or candidate.get("raw_output") or "").strip() if candidate else ""
        return {
            "topic_id": topic_id,
            "candidate_id": candidate_id,
            "cached": bool(cached),
            "topic": topic.to_dict(),
            "summary": summary,
            "snippets": snippets,
        }

    def _execute_topic(self, *, topic_id: str, topic: CuriosityTopic, task_id: str, trace_id: str) -> tuple[str | None, bool]:
        task_hash = build_task_hash(
            normalized_input=f"curiosity::{topic.topic_kind}::{topic.topic}",
            task_class="curiosity_roam",
            output_mode="summary_block",
        )
        cached = get_exact_candidate(task_hash, output_mode="summary_block")
        if cached:
            update_curiosity_topic(topic_id, status="completed", candidate_id=str(cached["candidate_id"]))
            record_curiosity_run(
                topic_id=topic_id,
                task_id=task_id,
                trace_id=trace_id,
                query_text=topic.topic,
                source_profile_ids=[profile.profile_id for profile in topic.source_profiles],
                snippets=[],
                candidate_id=str(cached["candidate_id"]),
                outcome="cache_hit",
            )
            return str(cached["candidate_id"]), True

        snippets: list[dict[str, Any]] = []
        selected_profiles = list(topic.source_profiles)[: self.config.max_queries_per_topic]
        for profile in selected_profiles:
            query = render_query(profile, topic.topic)
            notes = WebAdapter.search_query(
                query,
                task_id=task_id or None,
                limit=self.config.max_snippets_per_query,
                source_label="web.search",
                allowed_domains=profile.allow_domains,
                blocked_domains=profile.deny_domains,
            )
            for note in notes:
                domain = str(note.get("origin_domain") or "")
                if domain:
                    if not is_domain_allowed(domain, allow_domains=profile.allow_domains, deny_domains=profile.deny_domains):
                        continue
                    verdict = evaluate_source_domain(domain)
                    if verdict.blocked or verdict.score < 0.40:
                        continue
                else:
                    verdict = SourceCredibilityVerdict(
                        domain="",
                        score=max(0.42, float(profile.trust_weight)),
                        category=profile.credibility_class,
                        blocked=False,
                        reason="Domain missing; falling back to curated source profile trust.",
                    )
                enriched = dict(note)
                enriched["source_profile_id"] = profile.profile_id
                enriched["source_profile_label"] = profile.label
                enriched["source_credibility"] = verdict.to_dict()
                snippets.append(enriched)

        if not snippets:
            update_curiosity_topic(topic_id, status="empty")
            record_curiosity_run(
                topic_id=topic_id,
                task_id=task_id,
                trace_id=trace_id,
                query_text=topic.topic,
                source_profile_ids=[profile.profile_id for profile in selected_profiles],
                snippets=[],
                candidate_id=None,
                outcome="no_results",
            )
            return None, False

        best_ttl = min(profile.ttl_seconds for profile in selected_profiles) if selected_profiles else 3600
        confidence = _candidate_confidence(topic, snippets)
        summary_lines = [f"Bounded curiosity notes for {topic.topic}:"]
        for snippet in snippets[: self.config.max_queries_per_topic * self.config.max_snippets_per_query]:
            label = str(snippet.get("source_profile_label") or "source")
            summary_lines.append(f"- [{label}] {str(snippet.get('summary') or '').strip()}")
        candidate_id = record_candidate_output(
            task_hash=task_hash,
            task_id=task_id or None,
            trace_id=trace_id or None,
            task_class="curiosity_roam",
            task_kind=f"curiosity_{topic.topic_kind}",
            output_mode="summary_block",
            provider_name="curiosity_roamer",
            model_name="bounded_web_research",
            raw_output="\n".join(summary_lines),
            normalized_output="\n".join(summary_lines),
            structured_output={
                "topic": topic.topic,
                "topic_kind": topic.topic_kind,
                "snippets": snippets,
            },
            confidence=confidence,
            trust_score=confidence,
            validation_state="valid",
            metadata={
                "candidate_only": True,
                "curiosity_topic": topic.topic,
                "curiosity_reason": topic.reason,
                "source_profile_ids": [profile.profile_id for profile in selected_profiles],
                "interest_priority": topic.priority,
                "source_credibility_min": min(
                    (
                        float(dict(snippet.get("source_credibility") or {}).get("score") or 0.0)
                        for snippet in snippets
                    ),
                    default=0.0,
                ),
            },
            provenance={
                "search_engine": str(snippets[0].get("search_provider") or "unknown") if snippets else "unknown",
                "search_provider_order": list(dict.fromkeys(str(snippet.get("search_provider") or "unknown") for snippet in snippets)),
                "source_profiles": [profile.to_dict() for profile in selected_profiles],
            },
            ttl_seconds=best_ttl,
        )
        update_curiosity_topic(topic_id, status="completed", candidate_id=candidate_id)
        record_curiosity_run(
            topic_id=topic_id,
            task_id=task_id,
            trace_id=trace_id,
            query_text=topic.topic,
            source_profile_ids=[profile.profile_id for profile in selected_profiles],
            snippets=snippets,
            candidate_id=candidate_id,
            outcome="candidate_recorded",
        )
        return candidate_id, False


def curiosity_interest_score(*, user_input: str, classification: dict[str, Any], interpretation: Any, context_result: Any) -> float:
    text = (user_input or "").lower()
    task_class = str(classification.get("task_class", "unknown"))
    score = 0.34

    if task_class in {"research", "system_design"}:
        score += 0.24
    if any(token in text for token in ("learn", "research", "look up", "search", "best", "design", "telegram", "discord", "bot", "app", "web", "news")):
        score += 0.16
    if len(getattr(interpretation, "topic_hints", []) or []) >= 2:
        score += 0.08
    if float(getattr(interpretation, "understanding_confidence", 0.0) or 0.0) >= 0.70:
        score += 0.08
    if float(getattr(context_result, "retrieval_confidence_score", 0.0) or 0.0) < 0.55:
        score += 0.10

    return max(0.0, min(1.0, score))


def derive_curiosity_topics(*, user_input: str, classification: dict[str, Any], interpretation: Any, config: CuriosityConfig | None = None) -> list[CuriosityTopic]:
    config = config or load_curiosity_config()
    text = (user_input or "").strip()
    if not text:
        return []

    topics: list[tuple[str, str, str, float]] = []
    task_class = str(classification.get("task_class", "unknown"))
    topic_hints = [str(item) for item in getattr(interpretation, "topic_hints", []) or []]

    if topic_hints:
        for hint in topic_hints[:4]:
            kind = _topic_kind(hint, task_class, text, config=config)
            reason = f"topic_hint:{hint}"
            priority = 0.66 if kind != "news" else 0.52
            topics.append((hint, kind, reason, priority))

    condensed = " ".join(text.split()[:10]).strip()
    if condensed:
        kind = _topic_kind(condensed, task_class, text, config=config)
        topics.append((condensed, kind, "user_request", 0.72 if kind != "news" else 0.55))

    deduped: list[CuriosityTopic] = []
    seen: set[str] = set()
    kind_counts: dict[str, int] = {}
    for topic_text, topic_kind, reason, priority in topics:
        normalized = topic_text.lower().strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        limit = source_kind_limit(config, topic_kind)
        if kind_counts.get(topic_kind, 0) >= limit:
            continue
        profiles = tuple(profiles_for_topic(topic_kind, topic_text))
        if not profiles:
            continue
        deduped.append(
            CuriosityTopic(
                topic=topic_text,
                topic_kind=topic_kind,
                reason=reason,
                priority=priority,
                source_profiles=profiles,
            )
        )
        kind_counts[topic_kind] = kind_counts.get(topic_kind, 0) + 1
        if len(deduped) >= config.max_topics_per_task:
            break
    return deduped


def _topic_kind(topic: str, task_class: str, full_text: str, *, config: CuriosityConfig) -> str:
    lowered = f" {topic} {full_text} ".lower()
    words = {word for word in lowered.replace("/", " ").replace("-", " ").split() if word}
    has_design = any(
        phrase in lowered
        for phrase in (" design ", " layout ", " theme ", " mobile app ", " web app ")
    ) or bool({"ux", "ui"} & words)
    has_integration = any(token in lowered for token in ("telegram", "discord", "bot", "api", "integration"))
    if config.allow_news_pulse and any(token in lowered for token in ("news", "headline", "current events", "pulse", "today", "planet")):
        return "news"
    if has_integration:
        return "integration"
    if has_design:
        return "design"
    if task_class in {"research", "system_design", "dependency_resolution", "config"}:
        return "technical"
    return "general"


def _candidate_confidence(topic: CuriosityTopic, snippets: list[dict[str, Any]]) -> float:
    base = 0.34 + min(0.24, 0.04 * len(snippets))
    avg_trust = sum(profile.trust_weight for profile in topic.source_profiles[:2]) / max(1, min(2, len(topic.source_profiles)))
    domain_scores = [
        float(dict(snippet.get("source_credibility") or {}).get("score") or 0.0)
        for snippet in snippets
        if snippet.get("source_credibility")
    ]
    if domain_scores:
        avg_trust = (avg_trust + (sum(domain_scores) / len(domain_scores))) / 2.0
    if topic.topic_kind == "news":
        avg_trust = min(avg_trust, 0.58)
    return max(0.25, min(0.84, base + (0.42 * avg_trust)))


def _idle_commons_topic(*, seed_index: int | None = None) -> CuriosityTopic:
    if seed_index is None:
        hour_index = int(datetime.now(timezone.utc).timestamp() // 3600)
        peer_salt = sum(ord(ch) for ch in get_local_peer_id()[:12])
        seed_index = (hour_index + peer_salt) % len(_IDLE_COMMONS_SEEDS)
    topic_kind, seed_text, reason = _IDLE_COMMONS_SEEDS[int(seed_index) % len(_IDLE_COMMONS_SEEDS)]
    topic_text = f"Agent commons brainstorm: {seed_text}"
    return CuriosityTopic(
        topic=topic_text,
        topic_kind=topic_kind,
        reason=reason,
        priority=0.74,
        source_profiles=tuple(profiles_for_topic(topic_kind, topic_text)),
    )


def _commons_public_body(*, topic: CuriosityTopic, summary: str, snippets: list[dict[str, Any]]) -> str:
    lines = [
        f"Agent commons update: {topic.topic}.",
        f"Reason: {topic.reason}.",
    ]
    clean_summary = " ".join(str(summary or "").split()).strip()
    if clean_summary:
        lines.append(clean_summary[:900])
    labels: list[str] = []
    for snippet in snippets[:3]:
        domain = str(snippet.get("origin_domain") or "").strip()
        label = domain or str(snippet.get("source_profile_label") or "curated_source").strip()
        if label and label not in labels:
            labels.append(label)
    if labels:
        lines.append("Signals reviewed: " + ", ".join(labels[:3]) + ".")
    return " ".join(part.strip() for part in lines if part.strip())[:1500]
