from __future__ import annotations

import ast
import hashlib
import operator
import os
import platform
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core import policy_engine
from core.learning import load_procedure_shards, rank_reusable_procedures
from core.orchestration import TaskEnvelopeV1, build_task_envelope
from core.persistent_memory import session_memory_policy
from core.task_state_machine import transition
from core.trace_id import ensure_trace
from storage.db import get_connection

_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:\\[^\s]+"),         # Windows paths
    re.compile(r"~/(?:[^\s)]+)"),             # Home-relative Unix paths
    re.compile(r"/(?:Users|home|etc|var|tmp|opt|srv|private|mnt)/[^\s)]+"),  # Sensitive absolute Unix paths
]

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b")
_TOKEN_RE = re.compile(r"\b[A-Fa-f0-9]{24,}\b|\b[a-zA-Z0-9_\-]{32,}\b")

_BUSINESS_CHAT_MARKERS = (
    "business",
    "pricing",
    "position",
    "positioning",
    "go to market",
    "gtm",
    "sales",
    "marketing",
    "revenue",
    "customer acquisition",
    "brand strategy",
    "market strategy",
)
_FOOD_CHAT_MARKERS = (
    "food",
    "meal",
    "meals",
    "recipe",
    "recipes",
    "diet",
    "nutrition",
    "calories",
    "protein",
    "carbs",
    "fat loss",
    "what should i eat",
)
_RELATIONSHIP_CHAT_MARKERS = (
    "relationship",
    "relationships",
    "partner",
    "girlfriend",
    "boyfriend",
    "wife",
    "husband",
    "dating",
    "breakup",
    "intimacy",
    "sex life",
    "argument",
)
_CREATIVE_CHAT_MARKERS = (
    "brainstorm",
    "creative",
    "campaign idea",
    "campaign ideas",
    "name ideas",
    "tagline",
    "slogan",
    "story idea",
    "concept ideas",
    "creative direction",
)
_GENERAL_ADVISORY_MARKERS = (
    "should i",
    "what should i do",
    "help me decide",
    "need advice",
    "advice on",
    "how do i handle",
)
_HIVE_MARKERS = ("hive", "hive mind", "brain hive", "public hive")
_SEMANTIC_HIVE_PATTERNS = (
    re.compile(r"\b(?:check|show|list|see)\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\b"),
    re.compile(r"\bwhat(?:'s| is)\s+in\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\b"),
    re.compile(r"\bwhat(?:'s| is)\s+on\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\b"),
    re.compile(r"\banything\s+on\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\b"),
    re.compile(r"\bshow\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\s+(?:work|tasks?|queue)\b"),
    re.compile(r"\bwhat\s+(?:online\s+)?tasks?\s+(?:do\s+)?we\s+have\b"),
)
_ENTITY_LOOKUP_QUESTION_PATTERNS = (
    re.compile(r"\bwho(?:'s|\s+is)\b"),
    re.compile(r"\btell\s+me\s+about\b"),
    re.compile(r"\bwhat\s+do\s+you\s+know\s+about\b"),
    re.compile(r"\bis\s+(?:he|she|they)\s+(?:the\s+)?(?:owner|founder|ceo|cto|creator|co-founder)\b"),
    re.compile(r"\bwho\s+(?:is|are|was)\s+(?:behind|running|leading)\b"),
    re.compile(r"\bwhat\s+(?:is|are)\s+(?:his|her|their)\s+(?:role|position|title)\b"),
    re.compile(r"\bi\s+see\s+(?:him|her|them)\s+mentioned\b"),
    re.compile(r"\bwho\s+(?:runs?|owns?|founded|created|built|leads?)\b"),
    re.compile(r"\bfind\s+(?:me\s+)?who\b"),
)
_EXPLICIT_LOOKUP_MARKERS = (
    "find",
    "look up",
    "lookup",
    "search",
    "google",
    "check",
)
_WEB_SOCIAL_LOOKUP_MARKERS = (
    "on x",
    "x.com",
    "on twitter",
    "twitter",
    "on the web",
    "on web",
    "google",
    "online",
    "web",
)
_DIRECT_MATH_EXPRESSION_RE = re.compile(r"^[\d\s\.\+\-\*\/%\(\)]+$")
_SAFE_ARITHMETIC_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_WORD_MATH_MARKERS = (
    " total ",
    " sum ",
    " show the steps",
    " step by step",
    " twice ",
    " minus ",
    " plus ",
    " times ",
    " multiplied by ",
    " divided by ",
    " in total",
)
_WORD_MATH_CONTEXT_MARKERS = (
    " minute",
    " minutes",
    " hour",
    " hours",
    " task a",
    " task b",
    " task c",
    " takes ",
)
_LIVE_RECENCY_MARKERS = (
    " right now",
    " current ",
    " latest ",
    " today ",
    " just now",
    " just happened",
    " minute ago",
    " minutes ago",
    " hour ago",
    " hours ago",
)
_LIVE_FACT_DOMAIN_MARKERS = (
    " btc",
    " bitcoin",
    " eur/usd",
    " eurusd",
    " weather",
    " headline",
    " headlines",
    " news",
    " markets",
    " market",
    " price",
)
_NON_WEB_LOOKUP_EXCLUSIONS = (
    "file",
    "files",
    "folder",
    "workspace",
    "repo",
    "repository",
    "command",
    "terminal",
    "shell",
    "calendar",
    "email",
)
_PLAIN_TEXT_CHAT_TASK_CLASSES = {
    "chat_conversation",
    "chat_research",
    "general_advisory",
    "business_advisory",
    "food_nutrition",
    "relationship_advisory",
    "creative_ideation",
}
_AI_FIRST_CHAT_DOMAIN_TASK_CLASSES = {
    "unknown",
    "research",
    "chat_conversation",
    "chat_research",
    "general_advisory",
    "business_advisory",
    "food_nutrition",
    "relationship_advisory",
    "creative_ideation",
    "debugging",
    "dependency_resolution",
    "config",
    "system_design",
    "file_inspection",
    "shell_guidance",
}


