from __future__ import annotations

from unittest import mock

from core.persistent_memory import (
    append_conversation_event,
    conversation_log_path,
    describe_session_memory_policy,
    load_operator_dense_profile,
    maybe_handle_memory_command,
    memory_entries_path,
    memory_path,
    operator_dense_profile_path,
    search_relevant_memory,
    search_session_summaries,
    search_user_heuristics,
    session_memory_policy,
    session_summaries_path,
    summarize_memory,
    user_heuristics_path,
)
from core.runtime_paths import data_path
from core.user_preferences import extract_requested_agent_name, load_preferences, maybe_handle_preference_command
from storage.db import get_connection


def setup_function() -> None:
    for path in (
        memory_path(),
        conversation_log_path(),
        memory_entries_path(),
        session_summaries_path(),
        user_heuristics_path(),
        operator_dense_profile_path(),
    ):
        if path.exists():
            path.unlink()
    prefs_path = data_path("user_preferences.json")
    if prefs_path.exists():
        prefs_path.unlink()
    conn = get_connection()
    try:
        for table in ("session_memory_policies", "learning_shards", "local_tasks", "knowledge_holders", "knowledge_manifests"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()


def test_memory_remember_and_forget_commands_roundtrip() -> None:
    handled, _ = maybe_handle_memory_command("remember that the owner prefers concise answers")
    assert handled is True
    remembered = "\n".join(summarize_memory(limit=20)).lower()
    assert "prefers concise answers" in remembered

    handled, response = maybe_handle_memory_command("forget concise answers")
    assert handled is True
    assert "removed" in response.lower()
    remembered_after = "\n".join(summarize_memory(limit=20)).lower()
    assert "prefers concise answers" not in remembered_after


def test_preference_commands_persist() -> None:
    handled, _ = maybe_handle_preference_command("set humor 90%")
    assert handled is True
    handled, _ = maybe_handle_preference_command("act like Cornholio")
    assert handled is True

    prefs = load_preferences()
    assert prefs.humor_percent == 90
    assert prefs.character_mode.lower() == "cornholio"


def test_autonomy_and_workflow_preferences_persist() -> None:
    handled, response = maybe_handle_preference_command("don't ask for micro step approval")
    assert handled is True
    assert "hands-off" in response.lower()

    handled, response = maybe_handle_preference_command("hide workflow")
    assert handled is True
    assert "disabled" in response.lower()

    prefs = load_preferences()
    assert prefs.autonomy_mode == "hands_off"
    assert prefs.show_workflow is False


def test_workflow_default_is_hidden() -> None:
    prefs = load_preferences()
    assert prefs.show_workflow is False


def test_natural_language_hide_workflow_command_persists() -> None:
    handled, response = maybe_handle_preference_command(
        "Do not show me this workflow, keep it to yourself."
    )
    assert handled is True
    assert "disabled" in response.lower()
    assert load_preferences().show_workflow is False


def test_hive_followup_preferences_persist() -> None:
    handled, response = maybe_handle_preference_command("disable hive followups")
    assert handled is True
    assert "disabled" in response.lower()

    handled, response = maybe_handle_preference_command("don't help with research when idle")
    assert handled is True
    assert "disabled" in response.lower()

    prefs = load_preferences()
    assert prefs.hive_followups is False
    assert prefs.idle_research_assist is False


def test_hive_task_intake_preferences_persist() -> None:
    handled, response = maybe_handle_preference_command("stay visible but don't take tasks")
    assert handled is True
    assert "stay visible" in response.lower()

    prefs = load_preferences()
    assert prefs.accept_hive_tasks is False

    handled, response = maybe_handle_preference_command("accept hive tasks")
    assert handled is True
    assert "enabled" in response.lower()

    prefs = load_preferences()
    assert prefs.accept_hive_tasks is True


def test_social_commons_preferences_persist() -> None:
    handled, response = maybe_handle_preference_command("disable agent commons")
    assert handled is True
    assert "disabled" in response.lower()

    prefs = load_preferences()
    assert prefs.social_commons is False

    handled, response = maybe_handle_preference_command("enable agent commons")
    assert handled is True
    assert "enabled" in response.lower()

    prefs = load_preferences()
    assert prefs.social_commons is True


def test_extract_requested_agent_name_handles_natural_language_rename() -> None:
    assert extract_requested_agent_name("I renaming you to Cornholio, and my name is SLS") == "Cornholio"
    assert extract_requested_agent_name("rename yourself to Cornholio") == "Cornholio"
    assert extract_requested_agent_name("your name is Cornholio now") == "Cornholio"
    assert extract_requested_agent_name("you are acting weird but create hello world file and save it as .txt in Marchtest folder") is None


def test_rename_command_respects_owner_authority() -> None:
    with mock.patch("core.onboarding.force_rename") as force_rename, mock.patch(
        "core.identity_manager.update_local_persona"
    ) as update_persona:
        handled, response = maybe_handle_preference_command("I am renaming you to Cornholio")

    assert handled is True
    assert "Cornholio" in response
    force_rename.assert_called_once_with("Cornholio")
    update_persona.assert_called_once_with("default", display_name="Cornholio")


def test_auto_memory_extraction_persists_names_and_style_preferences() -> None:
    append_conversation_event(
        session_id="openclaw:test-user",
        user_input="My name is Operator. Keep answers concise and brutally honest.",
        assistant_output="Noted.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )

    remembered = "\n".join(summarize_memory(limit=20)).lower()
    assert "operator name is operator" in remembered
    assert "keep answers concise and brutally honest" in remembered

    hits = search_relevant_memory("what style should you use", topic_hints=["concise", "honest"], limit=4)
    assert any("concise" in str(hit.get("text") or "").lower() for hit in hits)


def test_user_heuristics_capture_stack_sources_and_autonomy_preferences() -> None:
    append_conversation_event(
        session_id="openclaw:heuristics",
        user_input=(
            "I am building Telegram bots in Python. Use official docs and good GitHub repos first. "
            "Don't ask for micro approval, just do the work and test all."
        ),
        assistant_output="Noted.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )

    heuristics = search_user_heuristics(
        "build a telegram bot",
        topic_hints=["telegram bot", "github"],
        limit=6,
    )
    categories = {(str(item.get("category") or ""), str(item.get("signal") or "")) for item in heuristics}

    assert ("preferred_stack", "python") in categories
    assert ("source_preference", "official_docs") in categories
    assert ("source_preference", "github_repos") in categories
    assert ("autonomy_preference", "hands_off") in categories


def test_dense_operator_profile_stays_local_and_tracks_project_focus() -> None:
    append_conversation_event(
        session_id="openclaw:dense-profile",
        user_input=(
            "I am building a Telegram bot in Python. Use official docs and GitHub repos first. "
            "Keep answers concise and brutally honest."
        ),
        assistant_output="Stored.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )

    profile = load_operator_dense_profile()

    assert profile["share_scope"] == "local_only"
    assert "LOCAL_ONLY" in list(profile.get("policy_tags") or [])
    assert "Telegram bot build" in list(profile.get("active_projects") or [])
    assert "official_docs_first" in list(profile.get("source_preferences") or [])
    assert "python" in list(profile.get("preferred_stacks") or [])
    assert "concise_direct" in list(profile.get("response_style") or [])
    assert "telegram bot build" in str(profile.get("dense_summary") or "").lower()


def test_new_name_replaces_old_name_memory() -> None:
    append_conversation_event(
        session_id="openclaw:test-user",
        user_input="My name is Operator.",
        assistant_output="Noted.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )
    append_conversation_event(
        session_id="openclaw:test-user",
        user_input="Call me SL now.",
        assistant_output="Understood.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )

    remembered = "\n".join(summarize_memory(limit=20)).lower()
    assert "operator name is operator" not in remembered
    assert "operator name is sl" in remembered


def test_session_scope_command_defaults_to_local_and_can_switch_to_hive() -> None:
    session_id = "openclaw:privacy-check"
    assert "PRIVATE VAULT" in describe_session_memory_policy(session_id)

    handled, response = maybe_handle_memory_command(
        "shared pack except my real name and address",
        session_id=session_id,
    )
    assert handled is True
    assert "SHARED PACK" in response

    policy = session_memory_policy(session_id)
    assert policy["share_scope"] == "hive_mind"
    assert policy["realm_label"] == "SHARED PACK"
    assert "my real name" in policy["restricted_terms"]
    assert "address" in policy["restricted_terms"]


def test_session_summaries_become_searchable_for_new_sessions() -> None:
    append_conversation_event(
        session_id="openclaw:session-a",
        user_input="We are debugging the telegram installer and OpenClaw continuity issue.",
        assistant_output="I'll inspect the installer and persistence path.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )
    append_conversation_event(
        session_id="openclaw:session-a",
        user_input="The problem is that a fresh session forgets prior continuity.",
        assistant_output="I will preserve the conversation and add session summaries.",
        source_context={"surface": "channel", "platform": "openclaw"},
    )

    summaries = search_session_summaries(
        "telegram installer continuity",
        topic_hints=["openclaw", "installer"],
        limit=3,
        exclude_session_id="openclaw:new-session",
    )
    assert summaries
    assert any("continuity" in str(item.get("summary") or "").lower() for item in summaries)


def test_public_commons_alias_maps_to_public_knowledge() -> None:
    session_id = "openclaw:public-commons"
    handled, response = maybe_handle_memory_command("public commons", session_id=session_id)
    assert handled is True
    assert "HIVE/PUBLIC COMMONS" in response
    policy = session_memory_policy(session_id)
    assert policy["share_scope"] == "public_knowledge"
    assert policy["realm_label"] == "HIVE/PUBLIC COMMONS"


def test_hive_mind_task_question_is_not_treated_as_memory_scope_command() -> None:
    handled, response = maybe_handle_memory_command(
        "what are the tasks available for Hive mind?",
        session_id="openclaw:hive-query",
    )
    assert handled is False
    assert response == ""
