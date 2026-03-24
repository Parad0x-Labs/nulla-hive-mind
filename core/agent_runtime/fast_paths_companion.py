from __future__ import annotations

from typing import Any

from core.persistent_memory import (
    load_operator_dense_profile,
    search_session_summaries,
    search_user_heuristics,
)


def maybe_handle_companion_memory_fast_path(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    source_surface = str((source_context or {}).get("surface", "cli")).lower()
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    clean = " ".join(str(user_input or "").split()).strip()
    if not clean:
        return None
    lowered = clean.lower()
    profile = load_operator_dense_profile()
    if not profile:
        return None

    if looks_like_companion_continuation_request(lowered):
        response = render_companion_continuation_response(
            session_id=session_id,
            query_text=clean,
            profile=profile,
        )
        if response:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
                confidence=0.86,
                source_context=source_context,
                reason="companion_memory_continuation",
            )

    if looks_like_personalized_plan_request(lowered):
        response = render_personalized_plan_response(
            query_text=clean,
            profile=profile,
        )
        if response:
            return agent._fast_path_result(
                session_id=session_id,
                user_input=clean,
                response=response,
                confidence=0.83,
                source_context=source_context,
                reason="companion_memory_personalization",
            )
    return None


def looks_like_companion_continuation_request(lowered: str) -> bool:
    markers = (
        "where we left off",
        "where we left it",
        "pick up where",
        "you know the project",
        "continue from",
    )
    return sum(1 for marker in markers if marker in str(lowered or "")) >= 1


def looks_like_personalized_plan_request(lowered: str) -> bool:
    text = str(lowered or "")
    if "bot" not in text and "agent" not in text and "service" not in text:
        return False
    return any(marker in text for marker in ("sketch", "outline", "plan", "approach"))


def render_companion_continuation_response(
    *,
    session_id: str,
    query_text: str,
    profile: dict[str, Any],
) -> str:
    active_projects = [str(item).strip() for item in list(profile.get("active_projects") or []) if str(item).strip()]
    source_prefs = {str(item).strip().lower() for item in list(profile.get("source_preferences") or [])}
    preferred_stacks = [str(item).strip() for item in list(profile.get("preferred_stacks") or []) if str(item).strip()]
    topic_hints = [project.replace(" build", "").lower() for project in active_projects[:2]]
    query_seed = " ".join([query_text, *active_projects, *preferred_stacks]).strip()
    summaries = search_session_summaries(
        query_seed or query_text,
        topic_hints=topic_hints,
        limit=2,
        exclude_session_id=session_id,
    )
    summary_text = str((summaries[0] if summaries else {}).get("summary") or "").strip()
    project_label = active_projects[0] if active_projects else "current project"
    if project_label == "Telegram bot build":
        lead = "Continuing the Telegram bot build."
    elif project_label == "OpenClaw/NULLA runtime work":
        lead = "Continuing the OpenClaw/NULLA runtime work."
    else:
        lead = f"Continuing the {project_label.lower()}."
    preference_bits: list[str] = []
    if preferred_stacks:
        preference_bits.append(preferred_stacks[0].upper() if len(preferred_stacks[0]) <= 4 else preferred_stacks[0])
    if "official_docs_first" in source_prefs:
        preference_bits.append("official docs first")
    if "github_references" in source_prefs:
        preference_bits.append("strong GitHub references after the docs")
    middle = ""
    if preference_bits:
        middle = "Working memory says: " + ", ".join(preference_bits) + "."
    if not summary_text and not active_projects:
        return ""
    next_step = dense_memory_next_step(
        project_label=project_label,
        summary_text=summary_text,
        preferred_stack=preferred_stacks[0] if preferred_stacks else "",
    )
    parts = [lead]
    if middle:
        parts.append(middle)
    if summary_text:
        parts.append(f"Latest carried context: {summary_text[:220]}.")
    if next_step:
        parts.append(f"Next step: {next_step}")
    return " ".join(part.strip() for part in parts if part.strip())


def render_personalized_plan_response(*, query_text: str, profile: dict[str, Any]) -> str:
    del profile
    heuristics = search_user_heuristics(query_text, topic_hints=[], limit=6)
    source_prefs = {str(item.get("signal") or "").strip().lower() for item in heuristics if str(item.get("category") or "") == "source_preference"}
    stacks = [str(item.get("signal") or "").strip().lower() for item in heuristics if str(item.get("category") or "") == "preferred_stack"]
    style_signals = {str(item.get("signal") or "").strip().lower() for item in heuristics if str(item.get("category") or "") == "response_style"}
    if not source_prefs and not stacks and not style_signals:
        return ""
    lines: list[str] = []
    if "official_docs" in source_prefs:
        lines.append("Official docs first.")
    if stacks:
        lines.append(f"Use {stacks[0]} as the baseline stack.")
    if "github_repos" in source_prefs:
        lines.append("Pull 1-2 strong GitHub repos only after the docs, as implementation references.")
    lines.append("Build the smallest working bot loop, then test the core flow end to end.")
    if "concise_direct" not in style_signals and "brutal_honest" not in style_signals:
        return " ".join(lines)
    return "\n".join(lines[:4])


def dense_memory_next_step(*, project_label: str, summary_text: str, preferred_stack: str) -> str:
    lowered_project = str(project_label or "").lower()
    lowered_summary = str(summary_text or "").lower()
    stack = str(preferred_stack or "").strip().lower()
    if "telegram" in lowered_project or "telegram" in lowered_summary or "bot" in lowered_summary:
        stack_text = f"{stack} " if stack else ""
        return f"lock the {stack_text}bot skeleton, verify the command flow against the official docs, then run an end-to-end smoke."
    if "runtime" in lowered_project or "openclaw" in lowered_project or "nulla" in lowered_project:
        return "inspect the current failing runtime surface, verify it against live state, then patch and retest."
    if summary_text:
        return summary_text[:180]
    return ""