def looks_like_semantic_hive_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    if any(
        marker in lowered
        for marker in (
            "create task",
            "create topic",
            "new task",
            "new topic",
            "claim task",
            "submit result",
            "post progress",
            "status",
            "done",
            "finished",
        )
    ):
        return False
    if any(pattern.search(lowered) for pattern in _SEMANTIC_HIVE_PATTERNS):
        return True
    return bool(any(marker in lowered for marker in _HIVE_MARKERS) and any(marker in lowered for marker in ("task", "tasks", "work", "queue", "open", "available", "online", "anything")))


def looks_like_public_entity_lookup_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered or looks_like_semantic_hive_request(lowered):
        return False
    if any(marker in lowered for marker in _NON_WEB_LOOKUP_EXCLUSIONS):
        return False
    if any(pattern.search(lowered) for pattern in _ENTITY_LOOKUP_QUESTION_PATTERNS):
        return True
    has_lookup_marker = any(marker in lowered for marker in _EXPLICIT_LOOKUP_MARKERS)
    has_web_or_social_cue = any(marker in lowered for marker in _WEB_SOCIAL_LOOKUP_MARKERS)
    has_public_context = any(marker in lowered for marker in (
        "solana", "founder", "ceo", "cto", "person", "guy", "girl", "crypto",
        "ethereum", "bitcoin", "helius", "blockchain", "defi", "nft",
        "company", "startup", "project", "protocol", "influencer",
        "community", "developer", "engineer", "owner", "co-founder",
    ))
    return bool(has_lookup_marker and (has_web_or_social_cue or has_public_context))


def looks_like_explicit_lookup_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered or looks_like_semantic_hive_request(lowered):
        return False
    if any(marker in lowered for marker in _NON_WEB_LOOKUP_EXCLUSIONS):
        return False
    if looks_like_public_entity_lookup_request(lowered):
        return True
    return bool(
        any(marker in lowered for marker in ("look up", "lookup", "search online", "check online", "browse"))
        or (
            any(marker in lowered for marker in ("find", "check", "search", "google"))
            and any(marker in lowered for marker in _WEB_SOCIAL_LOOKUP_MARKERS)
        )
    )


def looks_like_direct_math_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    if not _DIRECT_MATH_EXPRESSION_RE.fullmatch(lowered):
        return False
    if not any(marker in lowered for marker in ("+", "-", "*", "/", "%")):
        return False
    return len(re.findall(r"\d+(?:\.\d+)?", lowered)) >= 2


