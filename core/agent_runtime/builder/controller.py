from __future__ import annotations

from typing import Any


def workspace_build_observations(
    *,
    target: dict[str, str],
    write_results: list[dict[str, Any]],
    write_failures: list[str],
    verification: dict[str, Any] | None,
    sources: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "channel": "workspace_build",
        "target": {
            "platform": str(target.get("platform") or "").strip(),
            "language": str(target.get("language") or "").strip(),
            "root_dir": str(target.get("root_dir") or "").strip(),
        },
        "written_file_count": len(write_results),
        "written_files": [str(item.get("path") or "").strip() for item in write_results[:8]],
        "write_failures": [str(item).strip() for item in write_failures[:4] if str(item).strip()],
        "verification": {
            "status": str((verification or {}).get("status") or "").strip(),
            "ok": bool((verification or {}).get("ok", False)),
            "response_text": str((verification or {}).get("response_text") or "").strip(),
        },
        "sources": [
            {
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "label": str(item.get("label") or "").strip(),
            }
            for item in list(sources or [])[:4]
        ],
    }


def workspace_build_degraded_response(
    *,
    target: dict[str, str],
    write_results: list[dict[str, Any]],
    write_failures: list[str],
    verification: dict[str, Any] | None,
) -> str:
    root_dir = str(target.get("root_dir") or "the workspace").strip()
    if write_results:
        status = str((verification or {}).get("status") or "").strip()
        if status == "executed":
            verification_line = "Verification passed." if bool((verification or {}).get("ok", False)) else (
                f"Verification failed: {str((verification or {}).get('response_text') or '').strip()}"
            )
        elif status == "skipped":
            verification_line = "Verification was skipped for this scaffold type."
        else:
            verification_line = "Verification did not run."
        failure_line = ""
        if write_failures:
            failure_line = f" {len(write_failures)} write operation(s) failed."
        return (
            f"I completed the workspace build actions under `{root_dir}`, but I couldn't produce a clean final summary. "
            f"{verification_line}{failure_line}"
        ).strip()
    if write_failures:
        return f"I attempted the workspace build actions for `{root_dir}`, but the file writes did not complete cleanly.".strip()
    return "I couldn't complete the workspace build actions cleanly in this run."


def run_bounded_builder_loop(
    agent: Any,
    *,
    task: Any,
    session_id: str,
    effective_input: str,
    task_class: str,
    source_context: dict[str, object] | None,
    initial_payloads: list[dict[str, Any]],
    plan_tool_workflow_fn: Any,
    execute_tool_intent_fn: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], str, Any | None]:
    loop_source_context = agent._merge_runtime_source_contexts({}, dict(source_context or {}))
    executed_steps: list[dict[str, Any]] = []
    pending_payloads = [dict(item) for item in list(initial_payloads or []) if isinstance(item, dict)]
    stop_reason = ""
    failed_execution = None
    max_steps = 6

    while len(executed_steps) < max_steps:
        if pending_payloads:
            tool_payload = dict(pending_payloads.pop(0))
        else:
            workflow_decision = plan_tool_workflow_fn(
                user_text=effective_input,
                task_class=task_class,
                executed_steps=executed_steps,
                source_context=loop_source_context,
            )
            if workflow_decision.handled and workflow_decision.stop_after:
                stop_reason = str(workflow_decision.reason or "stop_after").strip()
                break
            if not workflow_decision.handled or not workflow_decision.next_payload:
                stop_reason = str(workflow_decision.reason or "no_followup_plan").strip()
                break
            tool_payload = dict(workflow_decision.next_payload or {})

        execution = execute_tool_intent_fn(
            tool_payload,
            task_id=task.task_id,
            session_id=session_id,
            source_context=loop_source_context,
            hive_activity_tracker=agent.hive_activity_tracker,
            public_hive_bridge=agent.public_hive_bridge,
        )
        if not execution.handled:
            stop_reason = "tool_not_handled"
            break

        executed_steps.append(
            agent._builder_controller_step_record(
                execution=execution,
                tool_payload=tool_payload,
            )
        )
        loop_source_context = agent._append_tool_result_to_source_context(
            loop_source_context,
            execution=execution,
            tool_name=str(getattr(execution, "tool_name", "") or tool_payload.get("intent") or ""),
        )
        if str(getattr(execution, "mode", "") or "").strip() != "tool_executed":
            failed_execution = execution
            stop_reason = f"{getattr(execution, 'mode', '') or 'tool_failed'!s}:{getattr(execution, 'status', '') or 'failed'!s}"
            break

    if not stop_reason and len(executed_steps) >= max_steps:
        stop_reason = "step_budget_exhausted"
    if not stop_reason:
        stop_reason = "bounded_loop_complete"
    return executed_steps, loop_source_context, stop_reason, failed_execution


