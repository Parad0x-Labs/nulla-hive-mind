from __future__ import annotations

import contextlib
import re
from typing import Any

from core.autonomous_topic_research import research_topic_from_signal
from core.credit_ledger import (
    escrow_credits_for_task,
    estimate_hive_task_credit_cost,
    get_credit_balance,
)
from core.hive_activity_tracker import (
    clear_hive_interaction_state,
    session_hive_state,
    set_hive_interaction_state,
)
from core.privacy_guard import text_privacy_risks
from core.task_router import redact_text
from network import signer as signer_mod

HIVE_CREATE_HARD_PRIVACY_RISKS = {
    "identity_marker",
    "name_disclosure",
    "location_disclosure",
    "phone_number",
    "postal_address",
}

HIVE_CONFIRM_POSITIVE_STRICT = re.compile(
    r"^\s*(?:yes|yea|yeah|yep|yup|ok(?:ay)?|sure|do\s*it|go\s*(?:ahead|for\s*it)|"
    r"lets?\s*(?:go|do\s*it)|for\s*sure|absolutely|confirmed?|lgtm|send\s*it|"
    r"post\s*it|create\s*it|ship\s*it|proceed|affirmative|y)\s*[.!]*\s*$",
    re.IGNORECASE,
)
HIVE_CONFIRM_POSITIVE_LOOSE = re.compile(
    r"^\s*(?:yes|yea|yeah|yep|yup|ok(?:ay)?|sure|do\s*it|go\s*(?:ahead|for\s*it)|"
    r"lets?\s*(?:go|do\s*it)|for\s*sure|absolutely|confirmed?|lgtm|send\s*it|"
    r"post\s*it|create\s*it|ship\s*it|proceed|affirmative)\b",
    re.IGNORECASE,
)
HIVE_CONFIRM_NEGATIVE = re.compile(
    r"^\s*(?:no|nah|nope|not?\s*now|later|meh|cancel|stop|skip|forget\s*it|"
    r"never\s*mind|nevermind|don'?t|nay|negative|n)\s*[.!]*\s*$",
    re.IGNORECASE,
)


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


def maybe_handle_hive_create_confirmation(
    agent: Any,
    user_input: str,
    *,
    task: Any,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    lowered = user_input.strip()
    variant_choice = agent._parse_hive_create_variant_choice(lowered)
    is_positive = bool(
        HIVE_CONFIRM_POSITIVE_STRICT.match(lowered) or HIVE_CONFIRM_POSITIVE_LOOSE.match(lowered)
    )
    is_negative = bool(HIVE_CONFIRM_NEGATIVE.match(lowered))
    pending = agent._load_pending_hive_create(
        session_id=session_id,
        source_context=source_context,
        fallback_task_id=task.task_id,
        allow_history_recovery=is_positive or is_negative or bool(variant_choice),
    )
    if pending is None:
        return None

    if is_positive or bool(variant_choice):
        chosen_variant = variant_choice or str(pending.get("default_variant") or "improved")
        available_variants = {
            key: dict(value)
            for key, value in dict(pending.get("variants") or {}).items()
            if isinstance(value, dict)
        }
        if chosen_variant == "original" and "original" not in available_variants:
            blocked_reason = str(pending.get("original_blocked_reason") or "").strip()
            reply = blocked_reason or "The original Hive draft isn't safe to publish. Use `send improved` instead."
            return agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=user_input,
                response=reply,
                confidence=0.92,
                source_context=source_context,
                reason="hive_topic_create_original_blocked",
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
        agent._clear_hive_create_pending(session_id)
        return agent._execute_confirmed_hive_create(
            pending,
            task=task,
            session_id=session_id,
            source_context=source_context,
            user_input=user_input,
            variant=chosen_variant,
        )

    if is_negative:
        agent._clear_hive_create_pending(session_id)
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=user_input,
            response="Got it -- Hive task discarded. What's next?",
            confidence=0.95,
            source_context=source_context,
            reason="hive_topic_create_cancelled",
            success=True,
            details={"status": "cancelled"},
            mode_override="tool_executed",
            task_outcome="cancelled",
            workflow_summary=agent._action_workflow_summary(
                operator_kind="hive.create_topic",
                dispatch_status="cancelled",
                details={"action_id": ""},
            ),
        )

    return None


