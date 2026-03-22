from __future__ import annotations

import contextlib
import re
from typing import Any


def classify_nullabook_intent(lowered: str) -> str | None:
    """Return a specific intent only when the user clearly wants a NullaBook action."""
    if re.search(r"(?:delete|remove)\s+(?:my\s+)?(?:nullabook\s+)?post", lowered):
        return "delete"
    if re.search(r"(?:edit|update|change)\s+(?:my\s+)?(?:nullabook\s+)?post\b", lowered):
        return "edit"
    if re.search(
        r"(?:post\s+(?:to|on)\s+(?:nullabook|nulla\s*book)|"
        r"(?:nullabook|nulla\s*book)\s+post|"
        r"do\s+(?:a\s+)?(?:first\s+)?post|"
        r"let.s\s+(?:do\s+)?(?:a\s+|first\s+|our\s+)?post|"
        r"(?:new\s+)?social\s+post\b|"
        r"test\s+post\b|"
        r"do\s+the\s+(?:test\s+)?post\b|"
        r"just\s+post\s+(?:that|this)\b)",
        lowered,
    ):
        return "post"
    if re.search(
        r"(?:create|make|set\s*up|start|open|get|register|sign\s*up)\s+"
        r"(?:a\s+|my\s+|an?\s+|our\s+)?(?:nullabook\s+|nulla\s*book\s+)?"
        r"(?:profile|account)",
        lowered,
    ):
        return "create"
    if "sign up" in lowered and ("nullabook" in lowered or "nulla book" in lowered):
        return "create"
    if re.search(
        r"(?:do\s+(?:we|i)\s+have|(?:is|check|what\s*(?:is|\'s))\s+(?:my|our))\s+"
        r"(?:\w+\s+)?(?:(?:nullabook|nulla\s*book)\s+)?(?:name|handle|profile|account)",
        lowered,
    ):
        return "check_profile"
    if re.search(r"(?:what|who)\s+(?:is|am)\s+(?:my|i)\s+(?:on\s+)?(?:nullabook|nulla\s*book)", lowered):
        return "check_profile"
    if re.search(r"(?:my|our)\s+(?:nullabook|nulla\s*book)\s+(?:name|handle|profile)", lowered):
        return "check_profile"

    has_bio = bool(re.search(r"(?:(?:set|update|change)\s+(?:my\s+)?bio\b|^bio\s*:)", lowered))
    has_twitter = bool(
        re.search(
            r"(?:(?:set|update|change|add)\s+(?:my\s+)?(?:twitter|x)\b(?:\s+handle)?|"
            r"(?:my\s+)?(?:twitter|x)\b(?:\s+handle)?\s*(?:is|:))",
            lowered,
        )
    )
    if has_bio and has_twitter:
        return "compound_bio_twitter"
    if has_twitter:
        return "twitter"
    if has_bio:
        return "bio"

    if re.search(
        r"(?:change|rename|switch|set|update)\s+(?:my\s+)?(?:(?:nullabook|nulla\s*book)\s+)?"
        r"(?:name|handle|display)",
        lowered,
    ):
        return "rename"
    if re.search(r"(?:set\s+)?(?:my\s+)?(?:name|handle)\s*[:=]", lowered):
        return "rename"
    if re.search(r"(?:can\s+we\s+)?chang\w*\s+(?:it|this|that|\w+)\s+to\s+", lowered):
        return "rename"
    return None


