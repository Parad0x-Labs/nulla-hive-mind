from __future__ import annotations

import contextlib
import re
from typing import Any

from core.agent_runtime import hive_topic_pending as agent_hive_topic_pending
from core.agent_runtime import hive_topic_public_copy as agent_hive_topic_public_copy
from core.autonomous_topic_research import research_topic_from_signal
from core.credit_ledger import (
    escrow_credits_for_task,
    estimate_hive_task_credit_cost,
    get_credit_balance,
)
from core.privacy_guard import text_privacy_risks
from network import signer as signer_mod


def maybe_handle_hive_topic_create_request(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    draft = agent._extract_hive_topic_create_draft(user_input)
    if draft is None:
        return None

    if not agent.public_hive_bridge.enabled():
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="Public Hive is not enabled on this runtime, so I can't create a live Hive task. Hive truth: future/unsupported.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_topic_create_disabled",
            success=False,
            details={"status": "disabled"},
            mode_override="tool_failed",
            task_outcome="failed",
            workflow_summary=agent._action_workflow_summary(
                operator_kind="hive.create_topic",
                dispatch_status="disabled",
                details={"action_id": ""},
            ),
        )
    if not agent.public_hive_bridge.write_enabled():
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="Hive task creation is disabled here because public Hive auth is not configured for writes. Hive truth: future/unsupported.",
            confidence=0.9,
            source_context=source_context,
            reason="hive_topic_create_missing_auth",
            success=False,
            details={"status": "missing_auth"},
            mode_override="tool_failed",
            task_outcome="failed",
            workflow_summary=agent._action_workflow_summary(
                operator_kind="hive.create_topic",
                dispatch_status="missing_auth",
                details={"action_id": ""},
            ),
        )

    variant_result = agent._build_hive_create_pending_variants(
        raw_input=user_input,
        draft=draft,
        task_id=task.task_id,
    )
    if not bool(variant_result.get("ok")):
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response=str(variant_result.get("response") or "I won't create that Hive task."),
            confidence=0.9,
            source_context=source_context,
            reason=str(variant_result.get("reason") or "hive_topic_create_privacy_blocked"),
            success=False,
            details={
                "status": "privacy_blocked",
                "privacy_risks": list(variant_result.get("privacy_risks") or []),
            },
            mode_override="tool_failed",
            task_outcome="failed",
            workflow_summary=agent._action_workflow_summary(
                operator_kind="hive.create_topic",
                dispatch_status="privacy_blocked",
                details={"action_id": ""},
            ),
        )
    pending = dict(variant_result.get("pending") or {})
    improved_variant = dict((pending.get("variants") or {}).get("improved") or {})
    title = str(improved_variant.get("title") or "").strip()
    summary = str(improved_variant.get("summary") or "").strip() or title
    preview_note = str(improved_variant.get("preview_note") or "")
    topic_tags = [
        str(item).strip()
        for item in list(improved_variant.get("topic_tags") or [])
        if str(item).strip()
    ][:8]
    if len(title) < 4:
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response=(
                "I can create the Hive task, but I still need a concrete title. "
                'Use a format like: create new task in Hive: "better watcher task UX".'
            ),
            confidence=0.42,
            source_context=source_context,
            reason="hive_topic_create_missing_title",
            success=False,
            details={"status": "missing_title"},
            mode_override="tool_failed",
            task_outcome="failed",
            workflow_summary=agent._action_workflow_summary(
                operator_kind="hive.create_topic",
                dispatch_status="missing_title",
                details={"action_id": ""},
            ),
        )

    dup = agent._check_hive_duplicate(title, summary)
    agent._remember_hive_create_pending(session_id, pending)
    estimated_cost = estimate_hive_task_credit_cost(
        title,
        summary,
        topic_tags=topic_tags,
        auto_start_research=bool(improved_variant.get("auto_start_research")),
    )
    dup_warning = ""
    if dup:
        dup_title = dup.get("title", "")
        dup_id = str(dup.get("topic_id") or "")[:8]
        dup_warning = (
            f"\n\nHeads up -- a similar topic already exists: "
            f"**{dup_title}** (#{dup_id}). Still want to create a new one?"
        )
    preview = agent._format_hive_create_preview(
        pending=pending,
        estimated_cost=estimated_cost,
        dup_warning=dup_warning,
        preview_note=preview_note,
    )
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=user_input,
        response=preview,
        confidence=0.95,
        source_context=source_context,
        reason="hive_topic_create_awaiting_confirmation",
        success=True,
        details={
            "status": "awaiting_confirmation",
            "title": title,
            "topic_tags": topic_tags,
            "default_variant": str(pending.get("default_variant") or "improved"),
        },
        mode_override="tool_preview",
        task_outcome="pending_approval",
        workflow_summary=agent._action_workflow_summary(
            operator_kind="hive.create_topic",
            dispatch_status="awaiting_confirmation",
            details={"action_id": ""},
        ),
    )