def maybe_run_builder_controller(
    agent: Any,
    *,
    task: Any,
    effective_input: str,
    classification: dict[str, Any],
    interpretation: Any,
    web_notes: list[dict[str, Any]],
    session_id: str,
    source_context: dict[str, object] | None,
    render_capability_truth_response_fn: Any,
    load_active_persona_fn: Any,
) -> dict[str, Any] | None:
    source_context = dict(source_context or {})
    profile = agent._builder_controller_profile(
        effective_input=effective_input,
        classification=classification,
        interpretation=interpretation,
        source_context=source_context,
    )
    if not profile.get("should_handle"):
        return None

    if not profile.get("supported"):
        report = dict(profile.get("gap_report") or {})
        return agent._fast_path_result(
            session_id=session_id,
            user_input=effective_input,
            response=render_capability_truth_response_fn(report),
            confidence=0.82 if str(report.get("support_level") or "").strip() == "partial" else 0.74,
            source_context=source_context,
            reason="builder_capability_gap",
        )

    target = dict(profile.get("target") or {})
    mode = str(profile.get("mode") or "workflow").strip()
    if mode == "scaffold" and not web_notes:
        web_notes = agent._collect_live_web_notes(
            task_id=task.task_id,
            query_text=effective_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
        )
    initial_payloads, sources = agent._builder_initial_payloads(
        mode=mode,
        target=target,
        user_request=effective_input,
        web_notes=web_notes,
        initial_payloads=list(profile.get("initial_payloads") or []),
    )
    if mode == "scaffold" and not initial_payloads:
        report = agent._builder_support_gap_report(
            source_context=source_context,
            reason=(
                "That request did not resolve to a supported scaffold target. "
                "The real scaffold lane here is still limited to Telegram or Discord bot builds."
            ),
        )
        return agent._fast_path_result(
            session_id=session_id,
            user_input=effective_input,
            response=render_capability_truth_response_fn(report),
            confidence=0.78,
            source_context=source_context,
            reason="builder_capability_gap",
        )

    executed_steps, loop_source_context, stop_reason, failed_execution = agent._run_bounded_builder_loop(
        task=task,
        session_id=session_id,
        effective_input=effective_input,
        task_class=str(classification.get("task_class") or "unknown"),
        source_context=source_context,
        initial_payloads=initial_payloads,
    )
    final_status = "failed" if failed_execution is not None else "completed"
    artifacts = agent._builder_controller_artifacts(
        executed_steps=executed_steps,
        stop_reason=stop_reason,
    )
    observations = agent._builder_controller_observations(
        mode=mode,
        target=target,
        executed_steps=executed_steps,
        stop_reason=stop_reason,
        sources=sources,
        final_status=final_status,
        artifacts=artifacts,
    )
    degraded = agent._builder_controller_degraded_response(
        target=target,
        executed_steps=executed_steps,
        stop_reason=stop_reason,
        failed_execution=failed_execution,
        effective_input=effective_input,
        session_id=session_id,
        artifacts=artifacts,
    )
    workflow_summary = agent._builder_controller_workflow_summary(
        mode=mode,
        executed_steps=executed_steps,
        stop_reason=stop_reason,
        artifacts=artifacts,
    )
    builder_details = {
        "mode": mode,
        "step_count": len(executed_steps),
        "stop_reason": stop_reason,
        "tool_steps": [str(step.get("tool_name") or "").strip() for step in executed_steps],
        "artifacts": artifacts,
        "executed_steps": executed_steps,
        "observations": observations,
    }
    direct_response = agent._builder_controller_direct_response(
        effective_input=effective_input,
        executed_steps=executed_steps,
    )
    if direct_response is not None:
        result = agent._fast_path_result(
            session_id=session_id,
            user_input=effective_input,
            response=direct_response,
            confidence=0.95,
            source_context=loop_source_context,
            reason="builder_controller_direct_response",
            runtime_event_details={"builder_controller": builder_details},
        )
        result["mode"] = "tool_failed" if failed_execution is not None else ("tool_executed" if executed_steps else "advice_only")
        result["workflow_summary"] = workflow_summary
        result["details"] = {"builder_controller": builder_details}
        return result
    if mode == "workflow" and executed_steps and failed_execution is None:
        response_text = agent._append_builder_artifact_citations(
            agent._render_tool_loop_response(
                final_message=degraded,
                executed_steps=executed_steps,
                include_step_summary=True,
            ),
            artifacts=artifacts,
        )
        result = agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=response_text,
            confidence=0.9,
            source_context=loop_source_context,
            reason="builder_controller_workflow_response",
            success=True,
            details={"builder_controller": builder_details},
            mode_override="tool_executed",
            task_outcome="success",
            workflow_summary=workflow_summary,
        )
        result["details"] = {"builder_controller": builder_details}
        return result
    if agent._is_chat_truth_surface(loop_source_context):
        result = agent._chat_surface_model_wording_result(
            session_id=session_id,
            user_input=effective_input,
            source_context=loop_source_context,
            persona=load_active_persona_fn(agent.persona_id),
            interpretation=interpretation,
            task_class=str(classification.get("task_class") or "integration_orchestration"),
            response_class=agent.ResponseClass.GENERIC_CONVERSATION,
            reason="builder_controller_model_wording",
            model_input=agent._chat_surface_builder_model_input(
                user_input=effective_input,
                observations=observations,
            ),
            fallback_response=degraded,
            tool_backing_sources=agent._builder_controller_backing_sources(executed_steps),
            response_postprocessor=lambda text: agent._append_builder_artifact_citations(text, artifacts=artifacts),
        )
        result["mode"] = "tool_failed" if failed_execution is not None else ("tool_executed" if executed_steps else "advice_only")
        result["workflow_summary"] = workflow_summary
        result["details"] = {"builder_controller": builder_details}
        return result

    response_text = degraded
    if executed_steps:
        response_text = agent._append_builder_artifact_citations(
            agent._render_tool_loop_response(
                final_message=degraded,
                executed_steps=executed_steps,
                include_step_summary=True,
            ),
            artifacts=artifacts,
        )
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=effective_input,
        response=response_text,
        confidence=0.84 if executed_steps and failed_execution is None else 0.58,
        source_context=loop_source_context,
        reason="builder_controller_pipeline",
        success=bool(executed_steps) and failed_execution is None,
        details={"builder_controller": builder_details},
        mode_override="tool_failed" if failed_execution is not None else ("tool_executed" if executed_steps else "advice_only"),
        task_outcome="failed" if failed_execution is not None else ("success" if executed_steps else "advice_only"),
        workflow_summary=workflow_summary,
    )