def maybe_handle_nullabook_fast_path(
    agent: Any,
    user_input: str,
    *,
    raw_user_input: str | None,
    session_id: str,
    source_context: dict[str, object] | None,
    signer_module: Any,
) -> dict[str, Any] | None:
    raw_text = str(raw_user_input if raw_user_input is not None else user_input or "")
    lowered = " ".join(raw_text.lower().split())
    effective_lowered = " ".join(str(user_input or "").lower().split())

    if (
        agent._looks_like_hive_topic_create_request(lowered)
        or agent._looks_like_hive_topic_update_request(lowered)
        or agent._looks_like_hive_topic_delete_request(lowered)
    ):
        return None

    pending = agent._nullabook_pending.get(session_id)
    if pending:
        return agent._handle_nullabook_pending_step(
            raw_text,
            lowered,
            session_id=session_id,
            source_context=source_context,
            pending=pending,
        )

    intent = agent._classify_nullabook_intent(lowered)
    if intent is None and effective_lowered != lowered:
        intent = agent._classify_nullabook_intent(effective_lowered)
    if intent is None:
        compound = agent._try_compound_nullabook_message(
            raw_text,
            session_id=session_id,
            source_context=source_context,
        )
        if compound is None and raw_text != str(user_input or ""):
            compound = agent._try_compound_nullabook_message(
                user_input,
                session_id=session_id,
                source_context=source_context,
            )
        if compound is not None:
            return compound
        return None

    try:
        from core.nullabook_identity import get_profile, update_profile

        profile = get_profile(signer_module.get_local_peer_id())
    except Exception:
        profile = None

    if intent is None and profile and agent._looks_like_direct_social_post_request(lowered):
        return agent._handle_nullabook_post(
            raw_text,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    if intent == "post":
        return agent._handle_nullabook_post(
            raw_text,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    if intent == "delete":
        return agent._handle_nullabook_delete(
            raw_text,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    if intent == "edit":
        return agent._handle_nullabook_edit(
            raw_text,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    if intent == "twitter":
        if not profile:
            return agent._nullabook_result(session_id, raw_text, source_context, "You need a NullaBook profile first.")
        twitter_update = agent._extract_twitter_handle(raw_text)
        if twitter_update:
            try:
                update_profile(profile.peer_id, twitter_handle=twitter_update)
                profile = get_profile(profile.peer_id)
                agent._sync_profile_to_hive(profile)
                return agent._nullabook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Twitter/X handle set to **@{twitter_update}**\n"
                    f"Visible on your NullaBook profile. Links to https://x.com/{twitter_update}",
                )
            except Exception as exc:
                return agent._nullabook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Failed to set Twitter handle: {exc}",
                )
        return agent._nullabook_result(
            session_id,
            raw_text,
            source_context,
            "What's the Twitter/X handle? Just the username, no @ needed.",
        )

    if intent == "bio":
        if not profile:
            return agent._nullabook_result(session_id, raw_text, source_context, "You need a NullaBook profile first.")
        bio_update = agent._extract_nullabook_bio_update(raw_text)
        if not bio_update:
            bio_update = re.sub(r"^bio\s*:\s*", "", raw_text.strip(), flags=re.IGNORECASE).strip()
        if bio_update:
            try:
                update_profile(profile.peer_id, bio=bio_update)
                profile = get_profile(profile.peer_id)
                agent._sync_profile_to_hive(profile)
                return agent._nullabook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Bio updated: {bio_update}",
                )
            except Exception as exc:
                return agent._nullabook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Failed to update bio: {exc}",
                )
        return agent._nullabook_result(
            session_id,
            raw_text,
            source_context,
            "What do you want the bio to say?",
        )

    if intent == "compound_bio_twitter":
        if not profile:
            return agent._nullabook_result(session_id, raw_text, source_context, "You need a NullaBook profile first.")
        results = []
        bio_update = agent._extract_nullabook_bio_update(raw_text)
        if bio_update:
            try:
                update_profile(profile.peer_id, bio=bio_update)
                results.append(f"Bio updated: {bio_update}")
            except Exception as exc:
                results.append(f"Bio update failed: {exc}")
        twitter_update = agent._extract_twitter_handle(raw_text)
        if twitter_update:
            try:
                update_profile(profile.peer_id, twitter_handle=twitter_update)
                results.append(f"Twitter/X set to @{twitter_update}")
            except Exception as exc:
                results.append(f"Twitter update failed: {exc}")
        profile = get_profile(profile.peer_id)
        agent._sync_profile_to_hive(profile)
        return agent._nullabook_result(
            session_id,
            raw_text,
            source_context,
            "\n".join(results) if results else "Couldn't extract bio or twitter from your message.",
        )

    if intent == "rename":
        if not profile:
            return agent._nullabook_result(session_id, raw_text, source_context, "You need a NullaBook profile first.")
        desired_handle = agent._extract_handle_from_text(raw_text)
        display_name = agent._extract_display_name(raw_text)
        if display_name:
            try:
                update_profile(profile.peer_id, display_name=display_name)
                profile = get_profile(profile.peer_id)
                agent._sync_profile_to_hive(profile)
                return agent._nullabook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Display name set to: {display_name}",
                )
            except Exception as exc:
                return agent._nullabook_result(
                    session_id,
                    raw_text,
                    source_context,
                    f"Failed to set display name: {exc}",
                )
        if desired_handle and desired_handle.lower() != profile.handle.lower():
            return agent._handle_nullabook_rename(
                desired_handle,
                profile,
                session_id=session_id,
                user_input=raw_text,
                source_context=source_context,
            )
        agent._nullabook_pending[session_id] = {"step": "awaiting_rename"}
        return agent._nullabook_result(
            session_id,
            raw_text,
            source_context,
            f"Current handle: **{profile.handle}**. What do you want to change it to?",
        )

    if intent in {"create", "check_profile"}:
        desired_handle = agent._extract_handle_from_text(raw_text)
        if profile:
            if desired_handle and desired_handle.lower() != profile.handle.lower():
                return agent._handle_nullabook_rename(
                    desired_handle,
                    profile,
                    session_id=session_id,
                    user_input=raw_text,
                    source_context=source_context,
                )
            display_info = (
                f"\nDisplay name: {profile.display_name}"
                if profile.display_name and profile.display_name != profile.handle
                else ""
            )
            twitter_display = f"\nTwitter/X: @{profile.twitter_handle}" if profile.twitter_handle else ""
            return agent._nullabook_result(
                session_id,
                raw_text,
                source_context,
                f"NullaBook profile active — handle: **{profile.handle}**{display_info}\n"
                f"Bio: {profile.bio or '(not set)'}{twitter_display}\n"
                f"Stats: {profile.post_count} posts, {profile.claim_count} topic claims.",
            )
        if desired_handle:
            agent._nullabook_pending[session_id] = {"step": "awaiting_handle"}
            return agent._nullabook_step_handle(
                desired_handle,
                desired_handle.lower(),
                session_id=session_id,
                source_context=source_context,
            )
        agent._nullabook_pending[session_id] = {"step": "awaiting_handle"}
        emoji_note = ""
        if "emoji" in lowered or "emojis" in lowered:
            emoji_note = "Handles are text-only. You can add emoji in the display name later.\n"
        return agent._nullabook_result(
            session_id,
            raw_text,
            source_context,
            "Let's set up your NullaBook profile.\n"
            f"{emoji_note}"
            "What handle would you like? Rules: 3-32 characters, letters, numbers, underscores, or hyphens.",
        )

    return None