def evaluate_direct_math_request(text: str) -> str | None:
    expression = " ".join(str(text or "").strip().split())
    if not looks_like_direct_math_request(expression):
        return None

    try:
        parsed = ast.parse(expression, mode="eval")
    except Exception:
        return None

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Num):  # pragma: no cover
            return float(node.n)
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_ARITHMETIC_OPERATORS:
            return _SAFE_ARITHMETIC_OPERATORS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_ARITHMETIC_OPERATORS:
            return _SAFE_ARITHMETIC_OPERATORS[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported arithmetic node")

    try:
        value = _eval(parsed)
    except Exception:
        return None

    rendered = str(int(value)) if isinstance(value, float) and value.is_integer() else f"{value:.12g}"
    return f"{expression} = {rendered}."


def evaluate_word_math_request(text: str) -> str | None:
    normalized = " ".join(str(text or "").strip().split())
    if not looks_like_word_math_request(normalized):
        return None

    task_triplet = re.search(
        r"task a takes (?P<a>\d+(?:\.\d+)?) [^.?!]*?task b takes twice task a minus (?P<delta>\d+(?:\.\d+)?) [^.?!]*?task c takes (?P<c>\d+(?:\.\d+)?)",
        normalized,
        re.IGNORECASE,
    )
    if task_triplet is None:
        return None

    a = float(task_triplet.group("a"))
    delta = float(task_triplet.group("delta"))
    c = float(task_triplet.group("c"))
    b = (2 * a) - delta
    total = a + b + c

    def _render(value: float) -> str:
        return str(int(value)) if value.is_integer() else f"{value:.12g}"

    return (
        f"Task A = {_render(a)}. "
        f"Task B = 2 * {_render(a)} - {_render(delta)} = {_render(b)}. "
        f"Task C = {_render(c)}. "
        f"Total = {_render(a)} + {_render(b)} + {_render(c)} = {_render(total)}."
    )


def looks_like_word_math_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered or looks_like_direct_math_request(lowered):
        return False
    if len(re.findall(r"\d+(?:\.\d+)?", lowered)) < 2:
        return False
    if not any(marker in lowered for marker in _WORD_MATH_MARKERS):
        return False
    return any(marker in lowered for marker in _WORD_MATH_CONTEXT_MARKERS)


def looks_like_live_recency_lookup(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    has_recency = any(marker in lowered for marker in _LIVE_RECENCY_MARKERS)
    has_domain = any(marker in lowered for marker in _LIVE_FACT_DOMAIN_MARKERS) or "what happened" in lowered
    return has_recency and has_domain


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


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


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
    elif looks_like_direct_math_request(text):
        task_class = "chat_conversation"
        confidence_hint = 0.94
    elif looks_like_word_math_request(text):
        task_class = "chat_conversation"
        confidence_hint = 0.86
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
    elif looks_like_semantic_hive_request(text):
        task_class = "integration_orchestration"
        confidence_hint = 0.84
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
    elif _contains_any(text, _BUSINESS_CHAT_MARKERS) or {"business", "pricing", "marketing", "sales"} & topic_hints:
        task_class = "business_advisory"
        confidence_hint = 0.62
    elif _contains_any(text, _FOOD_CHAT_MARKERS) or {"food", "nutrition", "meal", "diet"} & topic_hints:
        task_class = "food_nutrition"
        confidence_hint = 0.62
    elif _contains_any(text, _RELATIONSHIP_CHAT_MARKERS) or {"relationship", "dating", "intimacy"} & topic_hints:
        task_class = "relationship_advisory"
        confidence_hint = 0.62
    elif _contains_any(text, _CREATIVE_CHAT_MARKERS) or {"creative", "brainstorm", "campaign"} & topic_hints:
        task_class = "creative_ideation"
        confidence_hint = 0.62
    elif _contains_any(text, _GENERAL_ADVISORY_MARKERS):
        task_class = "general_advisory"
        confidence_hint = 0.58
    elif looks_like_live_recency_lookup(text):
        task_class = "research"
        confidence_hint = 0.78
    elif looks_like_public_entity_lookup_request(text) or looks_like_explicit_lookup_request(text):
        task_class = "research"
        confidence_hint = 0.70
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

    if task_class == "unknown":
        model_class = _classify_via_model(user_input)
        if model_class:
            task_class = model_class
            confidence_hint = 0.65

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


_VALID_TASK_CLASSES = {
    "chat_conversation", "research", "debugging", "system_design",
    "integration_orchestration", "shell_guidance", "file_inspection",
    "config", "dependency_resolution", "security_hardening",
    "business_advisory", "food_nutrition", "relationship_advisory",
    "creative_ideation", "general_advisory",
}


def _classify_via_model(user_input: str) -> str:
    """Ask the local LLM to classify user intent when regex fails."""
    try:
        import requests as _req

        from core.hardware_tier import recommended_ollama_model

        base_url = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        _req.get(f"{base_url}/api/tags", timeout=1.5)

        class_list = ", ".join(sorted(_VALID_TASK_CLASSES))
        prompt = (
            f"Classify this user message into exactly one category.\n"
            f"Categories: {class_list}\n\n"
            f"User message: {user_input[:300]}\n\n"
            f"Reply with ONLY the category name, nothing else."
        )
        resp = _req.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": recommended_ollama_model(),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 20,
            },
            timeout=8,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip().lower()
        clean = raw.strip().strip("'\"`.").strip()
        if clean in _VALID_TASK_CLASSES:
            return clean
        for cls in _VALID_TASK_CLASSES:
            if cls in clean:
                return cls
    except Exception:
        pass
    return ""


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


def model_execution_profile(
    task_class: str,
    *,
    chat_surface: bool = False,
    planner_style_requested: bool = False,
) -> dict[str, Any]:
    mapping = {
        "dependency_resolution": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True, "provider_role": "queen"},
        "debugging": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True, "provider_role": "queen"},
        "config": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": False, "provider_role": "auto"},
        "security_hardening": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True, "provider_role": "queen"},
        "system_design": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True, "provider_role": "queen"},
        "integration_orchestration": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True, "provider_role": "queen"},
        "research": {"task_kind": "summarization", "output_mode": "summary_block", "allow_paid_fallback": True, "provider_role": "queen"},
        "file_inspection": {"task_kind": "summarization", "output_mode": "summary_block", "allow_paid_fallback": False, "provider_role": "auto"},
        "shell_guidance": {"task_kind": "summarization", "output_mode": "summary_block", "allow_paid_fallback": False, "provider_role": "auto"},
        "unknown": {"task_kind": "normalization_assist", "output_mode": "summary_block", "allow_paid_fallback": False, "provider_role": "auto"},
        "chat_conversation": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
        "chat_research": {"task_kind": "summarization", "output_mode": "plain_text", "allow_paid_fallback": True, "provider_role": "queen"},
        "general_advisory": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
        "business_advisory": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
        "food_nutrition": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
        "relationship_advisory": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
        "creative_ideation": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
    }
    normalized_task_class = str(task_class or "unknown").strip().lower() or "unknown"
    base_profile = dict(mapping.get(normalized_task_class, mapping["unknown"]))

    if chat_surface and normalized_task_class in _AI_FIRST_CHAT_DOMAIN_TASK_CLASSES:
        if planner_style_requested:
            return {
                "task_kind": "action_plan",
                "output_mode": "action_plan",
                "allow_paid_fallback": bool(base_profile.get("allow_paid_fallback", False)),
                "provider_role": str(base_profile.get("provider_role", "auto") or "auto"),
            }
        return {
            "task_kind": "normalization_assist",
            "output_mode": "plain_text",
            "allow_paid_fallback": bool(base_profile.get("allow_paid_fallback", False)),
            "provider_role": str(base_profile.get("provider_role", "auto") or "auto"),
        }

    return base_profile


