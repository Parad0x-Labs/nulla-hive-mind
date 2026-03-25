from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.runtime_execution_tools import RuntimeExecutionResult, execute_runtime_tool
from core.runtime_tool_contracts import runtime_tool_contract_map

from .resource_scheduler import evaluate_task_envelope_capacity, schedule_task_envelopes
from .result_merge import merge_task_results
from .role_contracts import get_role_contract
from .task_envelope import TaskEnvelopeV1, task_envelope_from_dict
from .task_graph import TaskGraph


@dataclass(frozen=True)
class EnvelopeExecutionResult:
    envelope: TaskEnvelopeV1
    ok: bool
    status: str
    output_text: str
    receipts: tuple[dict[str, Any], ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def merge_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.envelope.task_id,
            "ok": self.ok,
            "status": self.status,
            "text": self.output_text,
            "score": float(self.details.get("score") or (1.0 if self.ok else 0.0)),
            "role": self.envelope.role,
            "receipts": [dict(item) for item in self.receipts],
            "details": dict(self.details or {}),
        }


def execute_task_envelope(
    envelope: TaskEnvelopeV1,
    *,
    workspace_root: str | None = None,
    session_id: str | None = None,
    source_context: dict[str, Any] | None = None,
    graph: TaskGraph | None = None,
    runtime_tool_executor: Callable[[str, dict[str, Any], dict[str, Any] | None], RuntimeExecutionResult] | None = None,
    child_executor: Callable[[TaskEnvelopeV1], EnvelopeExecutionResult] | None = None,
) -> EnvelopeExecutionResult:
    active_graph = graph or TaskGraph()
    if active_graph.get(envelope.task_id) is None:
        active_graph.add_task(envelope)
    active_graph.mark_status(envelope.task_id, "running")
    if envelope.role == "queen":
        result = _execute_queen_envelope(
            envelope,
            graph=active_graph,
            workspace_root=workspace_root,
            session_id=session_id,
            source_context=source_context,
            runtime_tool_executor=runtime_tool_executor,
            child_executor=child_executor,
        )
    else:
        result = _execute_worker_envelope(
            envelope,
            workspace_root=workspace_root,
            session_id=session_id,
            source_context=source_context,
            runtime_tool_executor=runtime_tool_executor,
        )
    active_graph.mark_status(envelope.task_id, "completed" if result.ok else "failed", result=result.merge_payload())
    if "graph" in result.details:
        result.details["graph"] = _graph_snapshot(active_graph)
    return result