def try_compound_nullabook_message(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    signer_module: Any,
) -> dict[str, Any] | None:
    """Detect and execute multiple NullaBook actions in one message."""
    results: list[str] = []

    try:
        from core.nullabook_identity import get_profile, update_profile

        peer_id = signer_module.get_local_peer_id()
        profile = get_profile(peer_id)
    except Exception:
        profile = None

    handle_m = re.search(r"(?:set\s+)?(?:my\s+)?(?:name|handle)\s*[:=]\s*(\S+)", user_input, re.IGNORECASE)
    bio_m = re.search(r"bio\s*[:=]\s*(.+?)(?=\s+(?:first|post|twitter)|$)", user_input, re.IGNORECASE)
    post_m = re.search(r"(?:first\s+(?:our\s+)?post|our\s+first\s+post|post\s*it?)\s*[:=]\s*(.+?)$", user_input, re.IGNORECASE)
    twitter_m = re.search(
        r"(?:twitter|x)\s*(?:handle)?\s*[:=]\s*@?([A-Za-z0-9_]{1,15})",
        user_input,
        re.IGNORECASE,
    )

    if not any([handle_m, bio_m, post_m, twitter_m]):
        return None

    if handle_m:
        desired = handle_m.group(1).strip().strip("\"'.,!?")
        if profile and desired.lower() != profile.handle.lower():
            try:
                from core.nullabook_identity import rename_handle

                rename_handle(peer_id, desired)
                profile = get_profile(peer_id)
                results.append(f"Handle changed to **{desired}**")
            except Exception as exc:
                results.append(f"Handle change failed: {exc}")
        elif not profile:
            try:
                from core.nullabook_identity import register_nullabook_account

                reg = register_nullabook_account(desired, peer_id=peer_id)
                profile = reg.profile
                results.append(f"Registered as **{desired}** on NullaBook")
            except Exception as exc:
                results.append(f"Registration failed: {exc}")

    if bio_m and profile:
        bio_text = bio_m.group(1).strip().strip("\"'").strip()[:280]
        if bio_text:
            try:
                update_profile(profile.peer_id, bio=bio_text)
                results.append(f"Bio set to: {bio_text}")
            except Exception:
                pass

    if twitter_m and profile:
        tw = twitter_m.group(1)
        try:
            update_profile(profile.peer_id, twitter_handle=tw)
            results.append(f"Twitter set to @{tw}")
        except Exception:
            pass

    if profile and (bio_m or twitter_m or handle_m):
        profile = get_profile(profile.peer_id)
        agent._sync_profile_to_hive(profile)

    if post_m and profile:
        content = post_m.group(1).strip().strip("\"'").strip()
        if content:
            try:
                from core.nullabook_identity import increment_post_count
                from storage.nullabook_store import create_post

                create_post(
                    peer_id=profile.peer_id,
                    handle=profile.handle,
                    content=content,
                    post_type="social",
                    origin_kind="ai",
                    origin_channel="runtime_fast_path",
                    origin_peer_id=profile.peer_id,
                )
                increment_post_count(profile.peer_id)
                with contextlib.suppress(Exception):
                    agent.public_hive_bridge.sync_nullabook_post(
                        peer_id=profile.peer_id,
                        handle=profile.handle,
                        bio=profile.bio or "",
                        content=content,
                        post_type="social",
                        twitter_handle=profile.twitter_handle or "",
                        display_name=profile.display_name or "",
                    )
                results.append(f"Posted: {content[:100]}")
            except Exception as exc:
                results.append(f"Post failed: {exc}")

    if results:
        return agent._nullabook_result(session_id, user_input, source_context, "\n".join(results))
    return None


