from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core import audit_logger, policy_engine
from core.model_teacher_pipeline import ModelTeacherPipeline
from core.task_capsule import TaskCapsule
from network.assist_models import TaskResult
from network.signer import get_local_peer_id as local_peer_id


@dataclass
class WorkerOutcome:
    result: TaskResult
    accepted_scope: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm(text: str) -> str:
    return " ".join((text or "").strip().split())


def _tokenize(text: str) -> set[str]:
    chars = []
    for ch in text.lower():
        chars.append(ch if ch.isalnum() else " ")
    return {t for t in "".join(chars).split() if len(t) > 2}


def _hash_result(summary: str, steps: list[str], evidence: list[str]) -> str:
    raw = json.dumps(
        {
            "summary": summary,
            "steps": steps,
            "evidence": evidence,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _truncate_to_budget(summary: str, evidence: list[str], steps: list[str], max_bytes: int) -> tuple[str, list[str], list[str]]:
    # simple budget-aware truncation
    if max_bytes <= 0:
        return "", [], []

    payload = {
        "summary": summary,
        "evidence": evidence,
        "steps": steps,
    }

    safety_limit = 512
    iterations = 0
    while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > max_bytes:
        iterations += 1
        if iterations > safety_limit:
            break
        before = (len(summary), len(evidence), len(steps))
        if len(evidence) > 1:
            evidence = evidence[:-1]
        elif len(steps) > 1:
            steps = steps[:-1]
        elif len(summary) > 128:
            summary = summary[: max(128, len(summary) - 64)].rstrip()
        else:
            break
        after = (len(summary), len(evidence), len(steps))
        if after == before:
            break
        payload = {
            "summary": summary,
            "evidence": evidence,
            "steps": steps,
        }

    return summary, evidence, steps


def _rank_inputs(inputs: list[str], constraints: list[str]) -> list[str]:
    constraint_tokens = set()
    for c in constraints:
        constraint_tokens |= _tokenize(c)

    scored: list[tuple[float, str]] = []
    for item in inputs:
        toks = _tokenize(item)
        overlap = len(toks & constraint_tokens)
        brevity_bonus = max(0.0, 1.0 - (len(item) / 180.0))
        score = (overlap * 2.0) + brevity_bonus
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored]


def _build_generic_steps(task_type: str, problem_class: str) -> list[str]:
    if task_type == "research":
        return [
            "identify_relevant_signal",
            "compare_safe_options",
            "summarize_best_tradeoff",
        ]
    if task_type == "classification":
        return [
            "inspect_abstract_inputs",
            "map_problem_to_class",
            "return_classification",
        ]
    if task_type == "ranking":
        return [
            "score_candidate_options",
            "rank_candidates",
            "return_ranked_recommendation",
        ]
    if task_type == "validation":
        return [
            "check_internal_consistency",
            "verify_constraint_fit",
            "return_validation_summary",
        ]
    if task_type == "planning":
        return [
            "decompose_subproblem",
            "sequence_safe_next_steps",
            "return_abstract_plan",
        ]
    if task_type == "code_reasoning":
        return [
            "identify_general_code_issue",
            "propose_safe_abstract_fix_path",
            "return_non_executable_guidance",
        ]
    if task_type == "documentation":
        return [
            "extract_key_points",
            "organize_clarified_summary",
            "return_draft_output",
        ]
    return [
        "review_task_capsule",
        "apply_safe_reasoning",
        "return_structured_result",
    ]


def _helper_model_profile(task_type: str) -> tuple[str, str, str]:
    # task_kind, output_mode, result_type
    mapping = {
        "research": ("summarization", "summary_block", "research_summary"),
        "classification": ("classification", "json_object", "classification"),
        "ranking": ("action_plan", "action_plan", "ranking"),
        "validation": ("action_plan", "action_plan", "validation"),
        "planning": ("action_plan", "action_plan", "plan_suggestion"),
        "code_reasoning": ("action_plan", "action_plan", "plan_suggestion"),
        "documentation": ("summarization", "summary_block", "draft_output"),
    }
    return mapping.get(task_type, ("normalization_assist", "summary_block", "plan_suggestion"))


def _extract_steps_from_text(text: str) -> list[str]:
    steps: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        else:
            match = re.match(r"^(\d+)[\.\)]\s*(.+)$", line)
            if match:
                line = match.group(2).strip()
        if not line:
            continue
        if len(steps) < 8:
            steps.append(line[:160])
    return steps


def _build_model_prompt(capsule: TaskCapsule, *, problem_class: str, abstract_inputs: list[str], constraints: list[str]) -> str:
    payload = {
        "task_id": capsule.task_id,
        "task_type": capsule.task_type,
        "subtask_type": capsule.subtask_type,
        "problem_class": problem_class,
        "summary": capsule.summary,
        "abstract_inputs": abstract_inputs[:8],
        "known_constraints": constraints[:8],
        "allowed_operations": list(capsule.allowed_operations),
        "forbidden_operations": list(capsule.forbidden_operations),
    }
    return (
        "You are a NULLA helper node. Produce concise, non-executable, policy-safe reasoning.\n"
        "Never output commands, shell snippets, credentials, or destructive instructions.\n"
        "Return only abstract analysis and safe guidance that fits the task capsule.\n"
        f"Capsule:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _run_model_reasoning(
    capsule: TaskCapsule,
    *,
    problem_class: str,
    abstract_inputs: list[str],
    constraints: list[str],
) -> dict[str, Any] | None:
    task_kind, output_mode, result_type = _helper_model_profile(capsule.task_type)
    pipeline = ModelTeacherPipeline()
    candidate = pipeline.run(
        task_kind=task_kind,
        prompt=_build_model_prompt(
            capsule,
            problem_class=problem_class,
            abstract_inputs=abstract_inputs,
            constraints=constraints,
        ),
        context={
            "task_id": capsule.task_id,
            "problem_class": problem_class,
            "subtask_type": capsule.subtask_type,
        },
        trace_id=capsule.task_id,
        output_mode=output_mode,
    )
    if candidate is None:
        return None

    raw_text = (candidate.output_text or "").strip()
    if not raw_text:
        return None
    steps = _extract_steps_from_text(raw_text)
    summary = raw_text.splitlines()[0].strip() if raw_text.splitlines() else raw_text
    summary = summary[:640] if summary else f"Helper reasoning completed for {problem_class}."
    evidence = [f"model:{candidate.source_model_tag}", f"task_kind:{task_kind}", f"output_mode:{output_mode}"]
    evidence.extend(abstract_inputs[:2])
    evidence.extend(constraints[:2])
    return {
        "summary": summary,
        "steps": steps or _build_generic_steps(capsule.task_type, problem_class),
        "evidence": evidence[:12],
        "confidence": max(0.0, min(1.0, float(candidate.confidence))),
        "result_type": result_type,
        "risk_flags": [],
        "model_source": candidate.source_model_tag,
    }


def _run_template_reasoning(
    capsule: TaskCapsule,
    *,
    problem_class: str,
    abstract_inputs: list[str],
    constraints: list[str],
) -> dict[str, Any]:
    steps = _build_generic_steps(capsule.task_type, problem_class)
    evidence: list[str] = []
    summary = ""
    confidence = 0.55
    result_type = "plan_suggestion"
    risk_flags: list[str] = []

    if capsule.task_type == "research":
        ranked = _rank_inputs(abstract_inputs, constraints)
        top = ranked[:3] if ranked else ["no_strong_input_signal"]
        evidence = top + constraints[:2]
        summary = (
            f"Research capsule reviewed for {problem_class}. "
            f"Top signal: {top[0]}. Best path is the safest option that satisfies the known constraints."
        )
        confidence = min(0.78, 0.58 + (0.04 * min(len(abstract_inputs), 4)))
        result_type = "research_summary"

    elif capsule.task_type == "classification":
        evidence = [problem_class] + abstract_inputs[:2]
        summary = (
            f"This subtask maps most closely to {problem_class}. "
            f"The abstract inputs align with that class and do not require direct execution."
        )
        confidence = 0.72
        result_type = "classification"

    elif capsule.task_type == "ranking":
        ranked = _rank_inputs(abstract_inputs, constraints)
        evidence = ranked[:5]
        top = ranked[0] if ranked else "no_rankable_candidate"
        summary = (
            f"Ranked the available abstract candidates. "
            f"Highest-fit option: {top}."
        )
        confidence = 0.74 if ranked else 0.46
        result_type = "ranking"

    elif capsule.task_type == "validation":
        evidence = constraints[:4] + abstract_inputs[:2]
        summary = (
            f"Validation complete for {problem_class}. "
            f"No direct-scope violations were requested inside the capsule."
        )
        confidence = 0.76
        result_type = "validation"

    elif capsule.task_type == "documentation":
        evidence = abstract_inputs[:3]
        summary = (
            f"Drafted a safe condensed summary for {problem_class} "
            f"using only sanitized context."
        )
        confidence = 0.66
        result_type = "draft_output"

    else:
        evidence = abstract_inputs[:3] + constraints[:2]
        summary = (
            f"Built an abstract plan for {problem_class}. "
            f"Only non-executable guidance is included."
        )
        confidence = 0.68
        result_type = "plan_suggestion"

    return {
        "summary": summary,
        "steps": steps,
        "evidence": evidence,
        "confidence": confidence,
        "result_type": result_type,
        "risk_flags": risk_flags,
        "model_source": None,
    }


def run_task_capsule(capsule: TaskCapsule, *, helper_agent_id: str | None = None) -> WorkerOutcome:
    helper_agent_id = helper_agent_id or local_peer_id()

    # hard scope check: v1 helper only supports pure reasoning work
    allowed = set(capsule.allowed_operations)
    forbidden = set(capsule.forbidden_operations)
    if "execute" not in forbidden or "access_db" not in forbidden or "call_shell" not in forbidden:
        raise ValueError("Capsule scope too permissive for local helper worker.")

    if not allowed.issubset({"reason", "research", "compare", "rank", "summarize", "validate", "draft"}):
        raise ValueError("Capsule requested unsupported operation.")

    ctx = capsule.sanitized_context
    problem_class = _norm(ctx.problem_class)
    abstract_inputs = [_norm(x) for x in ctx.abstract_inputs]
    constraints = [_norm(x) for x in ctx.known_constraints]

    model_result: dict[str, Any] | None = None
    try:
        model_result = _run_model_reasoning(
            capsule,
            problem_class=problem_class,
            abstract_inputs=abstract_inputs,
            constraints=constraints,
        )
    except Exception as exc:
        audit_logger.log(
            "helper_model_reasoning_error",
            target_id=capsule.task_id,
            target_type="helper_task",
            trace_id=capsule.task_id,
            details={"error": str(exc), "task_type": capsule.task_type},
        )

    reasoning = model_result or _run_template_reasoning(
        capsule,
        problem_class=problem_class,
        abstract_inputs=abstract_inputs,
        constraints=constraints,
    )
    summary = str(reasoning["summary"])
    evidence = [str(item) for item in list(reasoning.get("evidence") or [])]
    steps = [str(item) for item in list(reasoning.get("steps") or [])]
    confidence = float(reasoning.get("confidence") or 0.55)
    result_type = str(reasoning.get("result_type") or "plan_suggestion")
    risk_flags = [str(item) for item in list(reasoning.get("risk_flags") or [])]
    model_source = str(reasoning.get("model_source") or "")
    if model_source:
        audit_logger.log(
            "helper_model_reasoning_used",
            target_id=capsule.task_id,
            target_type="helper_task",
            trace_id=capsule.task_id,
            details={"task_type": capsule.task_type, "model_source": model_source, "confidence": confidence},
        )

    summary, evidence, steps = _truncate_to_budget(
        summary=summary,
        evidence=evidence,
        steps=steps,
        max_bytes=capsule.max_response_bytes,
    )

    result_hash = _hash_result(summary, steps, evidence)

    result = TaskResult(
        result_id=str(uuid.uuid4()),
        task_id=capsule.task_id,
        helper_agent_id=helper_agent_id,
        result_type=result_type,
        summary=summary,
        confidence=max(0.0, min(1.0, confidence)),
        evidence=evidence,
        abstract_steps=steps,
        risk_flags=risk_flags,
        result_hash=result_hash,
        timestamp=_utcnow(),
    )

    # Phase 25: Cooperative Swarm Learning
    if getattr(capsule, "learning_allowed", False) and bool(
        policy_engine.get("learning.enable_cooperative_swarm_memory", True)
    ):
        try:
            from storage.swarm_memory import save_sniffed_context
            save_sniffed_context(
                parent_peer_id=capsule.parent_agent_id,
                prompt_data=capsule.sanitized_context.model_dump(mode="json"),
                result_data={
                    "summary": summary,
                    "evidence": evidence,
                    "abstract_steps": steps
                }
            )
        except Exception as e:
            # We don't fail the task if local learning ingestion fails
            import logging
            logging.error(f"Failed to ingest learned context: {e}")
            audit_logger.log(
                "helper_learning_ingestion_failed",
                target_id=capsule.task_id,
                target_type="helper_task",
                trace_id=capsule.task_id,
                details={"error": str(e)},
            )

    return WorkerOutcome(
        result=result,
        accepted_scope=True,
    )