def _execute_worker_envelope(
    envelope: TaskEnvelopeV1,
    *,
    workspace_root: str | None,
    session_id: str | None,
    source_context: dict[str, Any] | None,
    runtime_tool_executor: Callable[[str, dict[str, Any], dict[str, Any] | None], RuntimeExecutionResult] | None,
) -> EnvelopeExecutionResult:
    contract = get_role_contract(envelope.role)
    capacity_state = evaluate_task_envelope_capacity(envelope)
    if capacity_state.availability_state == "blocked":
        reason = ", ".join(capacity_state.notes) or "provider capacity gate blocked this task"
        return EnvelopeExecutionResult(
            envelope=envelope,
            ok=False,
            status="capacity_blocked",
            output_text=(
                f"{envelope.role} envelope `{envelope.task_id}` is blocked by provider-capacity policy: {reason}."
            ),
            details={
                "capacity_state": capacity_state.to_dict(),
                "steps": [],
                "role_contract": contract.role,
            },
        )
    steps = [dict(step) for step in list(envelope.inputs.get("runtime_tools") or []) if isinstance(step, dict)]
    if not steps:
        return EnvelopeExecutionResult(
            envelope=envelope,
            ok=False,
            status="missing_runtime_tools",
            output_text=f"{envelope.role} envelope `{envelope.task_id}` has no runtime tool steps.",
            details={"steps": []},
        )

    execute_tool = runtime_tool_executor or _runtime_tool_executor
    receipts: list[dict[str, Any]] = []
    step_results: list[dict[str, Any]] = []
    step_results_by_id: dict[str, dict[str, Any]] = {}
    active_context = {
        **dict(source_context or {}),
        "workspace": workspace_root or str((source_context or {}).get("workspace") or ""),
        "session_id": str(session_id or envelope.task_id),
        "task_id": envelope.task_id,
        "task_role": envelope.role,
        "task_class": str(envelope.inputs.get("task_class") or ""),
        "task_envelope": envelope.to_dict(),
    }
    for index, step in enumerate(steps):
        step_id = str(step.get("step_id") or f"step-{index + 1}").strip()
        if not step_id:
            return EnvelopeExecutionResult(
                envelope=envelope,
                ok=False,
                status="invalid_step_id",
                output_text=f"{envelope.role} envelope `{envelope.task_id}` produced an empty runtime step id.",
                receipts=tuple(receipts),
                details={
                    "failed_step": index,
                    "role_contract": contract.role,
                    "step_results": step_results,
                },
            )
        if step_id in step_results_by_id:
            return EnvelopeExecutionResult(
                envelope=envelope,
                ok=False,
                status="duplicate_step_id",
                output_text=(
                    f"{envelope.role} envelope `{envelope.task_id}` reused runtime step id `{step_id}`, "
                    "which makes step references ambiguous."
                ),
                receipts=tuple(receipts),
                details={
                    "failed_step": index,
                    "failed_step_id": step_id,
                    "role_contract": contract.role,
                    "step_results": step_results,
                },
            )
        intent = str(step.get("intent") or "").strip()
        raw_arguments = dict(step.get("arguments") or {})
        arguments, reference_error = _resolve_step_arguments(raw_arguments, step_results_by_id=step_results_by_id)
        if reference_error is not None:
            return EnvelopeExecutionResult(
                envelope=envelope,
                ok=False,
                status="unresolved_step_reference",
                output_text=reference_error["message"],
                receipts=tuple(receipts),
                details={
                    "failed_step": index,
                    "failed_step_id": step_id,
                    "failed_intent": intent,
                    "reference_error": reference_error,
                    "role_contract": contract.role,
                    "step_results": step_results,
                },
            )
        permission_error = _check_step_permission(envelope, intent=intent)
        if permission_error is not None:
            return EnvelopeExecutionResult(
                envelope=envelope,
                ok=False,
                status=permission_error["status"],
                output_text=permission_error["message"],
                receipts=tuple(receipts),
                details={
                    "failed_step": index,
                    "failed_intent": intent,
                    "role_contract": contract.role,
                    "step_results": step_results,
                },
            )
        runtime_result = execute_tool(intent, arguments, active_context)
        step_payload = {
            "step_id": step_id,
            "intent": intent,
            "ok": runtime_result.ok,
            "status": runtime_result.status,
            "response_text": runtime_result.response_text,
            "arguments": dict(arguments or {}),
            "details": dict(runtime_result.details or {}),
        }
        step_results.append(step_payload)
        step_results_by_id[step_id] = step_payload
        if "observation" in runtime_result.details:
            receipts.append(
                {
                    "receipt_type": "tool_receipt",
                    "step_id": step_id,
                    "intent": intent,
                    "status": runtime_result.status,
                    "observation": dict(runtime_result.details.get("observation") or {}),
                }
            )
        contract_map = runtime_tool_contract_map()
        tool_contract = contract_map.get(intent)
        if tool_contract is not None and tool_contract.capability_id == "workspace.validate":
            receipts.append(
                {
                    "receipt_type": "validation_result",
                    "step_id": step_id,
                    "intent": intent,
                    "status": runtime_result.status,
                    "ok": runtime_result.ok,
                    "returncode": runtime_result.details.get("returncode"),
                }
            )
        if not runtime_result.ok:
            return EnvelopeExecutionResult(
                envelope=envelope,
                ok=False,
                status=runtime_result.status,
                output_text=runtime_result.response_text,
                receipts=tuple(receipts),
                details={
                    "failed_step": index,
                    "failed_intent": intent,
                    "step_results": step_results,
                },
            )

    missing_receipts = _missing_required_receipts(envelope, receipts)
    if missing_receipts:
        return EnvelopeExecutionResult(
            envelope=envelope,
            ok=False,
            status="missing_required_receipts",
            output_text=(
                f"{envelope.role} envelope `{envelope.task_id}` completed its steps but did not emit "
                f"required receipts: {', '.join(missing_receipts)}."
            ),
            receipts=tuple(receipts),
            details={
                "missing_receipts": missing_receipts,
                "step_results": step_results,
            },
        )

    final_response = str(step_results[-1].get("response_text") or f"{envelope.role} envelope completed.").strip()
    return EnvelopeExecutionResult(
        envelope=envelope,
        ok=True,
        status="completed",
        output_text=final_response,
        receipts=tuple(receipts),
        details={
            "score": 1.0,
            "capacity_state": capacity_state.to_dict(),
            "step_results": step_results,
        },
    )