def orchestration_role_for_task_class(task_class: str) -> str:
    normalized = str(task_class or "unknown").strip().lower() or "unknown"
    if normalized in {"system_design", "integration_orchestration"}:
        return "queen"
    if normalized in {"research", "chat_research"}:
        return "researcher"
    if normalized in {"debugging", "dependency_resolution", "config", "security_hardening"}:
        return "coder"
    if normalized in {"file_inspection", "shell_guidance"}:
        return "verifier"
    return "narrator"


def build_task_envelope_for_request(
    user_input: str,
    *,
    context: dict[str, Any] | None = None,
    task_id: str | None = None,
    parent_task_id: str = "",
    chat_surface: bool = False,
    planner_style_requested: bool = False,
) -> TaskEnvelopeV1:
    context = dict(context or {})
    classification = classify(user_input, context)
    routed_task_class = str(classification.get("task_class") or "unknown")
    if chat_surface:
        routed_task_class = chat_surface_execution_task_class(
            routed_task_class,
            user_input=user_input,
            context=context,
        )
    profile = model_execution_profile(
        routed_task_class,
        chat_surface=chat_surface,
        planner_style_requested=planner_style_requested,
    )
    role = orchestration_role_for_task_class(routed_task_class)
    if str(profile.get("task_kind") or "") == "action_plan" and role == "narrator":
        role = "queen"
    reused = _reused_procedure_inputs(task_class=routed_task_class, user_input=user_input)
    model_constraints = _routing_model_constraints(
        task_class=routed_task_class,
        role=role,
        profile=profile,
        context=context,
    )
    return build_task_envelope(
        task_id=task_id,
        parent_task_id=parent_task_id,
        role=role,
        goal=str(user_input or "").strip()[:600],
        inputs={
            "task_class": routed_task_class,
            "classification": classification,
            "task_kind": str(profile.get("task_kind") or ""),
            "output_mode": str(profile.get("output_mode") or ""),
            "routing_profile": dict(profile),
            "reused_procedure_ids": list(reused.get("reused_procedure_ids") or []),
            "reused_procedures": list(reused.get("reused_procedures") or []),
        },
        model_constraints=model_constraints,
        latency_budget=_latency_budget_for_request(
            task_class=routed_task_class,
            role=role,
        ),
        quality_target="high" if role in {"queen", "verifier", "researcher"} else "standard",
        required_receipts=("tool_receipt", "validation_result") if role in {"coder", "verifier"} else (),
        privacy_class=str(context.get("share_scope") or "local_only"),
    )


