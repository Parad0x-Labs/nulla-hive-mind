from __future__ import annotations

import hashlib
import os
import platform
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.persistent_memory import session_memory_policy
from core import policy_engine
from core.task_state_machine import transition
from core.trace_id import ensure_trace
from storage.db import get_connection


_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:\\[^\s]+"),         # Windows paths
    re.compile(r"/[^\s]+"),                   # Unix-ish paths
]

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b")
_TOKEN_RE = re.compile(r"\b[A-Fa-f0-9]{24,}\b|\b[a-zA-Z0-9_\-]{32,}\b")


@dataclass
class TaskRecord:
    task_id: str
    session_id: str
    task_class: str
    task_summary: str
    redacted_input_hash: str
    environment_os: str
    environment_shell: str
    environment_runtime: str
    environment_version_hint: str
    plan_mode: str
    share_scope: str
    confidence: float
    outcome: str
    harmful_flag: bool
    created_at: str
    updated_at: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_name() -> str:
    return "python"


def _runtime_version_hint() -> str:
    version = platform.python_version()
    major_minor = ".".join(version.split(".")[:2])
    return f"python-{major_minor}"


def _current_shell() -> str:
    if platform.system().lower() == "windows":
        return os.environ.get("COMSPEC", "cmd").split("\\")[-1].lower()
    return os.environ.get("SHELL", "sh").split("/")[-1].lower()


def redact_text(text: str) -> str:
    value = text.strip()
    value = _URL_RE.sub("<url>", value)
    value = _EMAIL_RE.sub("<email>", value)
    value = _TOKEN_RE.sub("<token>", value)

    for pattern in _PATH_PATTERNS:
        value = pattern.sub("<path>", value)

    # compress whitespace
    value = re.sub(r"\s+", " ", value).strip()
    return value