def _execute_queen_envelope(
    envelope: TaskEnvelopeV1,
    *,
    graph: TaskGraph,
    workspace_root: str | None,
    session_id: str | None,
    source_context: dict[str, Any] | None,
    runtime_tool_executor: Callable[[str, dict[str, Any], dict[str, Any] | None], RuntimeExecutionResult] | None,
    child_executor: Callable[[TaskEnvelopeV1], EnvelopeExecutionResult] | None,
) -> EnvelopeExecutionResult:
    children = _child_envelopes(envelope)
    if not children:
        return EnvelopeExecutionResult(
            envelope=envelope,
            ok=False,
            status="missing_subtasks",
            output_text=f"queen envelope `{envelope.task_id}` has no child envelopes to coordinate.",
            details={"graph": _graph_snapshot(graph)},
        )
    for child in children:
        if graph.get(child.task_id) is None:
            graph.add_task(child)

    scheduled, dependency_error = _schedule_child_envelopes(children)
    if dependency_error is not None:
        return EnvelopeExecutionResult(
            envelope=envelope,
            ok=False,
            status="dependency_blocked",
            output_text=dependency_error["message"],
            details={
                "graph": _graph_snapshot(graph),
                "dependency_error": dependency_error,
            },
        )
    ordered_children = {child.task_id: child for child in children}
    child_results: list[EnvelopeExecutionResult] = []
    child_result_map: dict[str, EnvelopeExecutionResult] = {}
    execute_child = child_executor or (
        lambda child: execute_task_envelope(
            child,
            workspace_root=workspace_root,
            session_id=session_id,
            source_context=source_context,
            graph=graph,
            runtime_tool_executor=runtime_tool_executor,
            child_executor=child_executor,
        )
    )
    for scheduled_child in scheduled:
        child = ordered_children[scheduled_child.task_id]
        blocked_dependencies = [
            dependency
            for dependency in _child_dependencies(child)
            if dependency in child_result_map and not child_result_map[dependency].ok
        ]
        if blocked_dependencies:
            child_result = EnvelopeExecutionResult(
                envelope=child,
                ok=False,
                status="dependency_failed",
                output_text=(
                    f"{child.role} envelope `{child.task_id}` is blocked because "
                    f"dependency execution failed: {', '.join(blocked_dependencies)}."
                ),
                details={"blocked_by": blocked_dependencies},
            )
        else:
            child_result = execute_child(child)
        graph.mark_status(child.task_id, "completed" if child_result.ok else "failed", result=child_result.merge_payload())
        child_results.append(child_result)
        child_result_map[child.task_id] = child_result

    merged = merge_task_results(envelope, [item.merge_payload() for item in child_results])
    ok = bool(merged.get("ok", False))
    if "winner" in merged:
        output_text = str(merged["winner"].get("text") or "").strip()
    else:
        output_text = str(merged.get("text") or "").strip()
    if not output_text:
        output_text = "queen envelope completed merge." if ok else "queen envelope failed to merge child results."
    return EnvelopeExecutionResult(
        envelope=envelope,
        ok=ok,
        status="completed" if ok else "merge_failed",
        output_text=output_text,
        receipts=tuple({"receipt_type": "merge_result", "strategy": merged.get("strategy"), "ok": ok} for _ in [0]),
        details={
            "graph": _graph_snapshot(graph),
            "scheduled_children": [item.task_id for item in scheduled],
            "scheduled_details": [item.to_dict() for item in scheduled],
            "child_results": [item.merge_payload() for item in child_results],
            "merged_result": merged,
            "score": float(merged.get("winner", {}).get("score") or (1.0 if ok else 0.0)),
        },
    )


