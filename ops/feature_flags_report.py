from __future__ import annotations

from pathlib import Path

from core.feature_flags import get_feature_flags
from core.runtime_paths import docs_path, project_path


MODULE_STATES: dict[str, str] = {
    "network/stream_transport.py": "implemented",
    "network/chunk_protocol.py": "implemented",
    "network/transfer_manager.py": "implemented",
    "network/stun_client.py": "partial",
    "network/nat_probe.py": "implemented",
    "network/hole_punch.py": "implemented",
    "network/bootstrap_node.py": "implemented",
    "network/knowledge_models.py": "implemented",
    "network/knowledge_router.py": "implemented",
    "network/presence_router.py": "implemented",
    "network/relay_fallback.py": "implemented",
    "adapters/base_adapter.py": "implemented",
    "adapters/cloud_fallback_provider.py": "implemented",
    "adapters/local_model_path_adapter.py": "implemented",
    "adapters/local_qwen_provider.py": "implemented",
    "adapters/local_subprocess_adapter.py": "implemented",
    "adapters/openai_compatible_adapter.py": "implemented",
    "adapters/optional_transformers_adapter.py": "implemented",
    "core/cache_freshness_policy.py": "implemented",
    "core/cache_invalidation.py": "implemented",
    "core/candidate_knowledge_lane.py": "implemented",
    "core/channel_gateway.py": "implemented",
    "core/curiosity_policy.py": "implemented",
    "core/curiosity_roamer.py": "implemented",
    "core/media_analysis_pipeline.py": "implemented",
    "core/media_evidence_ranker.py": "implemented",
    "core/media_ingestion.py": "implemented",
    "core/social_source_policy.py": "implemented",
    "core/source_credibility.py": "implemented",
    "core/verdict_engine.py": "implemented",
    "core/api_write_auth.py": "implemented",
    "core/internal_message_schema.py": "implemented",
    "core/input_normalizer.py": "implemented",
    "core/memory_first_router.py": "implemented",
    "core/model_failover.py": "implemented",
    "core/model_health.py": "implemented",
    "core/model_output_contracts.py": "implemented",
    "core/model_registry.py": "implemented",
    "core/model_selection_policy.py": "implemented",
    "core/model_teacher_pipeline.py": "implemented",
    "core/model_trust.py": "implemented",
    "core/mobile_companion_view.py": "implemented",
    "core/nulla_user_summary.py": "implemented",
    "core/output_validator.py": "implemented",
    "core/prompt_normalizer.py": "implemented",
    "core/source_reputation.py": "implemented",
    "core/human_input_adapter.py": "implemented",
    "core/bootstrap_context.py": "implemented",
    "core/brain_hive_guard.py": "implemented",
    "core/brain_hive_models.py": "implemented",
    "core/brain_hive_service.py": "implemented",
    "core/cold_context_gate.py": "implemented",
    "core/context_budgeter.py": "implemented",
    "core/context_relevance_ranker.py": "implemented",
    "core/meet_and_greet_models.py": "implemented",
    "core/meet_and_greet_replication.py": "implemented",
    "core/meet_and_greet_service.py": "implemented",
    "core/knowledge_advertiser.py": "implemented",
    "core/knowledge_fetcher.py": "implemented",
    "core/knowledge_freshness.py": "implemented",
    "core/knowledge_possession_challenge.py": "implemented",
    "core/knowledge_registry.py": "implemented",
    "core/knowledge_replication.py": "implemented",
    "core/prompt_assembly_report.py": "implemented",
    "core/runtime_guard.py": "implemented",
    "core/tiered_context_loader.py": "implemented",
    "core/evidence_scorer.py": "implemented",
    "core/conflict_classifier.py": "implemented",
    "core/challenge_engine.py": "implemented",
    "core/proof_of_execution.py": "implemented",
    "core/dispute_engine.py": "implemented",
    "core/appeal_queue.py": "implemented",
    "core/review_quorum.py": "implemented",
    "core/evidence_bundle.py": "implemented",
    "core/context_manifest.py": "implemented",
    "core/provenance_store.py": "implemented",
    "core/task_state_machine.py": "implemented",
    "core/retry_policy.py": "implemented",
    "core/timeout_policy.py": "implemented",
    "storage/cas.py": "implemented",
    "storage/chunk_store.py": "implemented",
    "storage/blob_index.py": "implemented",
    "storage/brain_hive_store.py": "implemented",
    "storage/manifest_store.py": "implemented",
    "storage/event_log.py": "implemented",
    "storage/event_hash_chain.py": "implemented",
    "storage/dialogue_memory.py": "implemented",
    "storage/context_access_log.py": "implemented",
    "storage/curiosity_state.py": "implemented",
    "storage/media_evidence_log.py": "implemented",
    "storage/knowledge_index.py": "implemented",
    "storage/knowledge_manifests.py": "implemented",
    "storage/knowledge_possession_store.py": "implemented",
    "storage/meet_node_registry.py": "implemented",
    "storage/payment_status.py": "implemented",
    "storage/replica_table.py": "implemented",
    "sandbox/job_runner.py": "implemented",
    "sandbox/resource_limits.py": "implemented",
    "sandbox/network_guard.py": "implemented",
    "sandbox/container_adapter.py": "implemented",
    "ops/feature_flags_report.py": "implemented",
    "ops/benchmark_caps.py": "implemented",
    "ops/context_budget_report.py": "implemented",
    "ops/curiosity_report.py": "implemented",
    "ops/morning_after_audit_report.py": "implemented",
    "ops/mobile_channel_preflight_report.py": "implemented",
    "ops/overnight_readiness_report.py": "implemented",
    "ops/replication_audit.py": "implemented",
    "ops/nulla_user_report.py": "implemented",
    "ops/swarm_knowledge_report.py": "implemented",
    "ops/swarm_trace_report.py": "implemented",
    "ops/integration_smoke_test.py": "implemented",
    "ops/chaos_test.py": "implemented",
    "docs/MEET_AND_GREET_PREFLIGHT.md": "implemented",
    "docs/MEET_AND_GREET_SERVER_ARCHITECTURE.md": "implemented",
    "docs/MEET_AND_GREET_API_CONTRACT.md": "implemented",
    "docs/MEET_AND_GREET_GLOBAL_TOPOLOGY.md": "implemented",
    "docs/OVERNIGHT_SOAK_RUNBOOK.md": "implemented",
    "docs/MODEL_PROVIDER_POLICY.md": "implemented",
    "apps/meet_and_greet_node.py": "partial",
    "apps/meet_and_greet_server.py": "partial",
    "core/credit_ledger.py": "simulated",
    "core/dna_payment_bridge.py": "simulated",
    "core/credit_dex.py": "simulated",
    "network/dht.py": "partial",
    "network/transport.py": "partial",
    "apps/nulla_daemon.py": "partial",
    "core/consensus_validator.py": "partial",
}