def _reused_procedure_inputs(*, task_class: str, user_input: str) -> dict[str, Any]:
    procedures = load_procedure_shards()
    if not procedures:
        return {}
    ranked = rank_reusable_procedures(
        task_class=str(task_class or "unknown").strip().lower() or "unknown",
        query_text=user_input,
        procedures=procedures,
        limit=3,
    )
    if not ranked:
        return {}
    return {
        "reused_procedure_ids": [shard.procedure_id for shard in ranked],
        "reused_procedures": [
            {
                "procedure_id": shard.procedure_id,
                "title": shard.title,
                "task_class": shard.task_class,
                "shareability": shard.shareability,
                "success_signal": shard.success_signal,
            }
            for shard in ranked
        ],
    }


def _routing_model_constraints(
    *,
    task_class: str,
    role: str,
    profile: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    privacy_class = str(context.get("share_scope") or "local_only").strip().lower() or "local_only"
    required_locality = "local" if privacy_class == "local_private" or role in {"coder", "verifier", "memory_clerk"} else ""
    preferred_locality = "local" if role in {"coder", "verifier", "memory_clerk"} else ""
    preferred_tool_support: list[str] = []
    if role == "researcher":
        preferred_tool_support.append("web_search")
    return {
        "routing_task_kind": str(profile.get("task_kind") or "").strip(),
        "routing_output_mode": str(profile.get("output_mode") or "").strip(),
        "allow_paid_fallback": bool(profile.get("allow_paid_fallback", False)),
        "preferred_provider_role": str(profile.get("provider_role") or "auto").strip() or "auto",
        "required_locality": required_locality,
        "preferred_locality": preferred_locality,
        "prefer_structured_output": str(profile.get("output_mode") or "plain_text") != "plain_text" or role in {"coder", "verifier", "queen"},
        "prefer_long_context": role in {"queen", "researcher"} or task_class in {"research", "chat_research", "system_design"},
        "prefer_code_complex": task_class in {"debugging", "dependency_resolution", "security_hardening", "integration_orchestration"},
        "preferred_tool_support": preferred_tool_support,
        "queue_pressure_strategy": "fail_closed" if required_locality else "degrade",
    }


def _latency_budget_for_request(*, task_class: str, role: str) -> str:
    if role in {"narrator", "verifier"}:
        return "low_latency"
    if role in {"queen", "researcher"} or task_class in {"research", "chat_research", "system_design", "integration_orchestration"}:
        return "deep"
    return "balanced"


def chat_surface_execution_task_class(
    task_class: str,
    *,
    user_input: str = "",
    context: dict[str, Any] | None = None,
) -> str:
    clean_task_class = str(task_class or "unknown").strip().lower() or "unknown"
    if clean_task_class in _PLAIN_TEXT_CHAT_TASK_CLASSES:
        return clean_task_class
    if clean_task_class == "unknown":
        return "chat_conversation"
    if clean_task_class == "research":
        return "chat_research"

    context = context or {}
    topic_hints = {str(item).lower() for item in context.get("topic_hints") or []}
    lower = redact_text(user_input).lower()
    if _contains_any(lower, _BUSINESS_CHAT_MARKERS) or {"business", "pricing", "marketing", "sales"} & topic_hints:
        return "business_advisory"
    if _contains_any(lower, _FOOD_CHAT_MARKERS) or {"food", "nutrition", "meal", "diet"} & topic_hints:
        return "food_nutrition"
    if _contains_any(lower, _RELATIONSHIP_CHAT_MARKERS) or {"relationship", "dating", "intimacy"} & topic_hints:
        return "relationship_advisory"
    if _contains_any(lower, _CREATIVE_CHAT_MARKERS) or {"creative", "brainstorm", "campaign"} & topic_hints:
        return "creative_ideation"
    if _contains_any(lower, _GENERAL_ADVISORY_MARKERS):
        return "general_advisory"
    return clean_task_class