def _child_envelopes(envelope: TaskEnvelopeV1) -> list[TaskEnvelopeV1]:
    out: list[TaskEnvelopeV1] = []
    for item in list(envelope.inputs.get("subtasks") or []):
        if isinstance(item, TaskEnvelopeV1):
            out.append(item)
            continue
        if isinstance(item, dict):
            payload = dict(item)
            payload.setdefault("parent_task_id", envelope.task_id)
            out.append(task_envelope_from_dict(payload))
    return out


def _child_dependencies(envelope: TaskEnvelopeV1) -> list[str]:
    return [
        str(item).strip()
        for item in list(envelope.inputs.get("depends_on") or [])
        if str(item).strip()
    ]


def _schedule_child_envelopes(children: list[TaskEnvelopeV1]) -> tuple[list[Any], dict[str, Any] | None]:
    scheduled = schedule_task_envelopes(children)
    by_id = {child.task_id: child for child in children}
    unresolved = {item.task_id for item in scheduled}
    ordered: list[Any] = []
    while unresolved:
        progressed = False
        for item in scheduled:
            if item.task_id not in unresolved:
                continue
            child = by_id[item.task_id]
            dependencies = _child_dependencies(child)
            unknown = [dependency for dependency in dependencies if dependency not in by_id]
            if unknown:
                return [], {
                    "task_id": child.task_id,
                    "dependencies": dependencies,
                    "missing_dependencies": unknown,
                    "message": (
                        f"queen envelope dependency graph is invalid for `{child.task_id}`: "
                        f"missing child dependencies {', '.join(unknown)}."
                    ),
                }
            if any(dependency in unresolved for dependency in dependencies):
                continue
            ordered.append(item)
            unresolved.remove(item.task_id)
            progressed = True
        if progressed:
            continue
        blocked = sorted(unresolved)
        return [], {
            "dependencies": {task_id: _child_dependencies(by_id[task_id]) for task_id in blocked},
            "message": (
                "queen envelope dependency graph is cyclic or unschedulable for child tasks: "
                f"{', '.join(blocked)}."
            ),
        }
    return ordered, None


def _check_step_permission(envelope: TaskEnvelopeV1, *, intent: str) -> dict[str, str] | None:
    contract_map = runtime_tool_contract_map()
    tool_contract = contract_map.get(intent)
    if tool_contract is None:
        return {
            "status": "unsupported_intent",
            "message": f"{envelope.role} envelope `{envelope.task_id}` requested unwired runtime intent `{intent}`.",
        }
    if tool_contract.capability_id not in envelope.tool_permissions:
        return {
            "status": "permission_denied",
            "message": (
                f"{envelope.role} envelope `{envelope.task_id}` is not allowed to run `{intent}` "
                f"because `{tool_contract.capability_id}` is missing from its tool permissions."
            ),
        }
    if tool_contract.side_effect_class in {"read_only", "validation_command"}:
        return None
    if tool_contract.side_effect_class not in envelope.allowed_side_effects:
        return {
            "status": "side_effect_denied",
            "message": (
                f"{envelope.role} envelope `{envelope.task_id}` is not allowed to trigger "
                f"`{tool_contract.side_effect_class}` side effects via `{intent}`."
            ),
        }
    return None


def _missing_required_receipts(envelope: TaskEnvelopeV1, receipts: list[dict[str, Any]]) -> list[str]:
    seen = {str(item.get("receipt_type") or "").strip() for item in receipts if str(item.get("receipt_type") or "").strip()}
    return [item for item in envelope.required_receipts if item not in seen]


def _graph_snapshot(graph: TaskGraph) -> list[dict[str, Any]]:
    return [
        {
            "task_id": node.envelope.task_id,
            "parent_task_id": node.envelope.parent_task_id,
            "role": node.envelope.role,
            "status": node.status,
            "children": sorted(node.children),
        }
        for node in graph.nodes()
    ]


def _runtime_tool_executor(intent: str, arguments: dict[str, Any], source_context: dict[str, Any] | None) -> RuntimeExecutionResult:
    result = execute_runtime_tool(intent, arguments, source_context=source_context)
    if result is None:
        return RuntimeExecutionResult(
            handled=True,
            ok=False,
            status="unsupported_intent",
            response_text=f"Runtime intent `{intent}` is not wired.",
            details={},
        )
    return result


