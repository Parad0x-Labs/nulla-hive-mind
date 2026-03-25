from __future__ import annotations

from typing import Any


def _dispatch_background_swarm_query(
    *,
    task: Any,
    classification: dict[str, Any],
    ranked: list[Any],
    context_result: Any,
    request_relevant_holders_fn: Any,
    dispatch_query_shard_fn: Any,
    build_generalized_query_fn: Any,
    audit_logger_module: Any,
) -> None:
    if ranked and float(getattr(context_result, "retrieval_confidence_score", 0.0) or 0.0) >= 0.65:
        return
    try:
        query = build_generalized_query_fn(task, classification)
        request_relevant_holders_fn(
            classification.get("task_class", "unknown"),
            task.task_summary,
            query_id=query["query_id"],
            limit=3,
        )
        dispatch_query_shard_fn(query, limit=5)
    except Exception as exc:
        audit_logger_module.log(
            "swarm_query_dispatch_error",
            target_id=task.task_id,
            target_type="task",
            details={"error": str(exc)},
        )


def _build_media_candidate(media_analysis: Any) -> dict[str, Any] | None:
    if not getattr(media_analysis, "analysis_text", ""):
        return None
    return {
        "summary": str(media_analysis.analysis_text or "").splitlines()[0][:220] or "Media evidence review",
        "resolution_pattern": [],
        "score": 0.58,
        "source_type": "multimodal_candidate",
        "source_node_id": media_analysis.provider_id,
        "provider_name": media_analysis.provider_id,
        "model_name": media_analysis.provider_id,
        "candidate_id": media_analysis.candidate_id,
    }