def execute_confirmed_hive_create(
    agent: Any,
    pending: dict[str, Any],
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
    user_input: str,
    variant: str,
    research_topic_from_signal_fn: Any = research_topic_from_signal,
) -> dict[str, Any]:
    variants = {
        key: dict(value)
        for key, value in dict(pending.get("variants") or {}).items()
        if isinstance(value, dict)
    }
    selected = dict(variants.get(variant or "") or variants.get("improved") or {})
    title = str(selected.get("title") or pending.get("title") or "").strip()
    summary = str(selected.get("summary") or pending.get("summary") or "").strip() or title
    topic_tags = [
        str(item).strip()
        for item in list(selected.get("topic_tags") or pending.get("topic_tags") or [])
        if str(item).strip()
    ][:8]
    linked_task_id = pending.get("task_id") or task.task_id
    auto_start_research = bool(selected.get("auto_start_research") or pending.get("auto_start_research")) or agent._wants_hive_create_auto_start(user_input)
    if variant == "original" and text_privacy_risks(f"{title}\n{summary}"):
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="The original Hive draft still looks private, so I won't post it. Use `send improved` instead.",
            confidence=0.92,
            source_context=source_context,
            reason="hive_topic_create_original_privacy_blocked",
            success=False,
            details={"status": "original_blocked"},
            mode_override="tool_failed",
            task_outcome="failed",
            workflow_summary=agent._action_workflow_summary(
                operator_kind="hive.create_topic",
                dispatch_status="original_blocked",
                details={"action_id": ""},
            ),
        )
    estimated_cost = estimate_hive_task_credit_cost(
        title,
        summary,
        topic_tags=topic_tags,
        auto_start_research=auto_start_research,
    )

    try:
        result = agent.public_hive_bridge.create_public_topic(
            title=title,
            summary=summary,
            topic_tags=topic_tags,
            linked_task_id=linked_task_id,
            idempotency_key=f"{linked_task_id}:hive_create",
        )
    except Exception as exc:
        error_text = str(exc or "").strip()
        lowered_error = error_text.lower()
        if "user command instead of agent analysis" in lowered_error:
            retry_title, retry_summary, _ = agent._shape_public_hive_admission_safe_copy(
                title=title,
                summary=summary,
                force=True,
            )
            if retry_title != title or retry_summary != summary:
                try:
                    result = agent.public_hive_bridge.create_public_topic(
                        title=retry_title,
                        summary=retry_summary,
                        topic_tags=topic_tags,
                        linked_task_id=linked_task_id,
                        idempotency_key=f"{linked_task_id}:hive_create",
                    )
                except Exception as retry_exc:
                    error_text = str(retry_exc or error_text).strip()
                    lowered_error = error_text.lower()
                else:
                    if result.get("ok") and str(result.get("topic_id") or "").strip():
                        title = retry_title
                        summary = retry_summary
                        error_text = ""
                    else:
                        status = str(result.get("status") or "admission_blocked").strip() or "admission_blocked"
                        return agent._action_fast_path_result(
                            task_id=task.task_id,
                            session_id=session_id,
                            user_input=user_input,
                            response=agent._hive_topic_create_failure_text(status),
                            confidence=0.46,
                            source_context=source_context,
                            reason=f"hive_topic_create_{status}",
                            success=False,
                            details={"status": status, **dict(result)},
                            mode_override="tool_failed",
                            task_outcome="failed",
                            workflow_summary=agent._action_workflow_summary(
                                operator_kind="hive.create_topic",
                                dispatch_status=status,
                                details={"action_id": ""},
                            ),
                        )
            else:
                lowered_error = error_text.lower()
        if not error_text:
            topic_id = str(result.get("topic_id") or "").strip()
            if not result.get("ok") or not topic_id:
                status = str(result.get("status") or "topic_failed").strip() or "topic_failed"
                return agent._action_fast_path_result(
                    task_id=task.task_id,
                    session_id=session_id,
                    user_input=user_input,
                    response=agent._hive_topic_create_failure_text(status),
                    confidence=0.46,
                    source_context=source_context,
                    reason=f"hive_topic_create_{status}",
                    success=False,
                    details={"status": status, **dict(result)},
                    mode_override="tool_failed",
                    task_outcome="failed",
                    workflow_summary=agent._action_workflow_summary(
                        operator_kind="hive.create_topic",
                        dispatch_status=status,
                        details={"action_id": ""},
                    ),
                )
        if error_text:
            lowered_error = error_text.lower()
            status = (
                "invalid_auth"
                if "unauthorized" in lowered_error
                else "admission_blocked"
                if "brain hive admission blocked" in lowered_error
                else "topic_failed"
            )
            return agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response=agent._hive_topic_create_failure_text(status),
                confidence=0.46,
                source_context=source_context,
                reason=f"hive_topic_create_{status}",
                success=False,
                details={"status": status, "error": error_text},
                mode_override="tool_failed",
                task_outcome="failed",
                workflow_summary=agent._action_workflow_summary(
                    operator_kind="hive.create_topic",
                    dispatch_status=status,
                    details={"action_id": ""},
                ),
            )
    topic_id = str(result.get("topic_id") or "").strip()
    if not result.get("ok") or not topic_id:
        status = str(result.get("status") or "topic_failed").strip() or "topic_failed"
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response=agent._hive_topic_create_failure_text(status),
            confidence=0.46,
            source_context=source_context,
            reason=f"hive_topic_create_{status}",
            success=False,
            details={"status": status, **dict(result)},
            mode_override="tool_failed",
            task_outcome="failed",
            workflow_summary=agent._action_workflow_summary(
                operator_kind="hive.create_topic",
                dispatch_status=status,
                details={"action_id": ""},
            ),
        )

    with contextlib.suppress(Exception):
        agent.hive_activity_tracker.note_watched_topic(session_id=session_id, topic_id=topic_id)
    tag_suffix = f" Tags: {', '.join(topic_tags[:6])}." if topic_tags else ""
    variant_suffix = (
        f" Using {variant or 'improved'} draft."
        if dict(pending.get("variants") or {}).get("original")
        else ""
    )
    response = f"Created Hive task `{title}` (#{topic_id[:8]}).{tag_suffix}{variant_suffix}"
    if estimated_cost > 0:
        peer_id = signer_mod.get_local_peer_id()
        if escrow_credits_for_task(
            peer_id,
            topic_id,
            estimated_cost,
            receipt_id=f"hive_task_escrow:{topic_id}",
        ):
            response = (
                f"{response} Reserved {estimated_cost:.1f} credits for Hive payouts. "
                f"Remaining balance: {get_credit_balance(peer_id):.2f}."
            )
        else:
            response = (
                f"{response} No credits were reserved because your current balance is "
                f"{get_credit_balance(peer_id):.2f}."
            )
    if auto_start_research:
        signal = {"topic_id": topic_id, "title": title}
        agent._sync_public_presence(status="busy", source_context=source_context)
        research_result = research_topic_from_signal_fn(
            signal,
            public_hive_bridge=agent.public_hive_bridge,
            curiosity=agent.curiosity,
            hive_activity_tracker=agent.hive_activity_tracker,
            session_id=session_id,
            auto_claim=True,
        )
        if research_result.ok:
            agent_hive_topic_pending.set_hive_interaction_state(
                session_id,
                mode="hive_task_active",
                payload={
                    "active_topic_id": topic_id,
                    "active_title": title,
                    "claim_id": str(research_result.claim_id or "").strip(),
                },
            )
            response = f"{response} Started Hive research on `{title}`."
            if research_result.claim_id:
                response = f"{response} Claim `{str(research_result.claim_id)[:8]}` is active."
        else:
            failure_text = str(research_result.response_text or "").strip()
            if failure_text:
                response = f"{response} The task is live, but starting research failed: {failure_text}"
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.95,
        source_context=source_context,
        reason="hive_topic_create_created",
        success=True,
        details={"status": "created", "topic_id": topic_id, "topic_tags": topic_tags},
        mode_override="tool_executed",
        task_outcome="success",
        workflow_summary=agent._action_workflow_summary(
            operator_kind="hive.create_topic",
            dispatch_status="created",
            details={"action_id": topic_id},
        ),
    )