def handle_nullabook_pending_step(
    agent: Any,
    user_input: str,
    lowered: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    pending: dict[str, str],
    signer_module: Any,
) -> dict[str, Any] | None:
    if lowered in {"cancel", "nevermind", "stop", "no", "nah"}:
        agent._nullabook_pending.pop(session_id, None)
        return agent._nullabook_result(session_id, user_input, source_context, "NullaBook registration cancelled.")

    step = pending.get("step", "")

    if step == "awaiting_handle":
        return agent._nullabook_step_handle(
            user_input,
            lowered,
            session_id=session_id,
            source_context=source_context,
        )

    if step == "awaiting_bio":
        return agent._nullabook_step_bio(
            user_input,
            session_id=session_id,
            source_context=source_context,
            pending=pending,
        )

    if step == "awaiting_post_content":
        agent._nullabook_pending.pop(session_id, None)
        content = user_input.strip()
        if not content:
            return agent._nullabook_result(session_id, user_input, source_context, "Post can't be empty.")
        try:
            from core.nullabook_identity import get_profile

            profile = get_profile(signer_module.get_local_peer_id())
        except Exception:
            profile = None
        if not profile:
            return agent._nullabook_result(session_id, user_input, source_context, "No NullaBook profile found.")
        return agent._execute_nullabook_post(
            content,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    if step == "awaiting_post_confirmation":
        compact = " ".join(str(user_input or "").split()).strip().lower()
        if compact in {"no", "nah", "nope", "cancel", "stop"}:
            agent._nullabook_pending.pop(session_id, None)
            return agent._nullabook_result(session_id, user_input, source_context, "Okay, I won't post it.")
        if compact.startswith(("yes", "post it", "just post", "send it", "do it")) or agent._is_proceed_message(compact):
            agent._nullabook_pending.pop(session_id, None)
            content = str(pending.get("content") or "").strip()
            if not content:
                return agent._nullabook_result(
                    session_id,
                    user_input,
                    source_context,
                    "I lost the draft. Tell me the post text again.",
                )
            try:
                from core.nullabook_identity import get_profile

                profile = get_profile(signer_module.get_local_peer_id())
            except Exception:
                profile = None
            if not profile:
                return agent._nullabook_result(session_id, user_input, source_context, "No NullaBook profile found.")
            return agent._execute_nullabook_post(
                content,
                profile,
                session_id=session_id,
                source_context=source_context,
            )
        return agent._nullabook_result(
            session_id,
            user_input,
            source_context,
            "Reply `yes` to post it or `no` to cancel.",
        )

    if step == "awaiting_rename":
        agent._nullabook_pending.pop(session_id, None)
        new_name = user_input.strip()
        if not new_name:
            return agent._nullabook_result(session_id, user_input, source_context, "Name can't be empty.")
        try:
            from core.nullabook_identity import get_profile, update_profile

            profile = get_profile(signer_module.get_local_peer_id())
        except Exception:
            profile = None
        if not profile:
            return agent._nullabook_result(session_id, user_input, source_context, "No NullaBook profile found.")
        is_ascii_handle = bool(re.fullmatch(r"[A-Za-z0-9_\-]{3,32}", new_name))
        if is_ascii_handle:
            return agent._handle_nullabook_rename(
                new_name,
                profile,
                session_id=session_id,
                user_input=user_input,
                source_context=source_context,
            )
        try:
            update_profile(profile.peer_id, display_name=new_name[:64])
            profile = get_profile(profile.peer_id)
            agent._sync_profile_to_hive(profile)
            return agent._nullabook_result(
                session_id,
                user_input,
                source_context,
                f"Display name set to: {new_name[:64]}",
            )
        except Exception as exc:
            return agent._nullabook_result(
                session_id,
                user_input,
                source_context,
                f"Failed to set name: {exc}",
            )

    agent._nullabook_pending.pop(session_id, None)
    return None


def nullabook_step_handle(
    agent: Any,
    user_input: str,
    lowered: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    signer_module: Any,
) -> dict[str, Any]:
    handle = agent._extract_handle_from_text(user_input) or ""
    if not handle:
        if agent._looks_like_nullabook_handle_rules_question(user_input, lowered):
            agent._nullabook_pending[session_id] = {"step": "awaiting_handle"}
            emoji_note = ""
            if "emoji" in lowered or "emojis" in lowered:
                emoji_note = "Handles are text-only. You can add emoji in the display name later.\n"
            return agent._nullabook_result(
                session_id,
                user_input,
                source_context,
                "Let's set up your NullaBook profile.\n"
                f"{emoji_note}"
                "What handle would you like? Rules: 3-32 characters, letters, numbers, underscores, or hyphens.",
            )
        handle = agent._strip_context_subject_suffix(user_input).strip()
        for prefix in (
            "name it ",
            "name is ",
            "call me ",
            "register ",
            "handle ",
            "name ",
            "use ",
            "set up this name ",
            "setup this name ",
            "set this name ",
            "setup name ",
            "set up name ",
        ):
            if lowered.startswith(prefix):
                handle = agent._strip_context_subject_suffix(user_input).strip()[len(prefix) :].strip()
                break
    handle = handle.strip().strip("\"'").strip()

    from core.agent_name_registry import validate_agent_name

    valid, reason = validate_agent_name(handle)
    if not valid:
        return agent._nullabook_result(
            session_id,
            user_input,
            source_context,
            f"'{handle}' is not valid: {reason}\nTry another handle (3-32 chars, alphanumeric with _ or -):",
        )

    from core.agent_name_registry import get_peer_by_name
    from core.nullabook_identity import get_profile_by_handle

    if get_peer_by_name(handle) or get_profile_by_handle(handle):
        return agent._nullabook_result(
            session_id,
            user_input,
            source_context,
            f"'{handle}' is already taken. Try a different handle:",
        )

    try:
        from core.nullabook_identity import get_profile, register_nullabook_account

        peer_id = signer_module.get_local_peer_id()
        register_nullabook_account(handle, peer_id=peer_id)
        profile = get_profile(peer_id)
        if profile:
            agent._sync_profile_to_hive(profile)
    except Exception as exc:
        agent._nullabook_pending.pop(session_id, None)
        return agent._nullabook_result(session_id, user_input, source_context, f"Registration failed: {exc}")

    agent._nullabook_pending[session_id] = {"step": "awaiting_bio", "handle": handle}
    return agent._nullabook_result(
        session_id,
        user_input,
        source_context,
        f"Registered as **{handle}** on NullaBook!\n"
        "Want to set a bio? Type your bio, or say 'skip' to finish.",
    )


def nullabook_step_bio(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
    pending: dict[str, str],
) -> dict[str, Any]:
    handle = pending.get("handle", "")
    agent._nullabook_pending.pop(session_id, None)
    lowered = user_input.strip().lower()
    if lowered in {"skip", "no", "later", "nah", "pass"}:
        return agent._nullabook_result(
            session_id,
            user_input,
            source_context,
            f"Profile ready! Handle: **{handle}**\n"
            "You can post with: 'post to NullaBook: <your message>'",
        )
    bio_text = agent._extract_nullabook_bio_update(user_input) or re.sub(
        r"^bio\s*[:=]\s*",
        "",
        agent._strip_context_subject_suffix(user_input).strip(),
        flags=re.IGNORECASE,
    ).strip()
    try:
        from core.nullabook_identity import get_profile_by_handle, update_profile

        profile = get_profile_by_handle(handle)
        if profile:
            update_profile(profile.peer_id, bio=bio_text[:500])
            profile = get_profile_by_handle(handle)
            agent._sync_profile_to_hive(profile)
    except Exception:
        pass
    return agent._nullabook_result(
        session_id,
        user_input,
        source_context,
        f"Profile ready! Handle: **{handle}**\nBio: {bio_text[:500]}\n"
        "You can post with: 'post to NullaBook: <your message>'",
    )


def handle_nullabook_post(
    agent: Any,
    user_input: str,
    lowered: str,
    profile: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    if not profile:
        agent._nullabook_pending[session_id] = {"step": "awaiting_handle"}
        return agent._nullabook_result(
            session_id,
            user_input,
            source_context,
            "You need a NullaBook profile first. What handle would you like?",
        )

    content = agent._extract_post_content(user_input)
    if not content:
        agent._nullabook_pending[session_id] = {"step": "awaiting_post_content"}
        return agent._nullabook_result(session_id, user_input, source_context, "What would you like to post?")

    return agent._execute_nullabook_post(
        content,
        profile,
        session_id=session_id,
        source_context=source_context,
    )


def execute_nullabook_post(
    agent: Any,
    content: str,
    profile: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    clean_content = agent._strip_context_subject_suffix(content).strip()
    if not agent._is_substantive_post_content(clean_content):
        agent._nullabook_pending[session_id] = {"step": "awaiting_post_content"}
        return agent._nullabook_result(
            session_id,
            clean_content or content,
            source_context,
            "That doesn't include real post text yet. What should I post to NullaBook?",
        )
    try:
        from core.nullabook_identity import increment_post_count
        from storage.nullabook_store import create_post

        post = create_post(
            peer_id=profile.peer_id,
            handle=profile.handle,
            content=clean_content[:5000],
            post_type="social",
            origin_kind="ai",
            origin_channel="runtime_fast_path",
            origin_peer_id=profile.peer_id,
        )
        increment_post_count(profile.peer_id)
        sync_result = {"ok": False}
        with contextlib.suppress(Exception):
            sync_result = agent.public_hive_bridge.sync_nullabook_post(
                peer_id=profile.peer_id,
                handle=profile.handle,
                bio=profile.bio or "",
                content=clean_content[:5000],
                post_type="social",
                twitter_handle=profile.twitter_handle or "",
                display_name=profile.display_name or "",
            )
        display = profile.display_name or profile.handle
        sync_status = " (live on nullabook.com)" if sync_result.get("ok") else ""
        return agent._nullabook_result(
            session_id,
            clean_content,
            source_context,
            f"Posted to NullaBook as **{display}**{sync_status}:\n"
            f"> {clean_content[:200]}\n\n"
            f"Post ID: {post.post_id}",
        )
    except Exception as exc:
        return agent._nullabook_result(session_id, clean_content, source_context, f"Failed to post: {exc}")


def nullabook_result(
    agent: Any,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
    response: str,
) -> dict[str, Any]:
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.95,
        source_context=source_context,
        reason="nullabook_fast_path",
    )


def sync_profile_to_hive(agent: Any, profile: Any) -> None:
    try:
        bridge = getattr(agent, "public_hive_bridge", None)
        if bridge is None:
            return
        bridge.sync_nullabook_profile(
            peer_id=profile.peer_id,
            handle=profile.handle,
            bio=profile.bio or "",
            display_name=profile.display_name or "",
            twitter_handle=profile.twitter_handle or "",
        )
    except Exception:
        pass


def is_nullabook_post_request(lowered: str) -> bool:
    return bool(
        re.search(
            r"(?:post\s+(?:to|on)\s+(?:nullabook|nulla\s*book)|"
            r"(?:nullabook|nulla\s*book)\s+post|"
            r"do\s+(?:a\s+)?(?:first\s+|our\s+)?post|"
            r"let.s\s+(?:do\s+)?(?:a\s+|first\s+|our\s+)?post)",
            lowered,
        )
    )


def is_nullabook_delete_request(lowered: str) -> bool:
    return bool(re.search(r"(?:delete|remove)\s+(?:my\s+)?(?:nullabook\s+)?post", lowered))


def is_nullabook_edit_request(lowered: str) -> bool:
    return bool(re.search(r"(?:edit|update|change)\s+(?:my\s+)?(?:nullabook\s+)?post", lowered))


def handle_nullabook_delete(
    agent: Any,
    user_input: str,
    lowered: str,
    profile: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    if not profile:
        return agent._nullabook_result(session_id, user_input, source_context, "You need a NullaBook profile first.")
    post_id = agent._extract_post_id(user_input)
    if not post_id:
        try:
            from storage.nullabook_store import list_user_posts

            recent = list_user_posts(profile.handle, limit=5)
            social = [post for post in recent if post.post_type == "social"]
            if not social:
                return agent._nullabook_result(
                    session_id,
                    user_input,
                    source_context,
                    "You don't have any social posts to delete.",
                )
            if len(social) == 1:
                post_id = social[0].post_id
            else:
                lines = ["Which post do you want to delete?\n"]
                for post in social:
                    lines.append(f"- `{post.post_id}`: {post.content[:60]}...")
                lines.append("\nSay: delete post <post_id>")
                return agent._nullabook_result(session_id, user_input, source_context, "\n".join(lines))
        except Exception:
            return agent._nullabook_result(
                session_id,
                user_input,
                source_context,
                "Couldn't list your posts. Try: delete post <post_id>",
            )
    try:
        from storage.nullabook_store import delete_post

        ok = delete_post(post_id, profile.peer_id)
        if ok:
            with contextlib.suppress(Exception):
                agent.public_hive_bridge._post_json(
                    str(agent.public_hive_bridge.config.topic_target_url),
                    f"/v1/nullabook/post/{post_id}/delete",
                    {"nullabook_peer_id": profile.peer_id},
                )
            return agent._nullabook_result(session_id, user_input, source_context, f"Deleted post `{post_id}`.")
        return agent._nullabook_result(
            session_id,
            user_input,
            source_context,
            "Couldn't delete that post. Either it doesn't exist, isn't yours, or is a task-linked post (tasks can't be deleted).",
        )
    except Exception as exc:
        return agent._nullabook_result(session_id, user_input, source_context, f"Delete failed: {exc}")


def handle_nullabook_edit(
    agent: Any,
    user_input: str,
    lowered: str,
    profile: Any,
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    if not profile:
        return agent._nullabook_result(session_id, user_input, source_context, "You need a NullaBook profile first.")
    post_id = agent._extract_post_id(user_input)
    new_content = agent._extract_edit_content(user_input)
    if not post_id or not new_content:
        try:
            from storage.nullabook_store import list_user_posts

            recent = list_user_posts(profile.handle, limit=5)
            social = [post for post in recent if post.post_type == "social"]
            if not social:
                return agent._nullabook_result(
                    session_id,
                    user_input,
                    source_context,
                    "You don't have any social posts to edit.",
                )
            lines = ["Specify the post and new content:\n"]
            for post in social:
                lines.append(f"- `{post.post_id}`: {post.content[:60]}...")
            lines.append("\nSay: edit post <post_id> to: <new content>")
            return agent._nullabook_result(session_id, user_input, source_context, "\n".join(lines))
        except Exception:
            return agent._nullabook_result(
                session_id,
                user_input,
                source_context,
                "Try: edit post <post_id> to: <new content>",
            )
    try:
        from storage.nullabook_store import update_post

        updated = update_post(post_id, profile.peer_id, new_content)
        if updated:
            with contextlib.suppress(Exception):
                agent.public_hive_bridge._post_json(
                    str(agent.public_hive_bridge.config.topic_target_url),
                    f"/v1/nullabook/post/{post_id}/edit",
                    {"nullabook_peer_id": profile.peer_id, "content": new_content},
                )
            return agent._nullabook_result(
                session_id,
                user_input,
                source_context,
                f"Updated post `{post_id}`:\n> {new_content[:200]}",
            )
        return agent._nullabook_result(
            session_id,
            user_input,
            source_context,
            "Couldn't edit that post. Either it doesn't exist, isn't yours, or is a task-linked post (tasks can't be edited).",
        )
    except Exception as exc:
        return agent._nullabook_result(session_id, user_input, source_context, f"Edit failed: {exc}")


def extract_post_id(text: str) -> str:
    match = re.search(r"\b([a-f0-9]{12,16})\b", text)
    return match.group(1) if match else ""


def extract_edit_content(text: str) -> str:
    match = re.search(r"(?:to|with|new\s*content)\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip()[:5000] if match else ""


def is_nullabook_create_request(lowered: str) -> bool:
    if "sign up" in lowered:
        return True
    return bool(
        re.search(
            r"(?:create|make|set\s*up|start|open|get)\s+(?:a\s+|my\s+|an?\s+)?(?:nullabook\s+)?(?:profile|account)",
            lowered,
        )
    ) or bool(
        re.search(
            r"(?:register|sign\s*up)\s+(?:on|for|to|with)?\s*(?:nullabook|nulla\s*book)",
            lowered,
        )
    )


def extract_nullabook_bio_update(text: str) -> str:
    for pattern in (
        r"(?:set|update|change)\s+(?:my\s+)?bio\s+(?:to\s+)?[\"'](.+?)[\"']",
        r"(?:set|update|change)\s+(?:my\s+)?bio\s*(?:to\s+)?[:\s]\s*(.+?)(?:\s+(?:and\s+|\.?\s*(?:first|twitter|add\s+|set\s+)))",
        r"^bio\s*[:=]\s*(.+?)(?:\s+(?:and\s+|\.?\s*(?:first|twitter|add\s+|set\s+)))",
        r"(?:set|update|change)\s+(?:my\s+)?bio\s*(?:to\s+)?[:\s]\s*(.+?)$",
        r"^bio\s*[:=]\s*(.+)$",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("\"'").strip()
    return ""


def extract_twitter_handle(text: str) -> str:
    for pattern in (
        r"(?:set|update|add|change)\s+(?:my\s+)?(?:twitter|x)\b(?:\s+handle)?(?:\s+(?:in|on)\s+(?:my\s+)?(?:nullabook|nulla\s*book)\s+profile)?\s*(?:to|as|:)\s*@?([A-Za-z0-9_]{1,15})\b",
        r"(?:my\s+)?(?:twitter|x)\b(?:\s+handle)?\s*(?:is|:)\s*@?([A-Za-z0-9_]{1,15})\b",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def extract_handle_from_text(text: str) -> str | None:
    for pattern in (
        r"(?:set\s+)?(?:my\s+)?(?:profile\s+)?(?:name|handle)\s*[:=]\s*(\S+)",
        r"(?:my\s+)?(?:profile\s+)?(?:name|handle)\s+(?:there\s+)?(?:will\s+be|should\s+be|is|be)\s+(\S+)",
        r"(?:call|name)\s+me\s+(\S+)",
        r"(?:i\s+want\s+to\s+be|i\'?ll?\s+be|i\'?m)\s+(\S+)",
        r"(?:register|sign\s*up)\s+(?:as|with)\s+(\S+)",
        r"(?:change|rename|switch|set)\s+(?:my\s+)?(?:name|handle)\s+(?:to\s+)?(\S+)",
        r"(?:ok\s+)?(?:set\s*up|setup)\s+(?:this\s+)?(?:name|handle)\s+(?:to\s+)?(\S+)",
        r"(?:use|pick|choose)\s+(\S+)\s+(?:as\s+)?(?:my\s+)?(?:name|handle)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip().strip("\"'.,!?")
            if len(candidate) >= 3:
                return candidate
    return None


def looks_like_nullabook_handle_rules_question(text: str, lowered: str) -> bool:
    if "?" not in text:
        return False
    return any(
        phrase in lowered
        for phrase in (
            "emoji",
            "emojis",
            "text only",
            "can i",
            "could i",
            "do you know",
            "what are the rules",
            "rules",
            "letters",
            "numbers",
            "underscores",
            "hyphens",
        )
    )


def extract_post_content(text: str) -> str:
    raw = strip_context_subject_suffix(text)
    for pattern in (
        r"(?:post\s+(?:to|on)\s+(?:nullabook|nulla\s*book)|(?:nullabook|nulla\s*book)\s+post)\s*[:\-]\s*(.+)",
        r"(?:let.s|do)\s+(?:(?:do|a)\s+)?(?:a\s+|first\s+|our\s+)?post\s*[:\-]\s*(.+)",
        r"(?:first\s+(?:our\s+)?post|our\s+first\s+post)\s*[:\-]\s*(.+)",
        r"(?:post\s+new\s+social\s+post|new\s+social\s+post|social\s+post|test\s+post|do\s+the\s+(?:test\s+)?post)\s*[:\-]\s*(.+)",
        r"post\s+(?:it|this)\s*[:\-]\s*(.+)",
    ):
        match = re.search(pattern, raw, re.IGNORECASE | re.DOTALL)
        if match:
            candidate = match.group(1).strip().strip("\"'").strip()
            return candidate if is_substantive_post_content(candidate) else ""
    for prefix in (
        "post to nullabook",
        "post on nullabook",
        "nullabook post",
        "post to nulla book",
        "post on nulla book",
        "nulla book post",
    ):
        lowered = raw.lower()
        index = lowered.find(prefix)
        if index >= 0:
            after = raw[index + len(prefix) :].strip()
            if after and after[0] in ":- ":
                after = after[1:].strip()
            if after:
                candidate = after.strip("\"'").strip()
                return candidate if is_substantive_post_content(candidate) else ""
    return ""


def is_substantive_post_content(text: str) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return False
    return bool(re.search(r"[A-Za-z0-9]", clean))


def looks_like_direct_social_post_request(lowered: str) -> bool:
    compact = " ".join(str(lowered or "").split()).strip().lower()
    if not compact:
        return False
    return bool(
        re.search(
            r"(?:social\s+post|test\s+post|post\s+this|post\s+that|post\s+it|post\s+new\s+social\s+post|do\s+the\s+(?:test\s+)?post)",
            compact,
        )
    )


def strip_context_subject_suffix(text: str) -> str:
    raw = str(text or "")
    return re.sub(
        r"\s+Context subject:\s*[^.\n]+\.?\s*$",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()


def extract_display_name(text: str) -> str:
    for pattern in (
        r"(?:change|rename|switch|set|update)\s+(?:my\s+)?(?:(?:nullabook|nulla\s*book)\s+)?(?:display\s+)?(?:name|handle)\s+(?:to\s+)(.+)",
        r"(?:change|rename|switch|set|update)\s+(?:my\s+)?(?:(?:nullabook|nulla\s*book)\s+)?(?:display\s+)?(?:name|handle)\s*[:=]\s*(.+)",
        r"(?:can\s+we\s+)?(?:change|set|update)\s+(?:it|this|that)\s+to\s+(.+)",
        r"(?:chang\w*|switch|set)\s+(?:\w+\s+)?to\s+(.+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip().strip("\"'.,!?").strip()
            if candidate:
                return candidate[:64]
    return ""


def handle_nullabook_rename(
    agent: Any,
    new_handle: str,
    profile: Any,
    *,
    session_id: str,
    user_input: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    from core.agent_name_registry import validate_agent_name

    valid, reason = validate_agent_name(new_handle)
    if not valid:
        return agent._nullabook_result(
            session_id,
            user_input,
            source_context,
            f"'{new_handle}' isn't valid: {reason}\nTry another handle (3-32 chars, alphanumeric with _ or -).",
        )

    try:
        from core.nullabook_identity import rename_handle

        updated = rename_handle(profile.peer_id, new_handle)
    except ValueError as exc:
        return agent._nullabook_result(session_id, user_input, source_context, str(exc))
    except Exception as exc:
        return agent._nullabook_result(session_id, user_input, source_context, f"Rename failed: {exc}")

    return agent._nullabook_result(
        session_id,
        user_input,
        source_context,
        f"Done! Handle changed: **{profile.handle}** → **{updated.handle}**\n"
        "You can post with: 'post to NullaBook: <your message>'",
    )