def _grouped_rows() -> dict[str, list[str]]:
    groups = {"implemented": [], "partial": [], "simulated": [], "planned": []}
    for module, state in sorted(MODULE_STATES.items()):
        target = state if state in groups else "planned"
        exists = Path(project_path(module)).exists()
        suffix = "" if exists else " (missing)"
        groups[target].append(f"- `{module}`{suffix}")
    groups["implemented"].extend(
        [f"- `{flag.name}`: {flag.reason}" for flag in get_feature_flags() if flag.state == "implemented"]
    )
    groups["partial"].extend(
        [f"- `{flag.name}`: {flag.reason}" for flag in get_feature_flags() if flag.state == "partial"]
    )
    groups["simulated"].extend(
        [f"- `{flag.name}`: {flag.reason}" for flag in get_feature_flags() if flag.state == "simulated"]
    )
    planned_notes = [
        "- `public_trustless_payments`: deferred until replay protection, reconciliation, and idempotent settlement are hardened.",
        "- `internet_scale_data_plane`: deferred beyond local and LAN rollout until relay/TURN-grade routing is proven.",
    ]
    groups["planned"].extend(planned_notes)
    return groups


def build_markdown() -> str:
    groups = _grouped_rows()
    lines = [
        "# IMPLEMENTATION STATUS",
        "",
        "Decentralized NULLA remains usable as a standalone local feature on-device.",
        "Liquefy/OpenClaw and Solana sidecars remain optional integration points, not mandatory runtime dependencies.",
        "",
    ]
    for heading in ("implemented", "partial", "simulated", "planned"):
        lines.append(f"## {heading.title()}")
        lines.extend(groups[heading] or ["- None"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_report() -> str:
    target = docs_path("IMPLEMENTATION_STATUS.md")
    target.write_text(build_markdown(), encoding="utf-8")
    return str(target)


def main() -> int:
    print(write_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
