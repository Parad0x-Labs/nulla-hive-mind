from __future__ import annotations

from dataclasses import dataclass

from core import policy_engine


@dataclass(frozen=True)
class RuntimeToolContract:
    intent: str
    description: str
    tool_surface: str
    capability_id: str
    capability_claim: str
    supported: bool
    unsupported_reason: str
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    side_effect_class: str
    approval_requirement: str
    timeout_policy: str
    retry_policy: str
    artifact_emission: str
    error_contract: str

    @property
    def read_only(self) -> bool:
        return self.side_effect_class == "read_only"


def runtime_tool_contracts() -> list[RuntimeToolContract]:
    read_enabled = bool(policy_engine.get("filesystem.allow_read_workspace", True))
    write_enabled = bool(policy_engine.get("filesystem.allow_write_workspace", False))
    sandbox_enabled = bool(policy_engine.get("execution.allow_sandbox_execution", False))
    return [
        RuntimeToolContract(
            intent="workspace.list_files",
            description="List files or directories inside the active workspace.",
            tool_surface="workspace",
            capability_id="workspace.read",
            capability_claim="list files, search text, and read files in the active workspace",
            supported=read_enabled,
            unsupported_reason="Workspace read actions are disabled on this runtime.",
            input_schema={"path": "string optional", "glob": "string optional", "limit": "integer optional"},
            output_schema={"paths": "list[string]", "truncated": "boolean"},
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
        ),
        RuntimeToolContract(
            intent="machine.list_directory",
            description="List visible entries inside a safe local directory such as Desktop, Downloads, or Documents.",
            tool_surface="machine",
            capability_id="machine.read",
            capability_claim="inspect safe local directories on this machine such as Desktop, Downloads, or Documents",
            supported=read_enabled,
            unsupported_reason="Safe local directory inspection is disabled on this runtime.",
            input_schema={"path": "string optional", "directories_only": "boolean optional", "limit": "integer optional"},
            output_schema={"entries": "list[directory_entry]", "truncated": "boolean"},
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
        ),
        RuntimeToolContract(
            intent="machine.inspect_specs",
            description="Inspect grounded machine hardware and runtime specs for this local host.",
            tool_surface="machine",
            capability_id="machine.read",
            capability_claim="inspect safe local directories on this machine such as Desktop, Downloads, or Documents",
            supported=read_enabled,
            unsupported_reason="Grounded machine inspection is disabled on this runtime.",
            input_schema={},
            output_schema={
                "chip_name": "string",
                "os_name": "string",
                "os_version": "string",
                "cpu_cores": "integer",
                "ram_gb": "number",
                "gpu_name": "string optional",
                "vram_gb": "number optional",
                "accelerator": "string",
                "recommended_model": "string",
            },
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
        ),
        RuntimeToolContract(
            intent="workspace.search_text",
            description="Search text inside workspace files and return file/line matches.",
            tool_surface="workspace",
            capability_id="workspace.read",
            capability_claim="list files, search text, and read files in the active workspace",
            supported=read_enabled,
            unsupported_reason="Workspace read actions are disabled on this runtime.",
            input_schema={"query": "string", "path": "string optional", "glob": "string optional", "limit": "integer optional"},
            output_schema={"matches": "list[file_line_match]", "match_count": "integer"},
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
        ),
        RuntimeToolContract(
            intent="workspace.read_file",
            description="Read a workspace file with line numbers.",
            tool_surface="workspace",
            capability_id="workspace.read",
            capability_claim="list files, search text, and read files in the active workspace",
            supported=read_enabled,
            unsupported_reason="Workspace read actions are disabled on this runtime.",
            input_schema={"path": "string", "start_line": "integer optional", "max_lines": "integer optional"},
            output_schema={"lines": "list[numbered_line]", "line_count": "integer"},
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
        ),
        RuntimeToolContract(
            intent="workspace.ensure_directory",
            description="Create a directory inside the active workspace.",
            tool_surface="workspace",
            capability_id="workspace.write",
            capability_claim="create directories, write files, and replace text in the active workspace",
            supported=write_enabled,
            unsupported_reason="Workspace write actions are disabled on this runtime.",
            input_schema={"path": "string"},
            output_schema={"path": "string", "action": "created|already_present"},
            side_effect_class="workspace_write",
            approval_requirement="runtime_policy",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="directory_observation",
            error_contract="returns_structured_error_result",
        ),
        RuntimeToolContract(
            intent="workspace.write_file",
            description="Write full text content to a workspace file.",
            tool_surface="workspace",
            capability_id="workspace.write",
            capability_claim="create directories, write files, and replace text in the active workspace",
            supported=write_enabled,
            unsupported_reason="Workspace write actions are disabled on this runtime.",
            input_schema={"path": "string", "content": "string"},
            output_schema={"path": "string", "line_count": "integer", "action": "created|updated"},
            side_effect_class="workspace_write",
            approval_requirement="runtime_policy",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="file_diff",
            error_contract="returns_structured_error_result",
        ),
        RuntimeToolContract(
            intent="workspace.replace_in_file",
            description="Replace text inside a workspace file.",
            tool_surface="workspace",
            capability_id="workspace.write",
            capability_claim="create directories, write files, and replace text in the active workspace",
            supported=write_enabled,
            unsupported_reason="Workspace write actions are disabled on this runtime.",
            input_schema={"path": "string", "old_text": "string", "new_text": "string", "replace_all": "boolean optional"},
            output_schema={"path": "string", "replacements": "integer"},
            side_effect_class="workspace_write",
            approval_requirement="runtime_policy",
            timeout_policy="local_only_default",
            retry_policy="none",
            artifact_emission="file_diff",
            error_contract="returns_structured_error_result",
        ),
        RuntimeToolContract(
            intent="sandbox.run_command",
            description="Run one bounded shell command inside the active workspace with network blocked.",
            tool_surface="sandbox",
            capability_id="sandbox.command",
            capability_claim="run bounded local commands in the active workspace with network blocked",
            supported=sandbox_enabled,
            unsupported_reason="Sandbox command execution is disabled on this runtime.",
            input_schema={"command": "string", "cwd": "string optional"},
            output_schema={"stdout": "string", "stderr": "string", "returncode": "integer"},
            side_effect_class="sandbox_command",
            approval_requirement="runtime_policy",
            timeout_policy="execution_gate_and_sandbox_limits",
            retry_policy="caller_managed",
            artifact_emission="command_output",
            error_contract="returns_structured_error_result",
        ),
    ]


def runtime_tool_contract_map() -> dict[str, RuntimeToolContract]:
    return {contract.intent: contract for contract in runtime_tool_contracts()}
