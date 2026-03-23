from __future__ import annotations

import re
from typing import Any

from core.hive_activity_tracker import (
    clear_hive_interaction_state,
    session_hive_state,
    set_hive_interaction_state,
)

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