def has_pending_hive_create_confirmation(
    agent: Any,
    *,
    session_id: str,
    hive_state: dict[str, Any],
    source_context: dict[str, object] | None,
) -> bool:
    pending = agent._hive_create_pending.get(session_id)
    if pending and str(pending.get("title") or "").strip():
        return True

    payload = dict(hive_state.get("interaction_payload") or {})
    stored = dict(payload.get("pending_hive_create") or {})
    if str(stored.get("title") or "").strip():
        return True

    recovered = agent._recover_hive_create_pending_from_history(
        history=list((source_context or {}).get("conversation_history") or []),
        fallback_task_id="",
    )
    return recovered is not None


def is_pending_hive_create_confirmation_input(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    hive_state: dict[str, Any] | None = None,
    session_hive_state_fn: Any = session_hive_state,
) -> bool:
    clean = " ".join(str(user_input or "").split()).strip()
    if not clean:
        return False
    is_confirmation = bool(
        HIVE_CONFIRM_POSITIVE_STRICT.match(clean)
        or HIVE_CONFIRM_POSITIVE_LOOSE.match(clean)
        or HIVE_CONFIRM_NEGATIVE.match(clean)
    )
    if not is_confirmation:
        return False
    state = hive_state or session_hive_state_fn(session_id)
    return agent._has_pending_hive_create_confirmation(
        session_id=session_id,
        hive_state=state,
        source_context=source_context,
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
            set_hive_interaction_state(
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


def format_hive_create_preview(
    agent: Any,
    *,
    pending: dict[str, Any],
    estimated_cost: float,
    dup_warning: str,
    preview_note: str,
) -> str:
    variants = {
        key: dict(value)
        for key, value in dict(pending.get("variants") or {}).items()
        if isinstance(value, dict)
    }
    improved = dict(variants.get("improved") or {})
    original = dict(variants.get("original") or {})
    tag_line = ""
    improved_tags = [
        str(item).strip()
        for item in list(improved.get("topic_tags") or [])
        if str(item).strip()
    ][:6]
    if improved_tags:
        tag_line = f"\nTags: {', '.join(improved_tags)}"
    cost_line = f"\nEstimated reward pool: {estimated_cost:.1f} credits." if estimated_cost > 0 else ""
    if original or str(pending.get("original_blocked_reason") or "").strip():
        lines = [
            "Ready to post this to the public Hive:",
            "",
            "Improved draft (default):",
            f"**{str(improved.get('title') or '').strip()}**",
            f"Summary: {agent._preview_text_snippet(str(improved.get('summary') or '').strip())}",
        ]
        if tag_line:
            lines.append(tag_line.strip())
        if cost_line:
            lines.append(cost_line.strip())
        if preview_note:
            lines.append(preview_note.strip())
        if original:
            lines.extend(
                [
                    "",
                    "Original draft:",
                    f"**{str(original.get('title') or '').strip()}**",
                    f"Summary: {agent._preview_text_snippet(str(original.get('summary') or '').strip())}",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "Original draft:",
                    str(pending.get("original_blocked_reason") or "Blocked for privacy."),
                ]
            )
        if dup_warning:
            lines.append(dup_warning.strip())
        reply_line = "Reply: `send improved` / `no`." if not original else "Reply: `send improved` / `send original` / `no`."
        lines.extend(["", reply_line])
        return "\n".join(line for line in lines if line is not None)
    return (
        f"Ready to post this to the public Hive:\n\n"
        f"**{str(improved.get('title') or '').strip()}**{tag_line}{cost_line}{dup_warning}{preview_note}\n\n"
        f"Confirm? (yes / no)"
    )


def preview_text_snippet(text: str, *, limit: int = 220) -> str:
    clean = " ".join(str(text or "").split()).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def parse_hive_create_variant_choice(text: str) -> str:
    compact = " ".join(str(text or "").split()).strip().lower()
    if re.fullmatch(r"(?:yes\s+)?(?:send\s+)?improved(?:\s+draft)?", compact):
        return "improved"
    if re.fullmatch(r"(?:yes\s+)?(?:send\s+)?original(?:\s+draft)?", compact):
        return "original"
    return ""


def remember_hive_create_pending(
    agent: Any,
    session_id: str,
    pending: dict[str, Any],
    *,
    set_hive_interaction_state_fn: Any = set_hive_interaction_state,
) -> None:
    variants = {
        key: agent._normalize_hive_create_variant(
            title=str(dict(value).get("title") or ""),
            summary=str(dict(value).get("summary") or ""),
            topic_tags=[
                str(item).strip()
                for item in list(dict(value).get("topic_tags") or [])
                if str(item).strip()
            ][:8],
            auto_start_research=bool(dict(value).get("auto_start_research")),
            preview_note=str(dict(value).get("preview_note") or ""),
        )
        for key, value in dict(pending.get("variants") or {}).items()
        if isinstance(value, dict)
    }
    if not variants:
        variants["improved"] = agent._normalize_hive_create_variant(
            title=str(pending.get("title") or "").strip(),
            summary=str(pending.get("summary") or "").strip(),
            topic_tags=[
                str(item).strip()
                for item in list(pending.get("topic_tags") or [])
                if str(item).strip()
            ][:8],
            auto_start_research=bool(pending.get("auto_start_research")),
        )
    payload = {
        "title": str((variants.get("improved") or {}).get("title") or pending.get("title") or "").strip(),
        "summary": str((variants.get("improved") or {}).get("summary") or pending.get("summary") or "").strip(),
        "topic_tags": list((variants.get("improved") or {}).get("topic_tags") or [])[:8],
        "task_id": str(pending.get("task_id") or "").strip(),
        "auto_start_research": bool((variants.get("improved") or {}).get("auto_start_research") or pending.get("auto_start_research")),
        "default_variant": str(pending.get("default_variant") or "improved"),
        "variants": variants,
        "original_blocked_reason": str(pending.get("original_blocked_reason") or "").strip(),
    }
    agent._hive_create_pending[session_id] = dict(payload)
    set_hive_interaction_state_fn(
        session_id,
        mode="hive_topic_create_pending",
        payload={"pending_hive_create": payload},
    )


def clear_hive_create_pending(
    agent: Any,
    session_id: str,
    *,
    session_hive_state_fn: Any = session_hive_state,
    clear_hive_interaction_state_fn: Any = clear_hive_interaction_state,
) -> None:
    agent._hive_create_pending.pop(session_id, None)
    hive_state = session_hive_state_fn(session_id)
    if str(hive_state.get("interaction_mode") or "").strip().lower() == "hive_topic_create_pending":
        clear_hive_interaction_state_fn(session_id)


def load_pending_hive_create(
    agent: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    fallback_task_id: str,
    allow_history_recovery: bool,
    session_hive_state_fn: Any = session_hive_state,
) -> dict[str, Any] | None:
    pending = agent._hive_create_pending.get(session_id)
    if pending:
        return dict(pending)

    hive_state = session_hive_state_fn(session_id)
    payload = dict(hive_state.get("interaction_payload") or {})
    stored = dict(payload.get("pending_hive_create") or {})
    if stored and (str(stored.get("title") or "").strip() or dict(stored.get("variants") or {})):
        variants = {
            key: agent._normalize_hive_create_variant(
                title=str(dict(value).get("title") or ""),
                summary=str(dict(value).get("summary") or ""),
                topic_tags=[
                    str(item).strip()
                    for item in list(dict(value).get("topic_tags") or [])
                    if str(item).strip()
                ][:8],
                auto_start_research=bool(dict(value).get("auto_start_research")),
                preview_note=str(dict(value).get("preview_note") or ""),
            )
            for key, value in dict(stored.get("variants") or {}).items()
            if isinstance(value, dict)
        }
        if not variants and str(stored.get("title") or "").strip():
            variants["improved"] = agent._normalize_hive_create_variant(
                title=str(stored.get("title") or "").strip(),
                summary=str(stored.get("summary") or "").strip()
                or str(stored.get("title") or "").strip(),
                topic_tags=[
                    str(item).strip()
                    for item in list(stored.get("topic_tags") or [])
                    if str(item).strip()
                ][:8],
                auto_start_research=bool(stored.get("auto_start_research")),
            )
        recovered = {
            "title": str((variants.get("improved") or {}).get("title") or stored.get("title") or "").strip(),
            "summary": str((variants.get("improved") or {}).get("summary") or stored.get("summary") or "").strip()
            or str(stored.get("title") or "").strip(),
            "topic_tags": list((variants.get("improved") or {}).get("topic_tags") or [])[:8],
            "task_id": str(stored.get("task_id") or "").strip() or fallback_task_id,
            "auto_start_research": bool((variants.get("improved") or {}).get("auto_start_research") or stored.get("auto_start_research")),
            "default_variant": str(stored.get("default_variant") or "improved"),
            "variants": variants,
            "original_blocked_reason": str(stored.get("original_blocked_reason") or "").strip(),
        }
        agent._hive_create_pending[session_id] = dict(recovered)
        return recovered

    if not allow_history_recovery:
        return None
    recovered = agent._recover_hive_create_pending_from_history(
        history=list((source_context or {}).get("conversation_history") or []),
        fallback_task_id=fallback_task_id,
    )
    if recovered is not None:
        agent._remember_hive_create_pending(session_id, recovered)
    return recovered


def recover_hive_create_pending_from_history(
    agent: Any,
    *,
    history: list[dict[str, Any]],
    fallback_task_id: str,
) -> dict[str, Any] | None:
    recent_messages = [dict(item) for item in list(history or [])[-8:] if isinstance(item, dict)]
    latest_user_text = ""
    latest_user_draft: dict[str, Any] | None = None
    for message in reversed(recent_messages):
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "")
        if not content:
            continue
        if latest_user_draft is None and role == "user":
            draft = agent._extract_hive_topic_create_draft(content)
            if draft is not None and str(draft.get("title") or "").strip():
                latest_user_text = content
                latest_user_draft = draft
                break

    if not latest_user_text or latest_user_draft is None:
        return None
    result = agent._build_hive_create_pending_variants(
        raw_input=latest_user_text,
        draft=latest_user_draft,
        task_id=fallback_task_id,
    )
    if not bool(result.get("ok")):
        return None
    return dict(result.get("pending") or {})


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
    if agent._looks_like_raw_chat_transcript(raw_input) and not agent._has_structured_hive_public_brief(raw_input):
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
    sanitized_title = agent._sanitize_public_hive_text(clean_title)
    sanitized_summary = agent._sanitize_public_hive_text(clean_summary) or sanitized_title
    sanitized_title, sanitized_summary, admission_note = agent._shape_public_hive_admission_safe_copy(
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
        or risk in {"email", "filesystem_path", "secret_assignment", "openai_key", "github_token", "aws_access_key", "slack_token"}
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


def has_structured_hive_public_brief(text: str) -> bool:
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return False
    return bool(
        re.search(r"\b(?:task|goal|summary|title|name it|call it|called)\b\s*[:=-]", clean, re.IGNORECASE)
    )


def looks_like_raw_chat_transcript(text: str) -> bool:
    raw = str(text or "")
    if not raw.strip():
        return False
    hits = 0
    patterns = (
        r"(?m)^\s*(?:NULLA|You|User|Assistant|U)\s*$",
        r"\b\d{1,2}:\d{2}\b",
        r"(?m)^\s*/new\s*$",
        r"∅",
        r"(?m)^\s*(?:U|A)\s*$",
    )
    for pattern in patterns:
        if re.search(pattern, raw, re.IGNORECASE):
            hits += 1
    return hits >= 2


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


def strip_wrapping_quotes(text: str) -> str:
    clean = str(text or "").strip()
    if len(clean) >= 2 and clean[0] == clean[-1] and clean[0] in {'"', "'", "`"}:
        return clean[1:-1].strip()
    return clean


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
