from dataclasses import dataclass
import shlex
from typing import List, Any

from core import policy_engine
from core.user_preferences import load_preferences

@dataclass
class GateDecision:
    mode: str                     # blocked, advice_only, simulate_only, sandbox, execute
    reason: str
    requires_user_approval: bool
    allowed_actions: List[str]

class ExecutionGate:
    """
    V2: The Hard Wall.
    No execution passes without explicitly fulfilling the 9 safety checks.
    """
    
    @staticmethod
    def _contains_blocked_risk(risk_flags: List[str]) -> bool:
        blocked = {
            "destructive_command",
            "privileged_action",
            "persistence_attempt",
            "exfiltration_hint",
            "shell_injection_risk",
            "raw_remote_instruction"
        }
        return any(flag in blocked for flag in risk_flags)

    @staticmethod
    def _actions_within_workspace(actions: List[dict]) -> bool:
        # In V2, we enforce a basic path traversal check here, augmented by filesystem_guard later
        for act in actions:
            cmd = act.get("cmd", "")
            if "cd /" in cmd or "cd \\" in cmd or "../.." in cmd:
                return False
        return True

    @staticmethod
    def _has_valid_capability(cap: str) -> bool:
        # For MVP, capabilities are disabled by default
        return False

    @staticmethod
    def local_action_guardrails(
        action_name: str,
        *,
        destructive: bool,
    ) -> dict[str, bool]:
        normalized = str(action_name or "").strip().lower()
        outward_facing = normalized in {"discord_post", "telegram_send"}
        privacy_sensitive = normalized in {"discord_post", "telegram_send"}
        return {
            "destructive": bool(destructive),
            "outward_facing": outward_facing,
            "privacy_sensitive": privacy_sensitive,
        }

    @staticmethod
    def evaluate_local_action(
        action_name: str,
        *,
        destructive: bool,
        user_approved: bool,
        reads_workspace: bool = False,
        writes_workspace: bool = False,
    ) -> GateDecision:
        guardrails = ExecutionGate.local_action_guardrails(
            action_name,
            destructive=destructive,
        )
        if not policy_engine.get("execution.allow_safe_local_actions", True):
            return GateDecision(
                mode="advice_only",
                reason="Safe local actions are disabled by policy.",
                requires_user_approval=False,
                allowed_actions=[],
            )

        allowed = set(policy_engine.get("execution.allowed_safe_local_actions", []) or [])
        if action_name not in allowed:
            return GateDecision(
                mode="blocked",
                reason=f"Action '{action_name}' is not in the allowed local action set.",
                requires_user_approval=False,
                allowed_actions=[],
            )

        if (
            (
                guardrails["destructive"]
                or guardrails["outward_facing"]
                or guardrails["privacy_sensitive"]
            )
            and policy_engine.get("execution.require_explicit_user_approval_for_execution", True)
            and not user_approved
            and ExecutionGate._requires_explicit_approval(
                action_name,
                destructive=guardrails["destructive"],
                outward_facing=guardrails["outward_facing"],
                privacy_sensitive=guardrails["privacy_sensitive"],
            )
        ):
            if guardrails["outward_facing"] or guardrails["privacy_sensitive"]:
                reason = "Outward-facing or privacy-sensitive action requires explicit user approval."
            else:
                reason = "Execution requires explicit user approval."
            return GateDecision(
                mode="advice_only",
                reason=reason,
                requires_user_approval=True,
                allowed_actions=[],
            )

        if writes_workspace and not destructive and not policy_engine.get("filesystem.allow_write_workspace", False):
            return GateDecision(
                mode="advice_only",
                reason="Write action blocked by filesystem policy.",
                requires_user_approval=False,
                allowed_actions=[],
            )

        if reads_workspace and not policy_engine.get("filesystem.allow_read_workspace", True):
            return GateDecision(
                mode="blocked",
                reason="Read action blocked by filesystem policy.",
                requires_user_approval=False,
                allowed_actions=[],
            )

        return GateDecision(
            mode="execute",
            reason="Bounded local action allowed.",
            requires_user_approval=False,
            allowed_actions=[action_name],
        )

    @staticmethod
    def _requires_explicit_approval(
        action_name: str,
        *,
        destructive: bool = False,
        outward_facing: bool = False,
        privacy_sensitive: bool = False,
    ) -> bool:
        prefs = load_preferences()
        autonomy_mode = str(getattr(prefs, "autonomy_mode", "hands_off") or "hands_off").strip().lower()
        if outward_facing or privacy_sensitive:
            return True
        if autonomy_mode == "strict":
            return True
        if autonomy_mode == "balanced":
            return destructive or action_name in {
                "cleanup_temp_files",
                "move_path",
                "schedule_calendar_event",
                "discord_post",
                "telegram_send",
            }
        return action_name in {"cleanup_temp_files", "move_path", "discord_post", "telegram_send"}

    @staticmethod
    def evaluate_command(cmd: str) -> dict[str, str]:
        text = str(cmd or "").strip()
        if not text:
            return {"decision": "blocked", "reason": "Empty command."}
        lowered = text.lower()
        for marker in ("rm -rf", "del /f", "format ", "shutdown", "reboot", "mkfs", "powershell -enc"):
            if marker in lowered:
                return {"decision": "blocked", "reason": "Command contains blocked destructive markers."}
        if any(marker in text for marker in ("&&", "||", ";", "|", "`", "$(", "\n")):
            return {"decision": "blocked", "reason": "Compound shell syntax is not allowed in sandbox commands."}
        try:
            argv = shlex.split(text, posix=True)
        except ValueError:
            return {"decision": "blocked", "reason": "Command could not be parsed safely."}
        if not argv:
            return {"decision": "blocked", "reason": "Empty command."}
        base_cmd = ExecutionGate._base_command(argv)
        destructive = ExecutionGate._is_destructive_command(base_cmd, argv, lowered)
        read_only = ExecutionGate._is_read_only_command(base_cmd, argv)
        writes_workspace = not read_only
        if not policy_engine.get("execution.allow_sandbox_execution", False):
            if policy_engine.get("execution.allow_simulation", True):
                return {
                    "decision": "simulate_only",
                    "reason": "Sandbox disabled; simulation allowed.",
                    "base_command": base_cmd,
                    "destructive": str(destructive).lower(),
                    "read_only": str(read_only).lower(),
                }
            return {
                "decision": "advice_only",
                "reason": "Execution disabled by policy.",
                "base_command": base_cmd,
                "destructive": str(destructive).lower(),
                "read_only": str(read_only).lower(),
            }
        if writes_workspace and not policy_engine.get("filesystem.allow_write_workspace", False):
            return {
                "decision": "advice_only",
                "reason": "Workspace writes are disabled by filesystem policy.",
                "base_command": base_cmd,
                "destructive": str(destructive).lower(),
                "read_only": str(read_only).lower(),
            }
        if read_only and not policy_engine.get("filesystem.allow_read_workspace", True):
            return {
                "decision": "blocked",
                "reason": "Workspace reads are disabled by filesystem policy.",
                "base_command": base_cmd,
                "destructive": str(destructive).lower(),
                "read_only": str(read_only).lower(),
            }
        if ExecutionGate._command_requires_approval(base_cmd, read_only=read_only, destructive=destructive):
            return {
                "decision": "advice_only",
                "reason": "Command requires explicit user approval.",
                "base_command": base_cmd,
                "destructive": str(destructive).lower(),
                "read_only": str(read_only).lower(),
            }
        return {
            "decision": "sandbox",
            "reason": "Sandbox execution allowed.",
            "base_command": base_cmd,
            "destructive": str(destructive).lower(),
            "read_only": str(read_only).lower(),
        }

    @staticmethod
    def _base_command(argv: List[str]) -> str:
        if not argv:
            return ""
        first = str(argv[0] or "").strip().lower()
        if first == "env":
            index = 1
            while index < len(argv):
                token = str(argv[index] or "").strip()
                if "=" in token and not token.startswith("-"):
                    index += 1
                    continue
                return token.lower()
        return first

    @staticmethod
    def _is_read_only_command(base_cmd: str, argv: List[str]) -> bool:
        readonly_bases = {
            "ls",
            "dir",
            "pwd",
            "cat",
            "type",
            "head",
            "tail",
            "wc",
            "rg",
            "grep",
            "find",
        }
        if base_cmd in readonly_bases:
            return True
        if base_cmd == "sed":
            return "-i" not in argv and not any(str(item or "").startswith("-i") for item in argv)
        if base_cmd == "git":
            readonly_subcommands = {"status", "diff", "show", "log", "branch", "rev-parse", "grep"}
            return len(argv) >= 2 and str(argv[1] or "").strip().lower() in readonly_subcommands
        if base_cmd in {"pytest", "python", "python3", "node", "nodejs", "npm", "pnpm", "yarn", "cargo"}:
            return False
        return False

    @staticmethod
    def _is_destructive_command(base_cmd: str, argv: List[str], lowered: str) -> bool:
        if base_cmd in {"rm", "del", "format", "mkfs", "shutdown", "reboot"}:
            return True
        if base_cmd == "git":
            destructive_git = {"reset", "clean", "checkout", "restore", "rebase", "merge", "cherry-pick", "am"}
            return len(argv) >= 2 and str(argv[1] or "").strip().lower() in destructive_git
        destructive_markers = ("sudo ", "launchctl", "systemctl", "crontab", "defaults write", "diskutil", "fdisk")
        return any(marker in lowered for marker in destructive_markers)

    @staticmethod
    def _command_requires_approval(base_cmd: str, *, read_only: bool, destructive: bool) -> bool:
        prefs = load_preferences()
        autonomy_mode = str(getattr(prefs, "autonomy_mode", "hands_off") or "hands_off").strip().lower()
        if autonomy_mode == "strict":
            return not read_only
        if destructive:
            return True
        if autonomy_mode == "balanced":
            return False
        if autonomy_mode == "hands_off":
            return False
        return not read_only

    @staticmethod
    def evaluate(plan: Any, task: dict, persona: Any) -> GateDecision:
        """
        Evaluates the plan against the exact 9-step constraint list from the V2 Spec.
        """
        # 1. Global default: advice-only
        default_mode = "advice_only"
            
        risk_flags = plan.risk_flags if hasattr(plan, 'risk_flags') else []
        safe_actions = plan.safe_actions if hasattr(plan, 'safe_actions') else []
        confidence = plan.confidence if hasattr(plan, 'confidence') else 0.5
        task_class = task.get("task_class", "")

        # 2. Never allow execution if plan contains blocked risk flags
        if ExecutionGate._contains_blocked_risk(risk_flags):
            return GateDecision(
                mode="blocked",
                reason="Plan contains blocked risk flags.",
                requires_user_approval=False,
                allowed_actions=[]
            )

        # 3. Never allow if task is system-sensitive
        if task_class in {"privileged_system_change", "persistence_setup", "unknown_binary_action"}:
            return GateDecision(
                mode="advice_only",
                reason="System-sensitive task forced to advice-only.",
                requires_user_approval=True,
                allowed_actions=[]
            )

        # 4. If sandbox execution disabled, stay advice-only
        if not policy_engine.get("execution.allow_sandbox_execution", False):
            if policy_engine.get("execution.allow_simulation", True):
                return GateDecision(
                    mode="simulate_only",
                    reason="Sandbox disabled; simulation allowed.",
                    requires_user_approval=False,
                    allowed_actions=["simulate"]
                )
            return GateDecision(
                mode="advice_only",
                reason="Execution disabled by policy.",
                requires_user_approval=False,
                allowed_actions=[]
            )

        # 5. Check confidence threshold
        if confidence < 0.85:
            return GateDecision(
                mode="advice_only",
                reason="Confidence below execution threshold.",
                requires_user_approval=False,
                allowed_actions=[]
            )

        # 6. Check required capabilities (omitted MVP tokens - failing closed)
        if hasattr(plan, 'reads_workspace') and plan.reads_workspace and not ExecutionGate._has_valid_capability("READ_WORKSPACE"):
            return GateDecision(
                mode="advice_only",
                reason="Missing capability token: READ_WORKSPACE",
                requires_user_approval=True,
                allowed_actions=[]
            )

        # 7. Check file scope
        if not ExecutionGate._actions_within_workspace(safe_actions):
            return GateDecision(
                mode="blocked",
                reason="Action escapes allowed workspace.",
                requires_user_approval=False,
                allowed_actions=[]
            )

        # 8. Final user approval gate
        if policy_engine.get("execution.require_explicit_user_approval_for_execution", True):
            return GateDecision(
                mode="advice_only",
                reason="Execution requires explicit user approval.",
                requires_user_approval=True,
                allowed_actions=[]
            )

        # 9. Last resort: sandbox only
        return GateDecision(
            mode="sandbox",
            reason="Passed safety checks; sandbox allowed.",
            requires_user_approval=False,
            allowed_actions=["sandbox_execute"]
        )
