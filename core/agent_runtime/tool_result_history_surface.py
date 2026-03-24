from __future__ import annotations

from typing import Any

from core.agent_runtime import orchestrator as agent_orchestrator_runtime
from core.agent_runtime import response_policy as agent_response_policy


class ToolResultHistorySurfaceMixin:
    def _append_tool_result_to_source_context(
        self,
        source_context: dict[str, Any] | None,
        *,
        execution: Any,
        tool_name: str,
    ) -> dict[str, Any]:
        return agent_response_policy.append_tool_result_to_source_context(
            self,
            source_context,
            execution=execution,
            tool_name=tool_name,
        )

    def _normalize_tool_history_message(self, item: dict[str, Any]) -> dict[str, str]:
        return agent_response_policy.normalize_tool_history_message(self, item)

    def _tool_surface_for_history(self, tool_name: str) -> str:
        return agent_response_policy.tool_surface_for_history(tool_name)

    def _tool_history_observation_payload(
        self,
        *,
        execution: Any,
        tool_name: str,
    ) -> dict[str, Any]:
        return agent_response_policy.tool_history_observation_payload(
            execution=execution,
            tool_name=tool_name,
        )

    def _tool_history_observation_prompt(self, observation: dict[str, Any]) -> str:
        return agent_orchestrator_runtime.tool_history_observation_prompt(observation)

    def _tool_history_observation_message(
        self,
        *,
        execution: Any,
        tool_name: str,
    ) -> dict[str, str]:
        return agent_response_policy.tool_history_observation_message(
            self,
            execution=execution,
            tool_name=tool_name,
        )
