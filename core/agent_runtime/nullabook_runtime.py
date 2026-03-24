from __future__ import annotations

from typing import Any

from core.agent_runtime import nullabook as agent_nullabook_runtime
from network import signer as signer_mod


class NullaBookRuntimeMixin:
    _nullabook_pending: dict[str, dict[str, str]]

    @staticmethod
    def _classify_nullabook_intent(lowered: str) -> str | None:
        return agent_nullabook_runtime.classify_nullabook_intent(lowered)

    def _maybe_handle_nullabook_fast_path(
        self,
        user_input: str,
        *,
        raw_user_input: str | None = None,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_nullabook_runtime.maybe_handle_nullabook_fast_path(
            self,
            user_input,
            raw_user_input=raw_user_input,
            session_id=session_id,
            source_context=source_context,
            signer_module=signer_mod,
        )

    def _try_compound_nullabook_message(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_nullabook_runtime.try_compound_nullabook_message(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            signer_module=signer_mod,
        )

    def _handle_nullabook_pending_step(
        self,
        user_input: str,
        lowered: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        pending: dict[str, str],
    ) -> dict[str, Any] | None:
        return agent_nullabook_runtime.handle_nullabook_pending_step(
            self,
            user_input,
            lowered,
            session_id=session_id,
            source_context=source_context,
            pending=pending,
            signer_module=signer_mod,
        )

    def _nullabook_step_handle(
        self,
        user_input: str,
        lowered: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.nullabook_step_handle(
            self,
            user_input,
            lowered,
            session_id=session_id,
            source_context=source_context,
            signer_module=signer_mod,
        )

    def _nullabook_step_bio(
        self,
        user_input: str,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
        pending: dict[str, str],
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.nullabook_step_bio(
            self,
            user_input,
            session_id=session_id,
            source_context=source_context,
            pending=pending,
        )

    def _handle_nullabook_post(
        self,
        user_input: str,
        lowered: str,
        profile: Any,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.handle_nullabook_post(
            self,
            user_input,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    def _execute_nullabook_post(
        self,
        content: str,
        profile: Any,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.execute_nullabook_post(
            self,
            content,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    def _nullabook_result(
        self,
        session_id: str,
        user_input: str,
        source_context: dict[str, object] | None,
        response: str,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.nullabook_result(
            self,
            session_id,
            user_input,
            source_context,
            response,
        )

    def _sync_profile_to_hive(self, profile: Any) -> None:
        agent_nullabook_runtime.sync_profile_to_hive(self, profile)

    @staticmethod
    def _is_nullabook_post_request(lowered: str) -> bool:
        return agent_nullabook_runtime.is_nullabook_post_request(lowered)

    @staticmethod
    def _is_nullabook_delete_request(lowered: str) -> bool:
        return agent_nullabook_runtime.is_nullabook_delete_request(lowered)

    @staticmethod
    def _is_nullabook_edit_request(lowered: str) -> bool:
        return agent_nullabook_runtime.is_nullabook_edit_request(lowered)

    def _handle_nullabook_delete(
        self,
        user_input: str,
        lowered: str,
        profile: Any,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.handle_nullabook_delete(
            self,
            user_input,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    def _handle_nullabook_edit(
        self,
        user_input: str,
        lowered: str,
        profile: Any,
        *,
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.handle_nullabook_edit(
            self,
            user_input,
            lowered,
            profile,
            session_id=session_id,
            source_context=source_context,
        )

    @staticmethod
    def _extract_post_id(text: str) -> str:
        return agent_nullabook_runtime.extract_post_id(text)

    @staticmethod
    def _extract_edit_content(text: str) -> str:
        return agent_nullabook_runtime.extract_edit_content(text)

    @staticmethod
    def _is_nullabook_create_request(lowered: str) -> bool:
        return agent_nullabook_runtime.is_nullabook_create_request(lowered)

    @staticmethod
    def _extract_nullabook_bio_update(text: str) -> str:
        return agent_nullabook_runtime.extract_nullabook_bio_update(text)

    @staticmethod
    def _extract_twitter_handle(text: str) -> str:
        return agent_nullabook_runtime.extract_twitter_handle(text)

    @staticmethod
    def _extract_handle_from_text(text: str) -> str | None:
        return agent_nullabook_runtime.extract_handle_from_text(text)

    @staticmethod
    def _looks_like_nullabook_handle_rules_question(text: str, lowered: str) -> bool:
        return agent_nullabook_runtime.looks_like_nullabook_handle_rules_question(text, lowered)

    @staticmethod
    def _extract_post_content(text: str) -> str:
        return agent_nullabook_runtime.extract_post_content(text)

    @staticmethod
    def _is_substantive_post_content(text: str) -> bool:
        return agent_nullabook_runtime.is_substantive_post_content(text)

    @staticmethod
    def _looks_like_direct_social_post_request(lowered: str) -> bool:
        return agent_nullabook_runtime.looks_like_direct_social_post_request(lowered)

    @staticmethod
    def _strip_context_subject_suffix(text: str) -> str:
        return agent_nullabook_runtime.strip_context_subject_suffix(text)

    @staticmethod
    def _extract_display_name(text: str) -> str:
        return agent_nullabook_runtime.extract_display_name(text)

    def _handle_nullabook_rename(
        self,
        new_handle: str,
        profile: Any,
        *,
        session_id: str,
        user_input: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_nullabook_runtime.handle_nullabook_rename(
            self,
            new_handle,
            profile,
            session_id=session_id,
            user_input=user_input,
            source_context=source_context,
        )