def execute_grounded_turn(
    agent: Any,
    *,
    task: Any,
    effective_input: str,
    classification: dict[str, Any],
    interpreted: Any,
    persona: Any,
    session_id: str,
    source_context: dict[str, object] | None,
    adapt_user_input_fn: Any,
    ingest_media_evidence_fn: Any,
    build_media_context_snippets_fn: Any,
    orchestrate_parent_task_fn: Any,
    build_plan_fn: Any,
    render_response_fn: Any,
    explicit_planner_style_requested_fn: Any,
    should_use_planner_renderer_fn: Any,
    request_relevant_holders_fn: Any,
    dispatch_query_shard_fn: Any,
    build_generalized_query_fn: Any,
    feedback_engine_module: Any,
    policy_engine_module: Any,
    from_task_result_fn: Any,
    append_conversation_event_fn: Any,
    audit_logger_module: Any,
) -> dict[str, Any]:
    surface = str((source_context or {}).get("surface", "cli")).lower()
    is_chat_surface = surface in {"channel", "openclaw", "api"}
    context_result = agent.context_loader.load(
        task=task,
        classification=classification,
        interpretation=interpreted,
        persona=persona,
        session_id=session_id,
    )
    ranked = context_result.local_candidates
    curiosity_result = None
    curiosity_plan_candidates: list[dict[str, Any]] = []
    curiosity_context_snippets: list[dict[str, Any]] = []
    if agent._should_frontload_curiosity(
        query_text=effective_input,
        classification=classification,
        interpretation=interpreted,
    ):
        curiosity_result = agent.curiosity.maybe_roam(
            task=task,
            user_input=effective_input,
            classification=classification,
            interpretation=interpreted,
            context_result=context_result,
            session_id=session_id,
        )
        curiosity_plan_candidates, curiosity_context_snippets = agent._curiosity_candidate_evidence(
            curiosity_result.candidate_ids
        )
    tool_execution = agent._maybe_execute_model_tool_intent(
        task=task,
        effective_input=effective_input,
        classification=classification,
        interpretation=interpreted,
        context_result=context_result,
        persona=persona,
        session_id=session_id,
        source_context=source_context,
        surface=surface,
    )
    if tool_execution is not None:
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=tool_execution["response"],
            confidence=float(tool_execution["confidence"]),
            source_context=source_context,
            reason=f"model_tool_intent_{tool_execution['status']}",
            success=bool(tool_execution["success"]),
            details=dict(tool_execution["details"]),
            mode_override=str(tool_execution["mode"]),
            task_outcome=str(tool_execution["task_outcome"]),
            learned_plan=tool_execution.get("learned_plan"),
            workflow_summary=str(tool_execution["workflow_summary"]),
        )

    orchestrate_parent_task_fn(
        parent_task_id=task.task_id,
        user_input=effective_input,
        classification=classification,
        environment_tags={
            "os": task.environment_os,
            "shell": task.environment_shell,
            "runtime": task.environment_runtime,
            "version_family": task.environment_version_hint,
        },
        exclude_host_group_hint_hash=None,
    )

    routing_classification, routing_profile = agent._model_routing_profile(
        user_input=effective_input,
        classification=classification,
        interpretation=interpreted,
        source_context=source_context,
    )
    adaptive_research = agent._collect_adaptive_research(
        task_id=task.task_id,
        query_text=effective_input,
        classification=routing_classification,
        interpretation=interpreted,
        source_context=source_context,
    )
    model_interpretation = interpreted
    adaptive_web_notes = [dict(note) for note in list(adaptive_research.notes or []) if isinstance(note, dict)]
    if is_chat_surface and (
        adaptive_research.enabled or adaptive_research.tool_gap_note or adaptive_research.admitted_uncertainty
    ):
        model_interpretation = adapt_user_input_fn(
            agent._chat_surface_adaptive_research_model_input(
                user_input=effective_input,
                task_class=str(routing_classification.get("task_class") or "unknown"),
                research_result=adaptive_research,
            ),
            session_id=session_id,
        )
    model_source_context = dict(source_context or {})
    if routing_profile.get("task_envelope"):
        model_source_context["task_envelope"] = dict(routing_profile.get("task_envelope") or {})
    if routing_profile.get("task_role"):
        model_source_context["task_role"] = str(routing_profile.get("task_role") or "")
    if routing_classification.get("task_class"):
        model_source_context["task_class"] = str(routing_classification.get("task_class") or "")
    model_execution = agent.memory_router.resolve(
        task=task,
        classification=routing_classification,
        interpretation=model_interpretation,
        context_result=context_result,
        persona=persona,
        force_model=is_chat_surface,
        surface=surface,
        source_context=model_source_context,
    )
    model_candidate = model_execution.as_plan_candidate()
    media_source_context = dict(source_context or {})
    if is_chat_surface and "fetch_text_references" not in media_source_context:
        media_source_context["fetch_text_references"] = True
    media_evidence = ingest_media_evidence_fn(
        task_id=task.task_id,
        trace_id=task.task_id,
        user_input=effective_input,
        source_context=media_source_context,
    )
    media_analysis = agent.media_pipeline.analyze(
        task_id=task.task_id,
        task_summary=task.task_summary,
        evidence_items=media_evidence,
    )
    media_context_snippets = build_media_context_snippets_fn(media_analysis.evidence_items or media_evidence)
    media_candidate = _build_media_candidate(media_analysis)

    web_notes = list(adaptive_web_notes)
    if not web_notes:
        web_notes = agent._collect_live_web_notes(
            task_id=task.task_id,
            query_text=effective_input,
            classification=classification,
            interpretation=interpreted,
            source_context=source_context,
        )
    web_plan_candidates = agent._web_note_plan_candidates(
        query_text=effective_input,
        classification=classification,
        web_notes=web_notes,
    )

    _dispatch_background_swarm_query(
        task=task,
        classification=classification,
        ranked=ranked,
        context_result=context_result,
        request_relevant_holders_fn=request_relevant_holders_fn,
        dispatch_query_shard_fn=dispatch_query_shard_fn,
        build_generalized_query_fn=build_generalized_query_fn,
        audit_logger_module=audit_logger_module,
    )

    evidence = {
        "candidates": sorted(
            curiosity_plan_candidates + web_plan_candidates,
            key=lambda item: float(item.get("score") or 0.0),
            reverse=True,
        )[:3],
        "local_candidates": ranked[:3],
        "swarm_candidates": context_result.swarm_metadata[:3],
        "model_candidates": [candidate for candidate in [model_candidate, media_candidate] if candidate],
        "context_snippets": curiosity_context_snippets + context_result.context_snippets() + media_context_snippets,
        "assembled_context": context_result.assembled_context(),
        "prompt_assembly_report": context_result.report.to_dict(),
        "model_execution": {
            "source": model_execution.source,
            "provider_id": model_execution.provider_id,
            "used_model": model_execution.used_model,
            "cache_hit": model_execution.cache_hit,
            "candidate_id": model_execution.candidate_id,
            "trust_score": model_execution.trust_score,
            "validation_state": model_execution.validation_state,
        },
        "media_analysis": {
            "used_provider": media_analysis.used_provider,
            "provider_id": media_analysis.provider_id,
            "candidate_id": media_analysis.candidate_id,
            "reason": media_analysis.reason,
            "evidence_count": len(media_analysis.evidence_items or media_evidence),
        },
        "adaptive_research": adaptive_research.to_dict(),
        "external_media_evidence": media_analysis.evidence_items or media_evidence,
        "web_notes": web_notes,
    }

    plan = build_plan_fn(
        task=task,
        classification=classification,
        evidence=evidence,
        persona=persona,
    )
    gate = agent._default_gate(plan, classification)

    planner_style_requested = explicit_planner_style_requested_fn(effective_input)
    planner_renderer_allowed = should_use_planner_renderer_fn(
        surface=surface,
        output_mode=str(routing_profile.get("output_mode") or ""),
        user_input=effective_input,
    )
    model_final_text = (
        agent._chat_surface_model_final_text(model_execution)
        if is_chat_surface
        else agent._model_final_response_text(model_execution)
    )
    model_final_answer_hit = bool(model_final_text)
    rendered_via = "model_final_wording"
    response_reason = "grounded_model_response"

    if planner_renderer_allowed and (not is_chat_surface or bool(model_execution.used_model)):
        response = render_response_fn(
            plan,
            gate,
            persona,
            input_interpretation=interpreted,
            prompt_assembly_report=context_result.report,
            surface=surface,
            allow_planner_style=planner_style_requested,
        )
        rendered_via = "reasoning_engine"
        response_reason = "grounded_plan_response"
        model_final_answer_hit = False
    elif model_final_answer_hit:
        response = model_final_text
    elif is_chat_surface:
        response = agent._chat_surface_honest_degraded_response(
            model_execution,
            user_input=effective_input,
            interpretation=interpreted,
        )
        rendered_via = "honest_degraded_chat"
        response_reason = "chat_model_unavailable_degraded"
    else:
        response = render_response_fn(
            plan,
            gate,
            persona,
            input_interpretation=interpreted,
            prompt_assembly_report=context_result.report,
            surface=surface,
            allow_planner_style=planner_style_requested,
        )
        rendered_via = "reasoning_engine"
        response_reason = "grounded_plan_response"

    execution_result = {"mode": "advice_only"}
    outcome = feedback_engine_module.evaluate_outcome(task, plan, gate, execution_result)
    feedback_engine_module.apply(task, evidence, outcome)

    if curiosity_result is None:
        curiosity_result = agent.curiosity.maybe_roam(
            task=task,
            user_input=effective_input,
            classification=classification,
            interpretation=interpreted,
            context_result=context_result,
            session_id=session_id,
        )
    workflow_summary = agent._task_workflow_summary(
        classification=classification,
        context_result=context_result,
        model_execution=evidence["model_execution"],
        media_analysis=evidence["media_analysis"],
        curiosity_result=curiosity_result.to_dict(),
        gate_mode=gate.mode,
    )
    if outcome.is_success and outcome.is_durable:
        shard = from_task_result_fn(task, plan, outcome)
        if policy_engine_module.validate_learned_shard(shard):
            agent._store_local_shard(
                shard,
                origin_task_id=task.task_id,
                origin_session_id=session_id,
            )

    public_export = agent._maybe_publish_public_task(
        task=task,
        classification=classification,
        assistant_response=response,
        session_id=session_id,
    )
    topic_id = str((public_export or {}).get("topic_id") or "").strip()
    if topic_id:
        agent.hive_activity_tracker.note_watched_topic(session_id=session_id, topic_id=topic_id)
    turn_result = agent._turn_result(
        response,
        agent._grounded_response_class(gate=gate, classification=classification),
        workflow_summary=workflow_summary,
        debug_origin="grounded_plan",
        allow_planner_style=planner_style_requested,
    )
    agent._apply_interaction_transition(session_id, turn_result)
    response = agent._decorate_chat_response(
        turn_result,
        session_id=session_id,
        source_context=source_context,
    )
    agent._emit_chat_truth_metrics(
        task_id=task.task_id,
        reason=response_reason,
        response_text=response,
        response_class=turn_result.response_class.value,
        source_context=source_context,
        rendered_via=rendered_via,
        fast_path_hit=False,
        model_inference_used=bool(model_execution.used_model),
        model_final_answer_hit=model_final_answer_hit,
        model_execution_source=str(model_execution.source or ""),
        tool_backing_sources=[
            source
            for source in (
                "web_lookup" if web_notes else "",
                "media_analysis" if media_analysis.used_provider else "",
            )
            if source
        ],
    )

    audit_logger_module.log(
        "agent_run_once_complete",
        target_id=task.task_id,
        target_type="task",
        details={
            "mode": gate.mode,
            "confidence": plan.confidence,
            "swarm_candidates_present": len(ranked),
            "understanding_confidence": interpreted.understanding_confidence,
            "input_quality_flags": interpreted.quality_flags,
            "context_retrieval_confidence": context_result.report.retrieval_confidence,
            "context_budget_used": context_result.report.total_tokens_used(),
            "model_execution_source": model_execution.source,
            "model_provider_id": model_execution.provider_id,
            "media_analysis_reason": media_analysis.reason,
            "media_evidence_count": len(media_analysis.evidence_items or media_evidence),
            "curiosity_mode": curiosity_result.mode,
            "curiosity_reason": curiosity_result.reason,
            "curiosity_candidate_count": len(curiosity_result.candidate_ids),
            "adaptive_research_enabled": adaptive_research.enabled,
            "adaptive_research_reason": adaptive_research.reason,
            "adaptive_research_strategy": adaptive_research.strategy,
            "adaptive_research_actions": list(adaptive_research.actions_taken),
            "adaptive_research_uncertainty": adaptive_research.admitted_uncertainty,
            "source_surface": (source_context or {}).get("surface"),
            "source_platform": (source_context or {}).get("platform"),
        },
    )

    append_conversation_event_fn(
        session_id=session_id,
        user_input=effective_input,
        assistant_output=response,
        source_context=source_context,
    )
    agent._emit_runtime_event(
        source_context,
        event_type="task_completed",
        message=f"Completed task with final response: {agent._runtime_preview(response)}",
        task_id=task.task_id,
        task_class=str(classification.get("task_class") or "unknown"),
    )
    agent._finalize_runtime_checkpoint(
        source_context,
        status="completed",
        final_response=response,
    )

    return {
        "task_id": task.task_id,
        "response": response,
        "mode": gate.mode,
        "confidence": plan.confidence,
        "understanding_confidence": interpreted.understanding_confidence,
        "interpreted_input": effective_input,
        "topic_hints": interpreted.topic_hints,
        "prompt_assembly_report": context_result.report.to_dict(),
        "model_execution": evidence["model_execution"],
        "media_analysis": evidence["media_analysis"],
        "research_controller": adaptive_research.to_dict(),
        "curiosity": curiosity_result.to_dict(),
        "backend": agent.backend_name,
        "device": agent.device,
        "session_id": session_id,
        "source_context": dict(source_context or {}),
        "workflow_summary": workflow_summary,
        "response_class": turn_result.response_class.value,
    }