def workspace_build_verification(
    *,
    target: dict[str, str],
    source_context: dict[str, object],
    execute_runtime_tool_fn: Any,
) -> dict[str, Any] | None:
    language = str(target.get("language") or "")
    root_dir = str(target.get("root_dir") or "").rstrip("/")
    if language != "python" or not root_dir:
        return {"status": "skipped", "ok": False, "response_text": "Verification skipped for non-Python scaffold."}
    execution = execute_runtime_tool_fn(
        "sandbox.run_command",
        {"command": f"python3 -m compileall -q {root_dir}/src", "_trusted_local_only": True},
        source_context=source_context,
    )
    if execution is None:
        return {"status": "not_run", "ok": False, "response_text": "Verification did not run."}
    return {
        "status": execution.status,
        "ok": execution.ok,
        "response_text": execution.response_text,
        "details": dict(execution.details),
    }


def workspace_build_response(
    *,
    target: dict[str, str],
    write_results: list[dict[str, Any]],
    write_failures: list[str],
    verification: dict[str, Any] | None,
    sources: list[dict[str, str]],
) -> str:
    lines = [
        f"Wrote a {target['platform']} {target['language']} scaffold under `{target['root_dir']}`."
        if target["platform"] != "generic"
        else f"Wrote a generic {target['language']} workspace starter under `{target['root_dir']}`."
    ]
    if write_results:
        lines.append("Files written:")
        lines.extend(f"- {item['path']}" for item in write_results[:8])
    if sources:
        lines.append("Sources used:")
        lines.extend(f"- {item['title']} [{item['url']}]" for item in sources[:3])
    verification_status = str((verification or {}).get("status") or "")
    verification_text = str((verification or {}).get("response_text") or "").strip()
    if verification_status == "executed":
        lines.append("Verification:")
        lines.append(f"- {verification_text}")
    elif verification_status == "skipped":
        lines.append("Verification skipped for this scaffold type.")
    if write_failures:
        lines.append("Write failures:")
        lines.extend(f"- {item}" for item in write_failures[:4])
    return "\n".join(lines)