def check_hive_duplicate(agent: Any, title: str, summary: str) -> dict[str, Any] | None:
    """Check if a similar hive topic exists within the last 3 days."""
    try:
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
        topics = agent.public_hive_bridge.list_public_topics(limit=50)
        title_tokens = set(title.lower().split())
        summary_tokens = set(summary.lower().split()[:30])
        all_tokens = title_tokens | summary_tokens
        stop_words = {
            "the",
            "a",
            "an",
            "is",
            "to",
            "for",
            "on",
            "in",
            "of",
            "and",
            "or",
            "how",
            "what",
            "why",
            "create",
            "task",
            "new",
            "hive",
        }
        meaningful = all_tokens - stop_words
        if not meaningful:
            return None
        for topic in topics:
            topic_date = str(topic.get("updated_at") or topic.get("created_at") or "")
            if topic_date and topic_date < cutoff:
                continue
            t_title = str(topic.get("title") or "").lower()
            t_summary = str(topic.get("summary") or "").lower()
            t_tokens = set(t_title.split()) | set(t_summary.split()[:30])
            overlap = meaningful & t_tokens
            if len(overlap) >= max(2, len(meaningful) * 0.5):
                return topic
    except Exception:
        pass
    return None


def clean_hive_title(raw: str) -> str:
    """Basic cleanup: strip command prefixes, fix common doubled chars, capitalize."""
    title = re.sub(
        r"^(?:create\s+(?:a\s+)?(?:hive\s+)?task\s*[-:—]*\s*)",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
    title = re.sub(r"^[-:—]+\s*", "", title).strip()
    if title and title[0].islower():
        title = title[0].upper() + title[1:]
    return title or raw


def extract_hive_topic_create_draft(agent: Any, text: str) -> dict[str, Any] | None:
    clean = " ".join(str(text or "").split()).strip()
    lowered = clean.lower()
    if not agent._looks_like_hive_topic_create_request(lowered):
        return None

    sections = {
        "title": re.search(r"\b(?:name it|title|call it|called)\b\s*[:=-]?\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
        "task": re.search(r"\btask\b\s*[:=-]\s*(.+?)(?=(?:\b(?:goal|summary)\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
        "goal": re.search(r"\bgoal\b\s*[:=-]\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
        "summary": re.search(r"\bsummary\b\s*[:=-]\s*(.+?)(?=(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", clean, re.IGNORECASE),
        "tags": re.search(r"\b(?:topic tags?|tags?)\b\s*[:=-]\s*(.+)$", clean, re.IGNORECASE),
    }
    title = ""
    if sections["title"] is not None:
        title = str(sections["title"].group(1) or "")
    elif sections["task"] is not None:
        title = str(sections["task"].group(1) or "")
    elif ":" in clean:
        title = clean.rsplit(":", 1)[-1]
    else:
        title = re.sub(r"^.*?\bhive\b[?!.,:;-]*\s*", "", clean, flags=re.IGNORECASE)
    title = re.sub(r"^(?:name it|title|call it|called)\b\s*[:=-]?\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(
        r"^(?:(?:ok\s+)?(?:lets?|let'?s|can you|please|pls|now)\s+)*"
        r"(?:create|make|start|open|add)\s+"
        r"(?:(?:a|the|new|hive|brain hive|this)\s+)*"
        r"(?:task|topic|thread)\s*"
        r"(?:(?:on|in|for|to|at)\s+(?:(?:the\s+)?(?:hive|hive mind|brain hive))\s*)?",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip().lstrip("-–—:;/.,!? ")
    if not title:
        for prefix in ("create task", "create new task", "create hive task", "new task", "add task"):
            if clean.lower().startswith(prefix):
                title = clean[len(prefix):].strip().lstrip("-:–/")
                break
    if re.match(r"^.{0,30}---+", title):
        title = re.sub(r"^.{0,30}---+\s*", "", title).strip()
    if " - " in title and len(title.split(" - ", 1)[1].strip()) > 15:
        title = title.split(" - ", 1)[1].strip()
    title = re.sub(r"^(?:task|goal|summary)\s*[:=-]\s*", "", title, flags=re.IGNORECASE).strip()
    title = agent._strip_wrapping_quotes(" ".join(title.split()).strip().strip("."))

    summary = ""
    if sections["summary"] is not None:
        summary = agent._strip_wrapping_quotes(" ".join(str(sections["summary"].group(1) or "").split()).strip().strip("."))
    elif sections["goal"] is not None:
        summary = agent._strip_wrapping_quotes(" ".join(str(sections["goal"].group(1) or "").split()).strip().strip("."))
    if not summary and title:
        summary = title

    topic_tags: list[str] = []
    if sections["tags"] is not None:
        raw_tags = str(sections["tags"].group(1) or "")
        topic_tags = [
            normalized
            for normalized in (
                agent._normalize_hive_topic_tag(item)
                for item in re.split(r"[,;|/]+", raw_tags)
            )
            if normalized
        ][:8]
    if not topic_tags and title:
        topic_tags = agent._infer_hive_topic_tags(title)

    return {
        "title": title[:180],
        "summary": summary[:4000],
        "topic_tags": topic_tags[:8],
        "auto_start_research": agent._wants_hive_create_auto_start(clean),
    }


def extract_original_hive_topic_create_draft(agent: Any, text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    compact = " ".join(raw.split()).strip()
    if not agent._looks_like_hive_topic_create_request(compact.lower()):
        return None
    sections = {
        "title": re.search(r"\b(?:name it|title|call it|called)\b\s*[:=-]?\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", compact, re.IGNORECASE),
        "task": re.search(r"\btask\b\s*[:=-]\s*(.+?)(?=(?:\b(?:goal|summary)\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", compact, re.IGNORECASE),
        "goal": re.search(r"\bgoal\b\s*[:=-]\s*(.+?)(?=(?:\bsummary\b\s*[:=-])|(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", compact, re.IGNORECASE),
        "summary": re.search(r"\bsummary\b\s*[:=-]\s*(.+?)(?=(?:\b(?:topic tags?|tags?)\b\s*[:=-])|$)", compact, re.IGNORECASE),
        "tags": re.search(r"\b(?:topic tags?|tags?)\b\s*[:=-]\s*(.+)$", compact, re.IGNORECASE),
    }
    title = ""
    if sections["title"] is not None:
        title = str(sections["title"].group(1) or "")
    elif sections["task"] is not None:
        title = str(sections["task"].group(1) or "")
    elif ":" in compact:
        title = compact.rsplit(":", 1)[-1]
    title = re.sub(r"^(?:task|title|name it|call it|called)\s*[:=-]\s*", "", title, flags=re.IGNORECASE).strip()
    title = agent._strip_wrapping_quotes(" ".join(title.split()).strip().strip("."))
    summary = ""
    if sections["summary"] is not None:
        summary = agent._strip_wrapping_quotes(" ".join(str(sections["summary"].group(1) or "").split()).strip().strip("."))
    elif sections["goal"] is not None:
        summary = agent._strip_wrapping_quotes(" ".join(str(sections["goal"].group(1) or "").split()).strip().strip("."))
    if not summary and title:
        summary = title
    topic_tags: list[str] = []
    if sections["tags"] is not None:
        raw_tags = str(sections["tags"].group(1) or "")
        topic_tags = [
            normalized
            for normalized in (
                agent._normalize_hive_topic_tag(item)
                for item in re.split(r"[,;|/]+", raw_tags)
            )
            if normalized
        ][:8]
    if not topic_tags and title:
        topic_tags = agent._infer_hive_topic_tags(title)
    if not title:
        return None
    return {
        "title": title[:180],
        "summary": summary[:4000],
        "topic_tags": topic_tags[:8],
        "auto_start_research": agent._wants_hive_create_auto_start(compact),
    }


def build_hive_create_pending_variants(
    agent: Any,
    *,
    raw_input: str,
    draft: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    improved_title = agent._clean_hive_title(str(draft.get("title") or "").strip())
    improved_summary = str(draft.get("summary") or "").strip() or improved_title
    improved_copy = agent._prepare_public_hive_topic_copy(
        raw_input=raw_input,
        title=improved_title,
        summary=improved_summary,
        mode="improved",
    )
    if not bool(improved_copy.get("ok")):
        return improved_copy

    improved_variant = agent._normalize_hive_create_variant(
        title=str(improved_copy.get("title") or improved_title).strip() or improved_title,
        summary=str(improved_copy.get("summary") or improved_summary).strip() or improved_summary,
        topic_tags=[
            str(item).strip()
            for item in list(draft.get("topic_tags") or [])
            if str(item).strip()
        ][:8],
        auto_start_research=bool(draft.get("auto_start_research")),
        preview_note=str(improved_copy.get("preview_note") or ""),
    )

    original_variant: dict[str, Any] | None = None
    original_blocked_reason = ""
    original_draft = agent._extract_original_hive_topic_create_draft(raw_input)
    if original_draft is not None:
        same_title = str(original_draft.get("title") or "").strip() == str(improved_variant.get("title") or "").strip()
        same_summary = str(original_draft.get("summary") or "").strip() == str(improved_variant.get("summary") or "").strip()
        if not (same_title and same_summary):
            original_copy = agent._prepare_public_hive_topic_copy(
                raw_input=raw_input,
                title=str(original_draft.get("title") or "").strip(),
                summary=str(original_draft.get("summary") or "").strip()
                or str(original_draft.get("title") or "").strip(),
                mode="original",
            )
            if bool(original_copy.get("ok")):
                original_variant = agent._normalize_hive_create_variant(
                    title=str(original_copy.get("title") or "").strip(),
                    summary=str(original_copy.get("summary") or "").strip(),
                    topic_tags=[
                        str(item).strip()
                        for item in list(original_draft.get("topic_tags") or [])
                        if str(item).strip()
                    ][:8],
                    auto_start_research=bool(original_draft.get("auto_start_research")),
                    preview_note=str(original_copy.get("preview_note") or ""),
                )
            else:
                original_blocked_reason = str(original_copy.get("response") or "").strip()

    pending = {
        "title": str(improved_variant.get("title") or "").strip(),
        "summary": str(improved_variant.get("summary") or "").strip(),
        "topic_tags": list(improved_variant.get("topic_tags") or []),
        "task_id": str(task_id or "").strip(),
        "auto_start_research": bool(improved_variant.get("auto_start_research")),
        "default_variant": "improved",
        "variants": {"improved": improved_variant},
        "original_blocked_reason": original_blocked_reason,
    }
    if original_variant is not None:
        pending["variants"]["original"] = original_variant
    return {"ok": True, "pending": pending}


def normalize_hive_create_variant(
    agent: Any,
    *,
    title: str,
    summary: str,
    topic_tags: list[str],
    auto_start_research: bool,
    preview_note: str = "",
) -> dict[str, Any]:
    resolved_title = str(title or "").strip()[:180]
    resolved_summary = str(summary or "").strip()[:4000] or resolved_title
    resolved_tags = [
        str(item).strip()
        for item in list(topic_tags or [])[:8]
        if str(item).strip()
    ]
    if not resolved_tags and resolved_title:
        resolved_tags = agent._infer_hive_topic_tags(resolved_title)
    return {
        "title": resolved_title,
        "summary": resolved_summary,
        "topic_tags": resolved_tags[:8],
        "auto_start_research": bool(auto_start_research),
        "preview_note": str(preview_note or "").strip(),
    }


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


prepare_public_hive_topic_copy = agent_hive_topic_public_copy.prepare_public_hive_topic_copy
sanitize_public_hive_text = agent_hive_topic_public_copy.sanitize_public_hive_text
shape_public_hive_admission_safe_copy = agent_hive_topic_public_copy.shape_public_hive_admission_safe_copy
has_structured_hive_public_brief = agent_hive_topic_public_copy.has_structured_hive_public_brief
looks_like_raw_chat_transcript = agent_hive_topic_public_copy.looks_like_raw_chat_transcript
maybe_handle_hive_create_confirmation = agent_hive_topic_pending.maybe_handle_hive_create_confirmation
has_pending_hive_create_confirmation = agent_hive_topic_pending.has_pending_hive_create_confirmation
is_pending_hive_create_confirmation_input = agent_hive_topic_pending.is_pending_hive_create_confirmation_input
format_hive_create_preview = agent_hive_topic_pending.format_hive_create_preview
preview_text_snippet = agent_hive_topic_pending.preview_text_snippet
parse_hive_create_variant_choice = agent_hive_topic_pending.parse_hive_create_variant_choice
remember_hive_create_pending = agent_hive_topic_pending.remember_hive_create_pending
clear_hive_create_pending = agent_hive_topic_pending.clear_hive_create_pending
load_pending_hive_create = agent_hive_topic_pending.load_pending_hive_create
recover_hive_create_pending_from_history = agent_hive_topic_pending.recover_hive_create_pending_from_history


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


infer_hive_topic_tags = agent_hive_topic_public_copy.infer_hive_topic_tags
normalize_hive_topic_tag = agent_hive_topic_public_copy.normalize_hive_topic_tag
strip_wrapping_quotes = agent_hive_topic_public_copy.strip_wrapping_quotes


def hive_topic_create_failure_text(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "privacy_blocked_topic":
        return "I won't create that Hive task because it looks like it contains private or secret material."
    if normalized == "missing_target":
        return "Hive topic creation is configured incompletely on this runtime, so I can't post the task yet. Hive truth: future/unsupported."
    if normalized == "disabled":
        return "Public Hive is not enabled on this runtime, so I can't create a live Hive task. Hive truth: future/unsupported."
    if normalized == "missing_auth":
        return "Hive task creation is disabled here because public Hive auth is not configured for writes. Hive truth: future/unsupported."
    if normalized == "invalid_auth":
        return "Hive task creation is configured, but the live Hive rejected this runtime's write auth. I need to refresh public Hive auth before posting."
    if normalized == "admission_blocked":
        return "The live Hive rejected that task draft as too command-like or low-substance. I need to frame it as agent analysis before posting."
    if normalized == "empty_topic":
        return "I can create the Hive task, but I still need a concrete title and summary."
    return "I couldn't create that Hive task."