def create_task_record(user_input: str, *, session_id: str | None = None) -> TaskRecord:
    redacted = redact_text(user_input)
    now = _utcnow()
    task_id = str(uuid.uuid4())
    task_summary = redacted[:240] if redacted else "empty_input"
    input_hash = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
    policy = session_memory_policy(session_id)

    task = TaskRecord(
        task_id=task_id,
        session_id=str(session_id or ""),
        task_class="pending",
        task_summary=task_summary,
        redacted_input_hash=input_hash,
        environment_os=platform.system().lower(),
        environment_shell=_current_shell(),
        environment_runtime=_runtime_name(),
        environment_version_hint=_runtime_version_hint(),
        plan_mode=str(policy_engine.get("execution.default_mode", "advice_only")),
        share_scope=str(policy.get("share_scope") or "local_only"),
        confidence=0.0,
        outcome="pending",
        harmful_flag=False,
        created_at=now,
        updated_at=now,
    )

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO local_tasks (
                task_id, session_id, task_class, task_summary, redacted_input_hash,
                environment_os, environment_shell, environment_runtime, environment_version_hint,
                plan_mode, share_scope, confidence, outcome, harmful_flag, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.session_id,
                task.task_class,
                task.task_summary,
                task.redacted_input_hash,
                task.environment_os,
                task.environment_shell,
                task.environment_runtime,
                task.environment_version_hint,
                task.plan_mode,
                task.share_scope,
                task.confidence,
                task.outcome,
                1 if task.harmful_flag else 0,
                task.created_at,
                task.updated_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    ensure_trace(task.task_id, trace_id=task.task_id)
    transition(
        entity_type="local_task",
        entity_id=task.task_id,
        to_state="created",
        details={"task_class": task.task_class, "plan_mode": task.plan_mode},
        trace_id=task.task_id,
    )
    return task


def load_task_record(task_id: str) -> TaskRecord | None:
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT task_id, session_id, task_class, task_summary, redacted_input_hash,
                   environment_os, environment_shell, environment_runtime, environment_version_hint,
                   plan_mode, share_scope, confidence, outcome, harmful_flag, created_at, updated_at
            FROM local_tasks
            WHERE task_id = ?
            LIMIT 1
            """,
            (clean_task_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    data = dict(row)
    data["harmful_flag"] = bool(data.get("harmful_flag"))
    return TaskRecord(**data)


def classify(user_input: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    text = redact_text(user_input).lower()
    context = context or {}
    topic_hints = {str(item).lower() for item in context.get("topic_hints") or []}
    reference_targets = {str(item).lower() for item in context.get("reference_targets") or []}
    understanding_confidence = float(context.get("understanding_confidence") or 0.0)
    quality_flags = {str(item).lower() for item in context.get("quality_flags") or []}

    risk_flags: list[str] = []
    task_class = "unknown"
    confidence_hint = 0.35

    risky_markers = {
        "rm -rf": "destructive_command",
        "format ": "destructive_command",
        "sudo ": "privileged_action",
        "powershell -enc": "shell_injection_risk",
        "registry": "privileged_action",
        "system32": "privileged_action",
        "startup": "persistence_attempt",
        "launchctl": "persistence_attempt",
        "systemctl": "persistence_attempt",
        "cron": "persistence_attempt",
        "exfiltrate": "exfiltration_hint",
    }

    for marker, flag in risky_markers.items():
        if marker in text:
            risk_flags.append(flag)

    if risk_flags:
        task_class = "risky_system_action"
        confidence_hint = 0.90
    elif any(k in text for k in ["harden", "protect", "password", "passwords", "secret", "secrets", "leak", "leaks", "credential"]) or {"security", "security hardening", "password leak", "protect"} & topic_hints:
        task_class = "security_hardening"
        confidence_hint = 0.80
    elif any(k in text for k in ["swarm", "mesh", "replica", "replication", "shard", "presence", "knowledge"]) or {"knowledge shard", "swarm memory", "replica", "replication", "presence", "knowledge"} & topic_hints:
        task_class = "system_design"
        confidence_hint = 0.74
    elif any(k in text for k in ["traceback", "stack trace", "exception", "error", "bug", "fails", "broken"]):
        task_class = "debugging"
        confidence_hint = 0.75
    elif any(k in text for k in ["npm", "pip", "cargo", "brew", "dependency", "install", "module not found"]):
        task_class = "dependency_resolution"
        confidence_hint = 0.78
    elif any(k in text for k in ["config", "yaml", "json", ".env", "setting", "configure"]):
        task_class = "config"
        confidence_hint = 0.70
    elif (
        any(k in text for k in ["hive", "brain hive", "public hive", "hive mind"])
        and any(k in text for k in ["available", "open", "task", "tasks", "queue", "pull", "claim", "work on", "help with", "select"])
    ):
        task_class = "integration_orchestration"
        confidence_hint = 0.82
    elif (
        any(
            marker in text
            for marker in [
                "build",
                "design",
                "architecture",
                "plan",
                "best practice",
                "best practices",
                "framework",
                "stack",
                "github",
                "repo",
                "repos",
                "docs",
                "documentation",
            ]
        )
        and any(
            marker in text
            for marker in [
                "telegram",
                "discord",
                "bot",
                "api",
                "integration",
                "webhook",
                "agent",
            ]
        )
    ):
        task_class = "system_design"
        confidence_hint = 0.80
    elif any(k in text for k in ["find", "look up", "research", "search", "what is", "tell me about"]):
        task_class = "research"
        confidence_hint = 0.65
    elif any(
        k in text
        for k in [
            "calendar",
            "schedule",
            "meeting",
            "email",
            "inbox",
            "telegram",
            "discord",
            "openclaw",
            "integration",
            "webhook",
            "sync",
        ]
    ):
        task_class = "integration_orchestration"
        confidence_hint = 0.76
    elif any(k in text for k in ["read file", "inspect file", "open file", "check file"]):
        task_class = "file_inspection"
        confidence_hint = 0.68
    elif any(k in text for k in ["run ", "execute ", "command", "shell", "terminal"]):
        task_class = "shell_guidance"
        confidence_hint = 0.72

    if reference_targets:
        confidence_hint = min(0.92, confidence_hint + 0.04)
    if understanding_confidence:
        confidence_hint = min(confidence_hint, max(0.30, understanding_confidence))
    if "ambiguous_reference" in quality_flags:
        confidence_hint = min(confidence_hint, 0.42)

    return {
        "task_class": task_class,
        "risk_flags": sorted(set(risk_flags)),
        "confidence_hint": confidence_hint,
        "context_hints": list(context.keys()),
    }


def context_strategy(task_class: str, *, context: dict[str, Any] | None = None, user_input: str = "") -> dict[str, Any]:
    context = context or {}
    topic_hints = {str(item).lower() for item in context.get("topic_hints") or []}
    lower = redact_text(user_input).lower()
    explicit_archive = any(
        marker in lower
        for marker in ("archive", "history", "older", "previous", "earlier", "receipt", "audit", "trace")
    )

    base = {
        "total_context_budget": 900,
        "bootstrap_budget": 180,
        "relevant_budget": 520,
        "cold_budget": 0,
        "max_bootstrap_items": 5,
        "max_relevant_items": 6,
        "max_cold_items": 2,
        "allow_swarm_metadata": False,
        "allow_swarm_fetch": False,
        "archive_dependent": False,
    }

    if task_class in {"shell_guidance", "file_inspection"}:
        base.update({"total_context_budget": 680, "relevant_budget": 300, "max_relevant_items": 4})
    elif task_class in {"system_design", "research", "integration_orchestration"}:
        base.update({"total_context_budget": 1100, "relevant_budget": 650, "max_relevant_items": 8, "allow_swarm_metadata": True})
    elif task_class == "security_hardening":
        base.update({"total_context_budget": 980, "relevant_budget": 560, "max_relevant_items": 6})
    elif task_class in {"dependency_resolution", "config", "debugging"}:
        base.update({"total_context_budget": 950, "relevant_budget": 560, "max_relevant_items": 7})

    if {"swarm", "mesh", "replication", "presence", "knowledge"} & topic_hints:
        base["allow_swarm_metadata"] = True

    if any(
        marker in lower
        for marker in (
            "from swarm",
            "from hive",
            "use swarm",
            "use hive",
            "consult swarm",
            "consult hive",
            "remote peers",
            "peer research",
            "swarm memory",
            "hive mind",
            "shared research",
        )
    ) or {"swarm memory", "knowledge shard", "hive mind", "public hive"} & topic_hints:
        base["allow_swarm_metadata"] = True
        base["allow_swarm_fetch"] = True

    if explicit_archive:
        base.update(
            {
                "cold_budget": max(120, int(base["cold_budget"])),
                "archive_dependent": True,
                "total_context_budget": max(int(base["total_context_budget"]), int(base["bootstrap_budget"]) + int(base["relevant_budget"]) + 120),
            }
        )

    return base


def curiosity_profile(task_class: str, *, context: dict[str, Any] | None = None, user_input: str = "") -> dict[str, Any]:
    context = context or {}
    lower = redact_text(user_input).lower()
    topic_hints = {str(item).lower() for item in context.get("topic_hints") or []}
    interest_score = 0.30
    topic_kind = "general"

    if task_class in {"research", "system_design"}:
        interest_score += 0.22
        topic_kind = "technical"
    if any(token in lower for token in ("telegram", "discord", "bot", "api", "integration", "calendar", "email", "meeting", "schedule", "inbox")):
        interest_score += 0.18
        topic_kind = "integration"
    elif any(token in lower for token in ("design", "ux", "ui", "mobile app", "web app", "layout")):
        interest_score += 0.16
        topic_kind = "design"
    elif any(token in lower for token in ("news", "headline", "current events", "pulse", "today")):
        interest_score += 0.12
        topic_kind = "news"

    if {"telegram bot", "meet and greet", "swarm memory", "knowledge shard"} & topic_hints:
        interest_score += 0.10

    return {
        "interest_score": max(0.0, min(1.0, interest_score)),
        "topic_kind": topic_kind,
    }


def model_execution_profile(task_class: str) -> dict[str, Any]:
    mapping = {
        "dependency_resolution": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True},
        "debugging": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True},
        "config": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": False},
        "security_hardening": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True},
        "system_design": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True},
        "integration_orchestration": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True},
        "research": {"task_kind": "summarization", "output_mode": "summary_block", "allow_paid_fallback": True},
        "file_inspection": {"task_kind": "summarization", "output_mode": "summary_block", "allow_paid_fallback": False},
        "shell_guidance": {"task_kind": "summarization", "output_mode": "summary_block", "allow_paid_fallback": False},
        "unknown": {"task_kind": "normalization_assist", "output_mode": "summary_block", "allow_paid_fallback": False},
    }
    return dict(mapping.get(task_class, mapping["unknown"]))