def _resolve_step_arguments(
    value: Any,
    *,
    step_results_by_id: dict[str, dict[str, Any]],
    location: str = "arguments",
) -> tuple[Any, dict[str, Any] | None]:
    if isinstance(value, dict):
        reference_step = str(value.get("$from_step") or "").strip()
        if reference_step:
            return _resolve_step_reference(value, step_results_by_id=step_results_by_id, location=location)
        resolved: dict[str, Any] = {}
        for key, item in value.items():
            child_value, child_error = _resolve_step_arguments(
                item,
                step_results_by_id=step_results_by_id,
                location=f"{location}.{key}",
            )
            if child_error is not None:
                return None, child_error
            resolved[str(key)] = child_value
        return resolved, None
    if isinstance(value, list):
        resolved_items: list[Any] = []
        for index, item in enumerate(value):
            child_value, child_error = _resolve_step_arguments(
                item,
                step_results_by_id=step_results_by_id,
                location=f"{location}[{index}]",
            )
            if child_error is not None:
                return None, child_error
            resolved_items.append(child_value)
        return resolved_items, None
    return value, None


def _resolve_step_reference(
    reference: dict[str, Any],
    *,
    step_results_by_id: dict[str, dict[str, Any]],
    location: str,
) -> tuple[Any, dict[str, Any] | None]:
    step_id = str(reference.get("$from_step") or "").strip()
    source = step_results_by_id.get(step_id)
    if source is None:
        return None, {
            "location": location,
            "step_id": step_id,
            "path": str(reference.get("$path") or "observation").strip(),
            "message": f"Runtime step reference at `{location}` points to unknown step `{step_id}`.",
        }
    require_single_match = bool(reference.get("$require_single_match"))
    observation = dict(source.get("details") or {}).get("observation")
    if require_single_match and isinstance(observation, dict):
        match_count = int(observation.get("match_count") or 0)
        if match_count != 1:
            return None, {
                "location": location,
                "step_id": step_id,
                "path": str(reference.get("$path") or "observation").strip(),
                "message": (
                    f"Runtime step reference at `{location}` is ambiguous because step `{step_id}` "
                    f"returned {match_count} matches instead of exactly one."
                ),
            }
    path = str(reference.get("$path") or "observation").strip() or "observation"
    resolved = _lookup_reference_value(source, path)
    if resolved is None or (isinstance(resolved, (str, list, dict)) and not resolved):
        if "$default" in reference:
            return reference.get("$default"), None
        return None, {
            "location": location,
            "step_id": step_id,
            "path": path,
            "message": (
                f"Runtime step reference at `{location}` could not resolve `{path}` from step `{step_id}`."
            ),
        }
    return resolved, None


def _lookup_reference_value(source: dict[str, Any], path: str) -> Any:
    observation = dict((source.get("details") or {}).get("observation") or {})
    if str(observation.get("intent") or "").strip() in {"workspace.search_text", "workspace.symbol_search"}:
        matches = [dict(item) for item in list(observation.get("matches") or []) if isinstance(item, dict)]
        primary = dict((matches[:1] or [{}])[0] or {})
        if "primary_path" not in observation and str(primary.get("path") or "").strip():
            observation["primary_path"] = str(primary.get("path") or "").strip()
        if "primary_line" not in observation and str(primary.get("line") or "").strip():
            observation["primary_line"] = int(primary.get("line") or 0)
    current: Any = {
        "step_id": source.get("step_id"),
        "intent": source.get("intent"),
        "status": source.get("status"),
        "response_text": source.get("response_text"),
        "arguments": dict(source.get("arguments") or {}),
        "details": dict(source.get("details") or {}),
        "observation": observation,
    }
    for raw_part in [part for part in str(path or "").split(".") if part]:
        if isinstance(current, dict):
            current = current.get(raw_part)
            continue
        if isinstance(current, list) and raw_part.isdigit():
            index = int(raw_part)
            if 0 <= index < len(current):
                current = current[index]
                continue
        return None
    return current
