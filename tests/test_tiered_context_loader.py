from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timezone
from unittest import mock

from apps.nulla_agent import NullaAgent
from core.final_response_store import store_final_response
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.knowledge_registry import record_remote_holder
from core.persistent_memory import (
    append_conversation_event,
    conversation_log_path,
    memory_entries_path,
    memory_path,
    session_summaries_path,
    user_heuristics_path,
)
from core.runtime_continuity import create_runtime_checkpoint, update_runtime_checkpoint
from core.task_router import classify, create_task_record
from core.tiered_context_loader import TieredContextLoader
from network.signer import get_local_peer_id
from storage.context_access_log import recent_context_access
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.shard_fetch_receipts import record_fetch_receipt
from storage.shard_reuse_outcomes import record_shard_reuse_outcomes
from storage.swarm_memory import save_sniffed_context


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _interpretation(text: str, *, confidence: float = 0.72, topics: list[str] | None = None) -> HumanInputInterpretation:
    return HumanInputInterpretation(
        raw_text=text,
        normalized_text=text,
        reconstructed_text=text,
        intent_mode="request",
        topic_hints=list(topics or []),
        reference_targets=[],
        understanding_confidence=confidence,
        quality_flags=[],
        needs_clarification=confidence < 0.45,
        turn_id=None,
    )


class TieredContextLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            existing_tables = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
            for table in (
                "local_tasks",
                "learning_shards",
                "finalized_responses",
                "dialogue_sessions",
                "dialogue_turns",
                "adaptive_lexicon",
                "knowledge_manifests",
                "knowledge_holders",
                "presence_leases",
                "index_deltas",
                "payment_status_markers",
                "context_access_log",
                "shard_fetch_receipts",
                "shard_reuse_outcomes",
                "sniffed_context",
                "runtime_tool_receipts",
                "runtime_checkpoints",
                "runtime_session_events",
                "runtime_sessions",
            ):
                if table in existing_tables:
                    conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()
        for path in (memory_path(), conversation_log_path(), memory_entries_path(), session_summaries_path(), user_heuristics_path()):
            if path.exists():
                path.unlink()
        self.loader = TieredContextLoader()
        self.persona = load_active_persona("default")
        self.session_id = f"ctx-{uuid.uuid4().hex}"

    def _insert_local_shard(self, *, problem_class: str, summary: str, resolution_pattern: list[str] | None = None) -> str:
        shard_id = f"shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        now = _now()
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, created_at, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, 'local_generated', ?, 0.92, 0.81, 0, 0, 'active', '[]', ?, NULL, '', ?, ?)
                """,
                (
                    shard_id,
                    problem_class,
                    f"sig-{uuid.uuid4().hex}",
                    summary,
                    json.dumps(resolution_pattern or ["review_problem", "choose_safe_next_step"]),
                    json.dumps({"os": "unknown", "runtime": "python", "shell": "unknown", "version_family": "unknown"}),
                    get_local_peer_id(),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return shard_id

    def test_bootstrap_only_path(self) -> None:
        task = create_task_record("quick check status")
        interpretation = _interpretation("quick check status", confidence=0.78, topics=["status"])
        result = self.loader.load(
            task=task,
            classification=classify("quick check status", context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertTrue(result.bootstrap_items)
        self.assertFalse(result.relevant_items)
        self.assertEqual(result.report.cold_tokens_used, 0)
        self.assertGreater(result.report.bootstrap_tokens_used, 0)

    def test_relevant_retrieval_under_budget(self) -> None:
        self._insert_local_shard(
            problem_class="security_hardening",
            summary="Harden local credentials and prevent password leaks from Telegram automation.",
            resolution_pattern=["identify_sensitive_surfaces", "remove_secret_exposure_paths"],
        )
        task = create_task_record("harden local credentials so passwords never leak")
        interpretation = _interpretation("harden local credentials so passwords never leak", topics=["security hardening", "password leak"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertTrue(any(item.source_type == "local_shard" for item in result.relevant_items))
        self.assertLessEqual(result.report.relevant_tokens_used, result.report.relevant_budget)

    def test_relevant_retrieval_includes_structured_runtime_tool_observation(self) -> None:
        checkpoint = create_runtime_checkpoint(
            session_id=self.session_id,
            request_text="latest qwen release notes",
            source_context={"runtime_session_id": self.session_id, "surface": "openclaw", "platform": "openclaw"},
        )
        update_runtime_checkpoint(
            checkpoint["checkpoint_id"],
            state={
                "executed_steps": [
                    {
                        "tool_name": "web.search",
                        "status": "executed",
                        "summary": "Found Qwen release notes.",
                    }
                ],
                "last_tool_response": {
                    "handled": True,
                    "ok": True,
                    "status": "executed",
                    "response_text": 'Search results for "latest qwen release notes": ...',
                    "tool_name": "web.search",
                    "details": {
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "web.search",
                            "tool_surface": "web",
                            "ok": True,
                            "status": "executed",
                            "query": "latest qwen release notes",
                            "results": [
                                {
                                    "title": "Qwen release notes",
                                    "url": "https://example.test/qwen",
                                    "snippet": "Fresh update summary",
                                }
                            ],
                        }
                    },
                },
            },
            status="completed",
        )
        task = create_task_record("tell me about the latest qwen release notes")
        interpretation = _interpretation(
            "tell me about the latest qwen release notes",
            topics=["qwen", "release notes"],
        )
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
            total_context_budget=5000,
        )

        tool_items = [item for item in result.relevant_items if item.source_type == "tool_observation"]
        self.assertTrue(tool_items)
        self.assertIn('"tool_surface": "web"', result.assembled_context())
        self.assertIn('"query": "latest qwen release notes"', result.assembled_context())
        self.assertNotIn("Real tool result from", result.assembled_context())

    def test_relevant_retrieval_uses_persistent_memory_and_prior_session_summary(self) -> None:
        append_conversation_event(
            session_id="openclaw:prior-session",
            user_input="My project uses a Telegram OpenClaw installer. Keep answers blunt and concise.",
            assistant_output="Understood.",
            source_context={"surface": "channel", "platform": "openclaw"},
        )
        append_conversation_event(
            session_id="openclaw:prior-session",
            user_input="The continuity bug happens when a new session forgets prior installer state.",
            assistant_output="I will store a session summary for continuity.",
            source_context={"surface": "channel", "platform": "openclaw"},
        )
        task = create_task_record("fix telegram installer continuity again")
        interpretation = _interpretation(
            "fix telegram installer continuity again",
            topics=["telegram", "openclaw integration"],
        )
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        source_types = {item.source_type for item in result.relevant_items}
        self.assertTrue({"runtime_memory", "session_summary"} & source_types)

    def test_relevant_retrieval_includes_inferred_user_heuristics(self) -> None:
        append_conversation_event(
            session_id="openclaw:heuristic-session",
            user_input="I am building Telegram bots in Python. Use official docs and GitHub repos first.",
            assistant_output="Understood.",
            source_context={"surface": "channel", "platform": "openclaw"},
        )
        task = create_task_record("design a telegram bot runtime")
        interpretation = _interpretation(
            "design a telegram bot runtime",
            topics=["telegram bot", "github"],
        )
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        heuristic_items = [item for item in result.relevant_items if item.source_type == "user_heuristic"]
        self.assertTrue(heuristic_items)
        self.assertTrue(any("official documentation" in item.content.lower() or "github" in item.content.lower() for item in heuristic_items))

    def test_over_budget_trimming_behavior(self) -> None:
        long_text = "context " * 500
        store_final_response(
            parent_task_id=f"task-{uuid.uuid4().hex}",
            raw=long_text,
            rendered=long_text,
            status="complete",
            confidence=0.8,
        )
        task = create_task_record("system design context trimming")
        interpretation = _interpretation("system design context trimming", topics=["swarm", "memory"])
        result = self.loader.load(
            task=task,
            classification=classify("swarm memory system design", context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
            total_context_budget=220,
        )
        self.assertTrue(result.report.items_excluded or result.report.trimming_decisions)

    def test_cold_context_blocked_by_default(self) -> None:
        store_final_response(
            parent_task_id=f"task-{uuid.uuid4().hex}",
            raw="Old archive item",
            rendered="Old archive item about earlier system state",
            status="complete",
            confidence=0.7,
        )
        task = create_task_record("design current swarm topology")
        interpretation = _interpretation("design current swarm topology", topics=["swarm"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertFalse(result.cold_items)
        self.assertFalse(result.report.cold_archive_opened)

    def test_cold_context_explicitly_allowed(self) -> None:
        store_final_response(
            parent_task_id=f"task-{uuid.uuid4().hex}",
            raw="Historical meet topology archive",
            rendered="Historical meet topology archive from an earlier run.",
            status="complete",
            confidence=0.78,
        )
        task = create_task_record("show previous archive for meet topology")
        interpretation = _interpretation("show previous archive for meet topology", topics=["archive", "meet and greet"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertTrue(result.cold_decision.allow)
        self.assertTrue(result.report.cold_archive_opened or result.cold_items)

    def test_low_confidence_relevant_retrieval_falls_back_safely(self) -> None:
        task = create_task_record("unclear thing maybe that one")
        interpretation = _interpretation("unclear thing maybe that one", confidence=0.31, topics=[])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertEqual(result.report.retrieval_confidence, "low")
        self.assertTrue(result.bootstrap_items)
        self.assertFalse(result.local_candidates)

    def test_swarm_metadata_consulted_without_auto_fetching_full_payload(self) -> None:
        record_remote_holder(
            shard_id=f"remote-{uuid.uuid4().hex}",
            holder_peer_id=f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}",
            content_hash=f"remote-{uuid.uuid4().hex}",
            version=1,
            freshness_ts=_now(),
            ttl_seconds=600,
            topic_tags=["swarm", "knowledge", "replication"],
            summary_digest="digest-swarm-knowledge",
            size_bytes=128,
            metadata={"problem_class": "system_design"},
            fetch_route={"method": "request_shard", "shard_id": "remote"},
            trust_weight=0.64,
            home_region="us",
        )
        task = create_task_record("design swarm knowledge replication")
        interpretation = _interpretation("design swarm knowledge replication", topics=["swarm", "knowledge"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertTrue(result.report.swarm_metadata_consulted)
        self.assertTrue(result.swarm_metadata)
        self.assertTrue(all(item.get("metadata_only") for item in result.swarm_metadata))

    def test_explicit_swarm_query_requests_live_remote_fetch(self) -> None:
        task = create_task_record("use swarm memory from peers for this telegram bot architecture")
        interpretation = _interpretation(
            "use swarm memory from peers for this telegram bot architecture",
            topics=["swarm memory", "telegram bot"],
        )
        with mock.patch(
            "core.tiered_context_loader.consult_relevant_swarm_metadata",
            return_value={
                "consulted": True,
                "fetched": 1,
                "items": [
                    {
                        "shard_id": "remote-shard",
                        "holder_peer_id": "peer-remote-1234567890",
                        "home_region": "eu",
                        "topic_tags": ["swarm", "telegram"],
                        "fetch_route": {"method": "request_shard"},
                        "trust_weight": 0.72,
                        "relevance_score": 2.4,
                        "problem_class": "system_design",
                        "utility_score": 0.84,
                        "quality_score": 0.78,
                        "metadata_only": False,
                        "fetched": True,
                    }
                ],
                "metadata_only": False,
            },
        ) as consult_swarm:
            result = self.loader.load(
                task=task,
                classification=classify(task.task_summary, context=interpretation.as_context()),
                interpretation=interpretation,
                persona=self.persona,
                session_id=self.session_id,
            )

        consult_swarm.assert_called_once()
        self.assertTrue(consult_swarm.call_args.kwargs["allow_fetch"])
        self.assertTrue(any(item.source_type == "swarm_remote_context" for item in result.relevant_items))

    def test_cached_remote_shard_surfaces_reuse_citation(self) -> None:
        shard_id = f"remote-cache-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        now = _now()
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, created_at, updated_at
                ) VALUES (?, 1, 'system_design', ?, ?, ?, ?, 'peer_received', ?, 0.88, 0.56, 0, 0, 'active', '[]', ?, NULL, ?, ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    "Remote swarm replication notes that were already fetched and cached locally",
                    json.dumps(["compare topology", "validate holder state"]),
                    json.dumps({"os": "unknown", "runtime": "python", "shell": "unknown", "version_family": "unknown"}),
                    f"peer-origin-{uuid.uuid4().hex}{uuid.uuid4().hex}",
                    now,
                    "signed",
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        receipt_id = record_fetch_receipt(
            shard_id=shard_id,
            source_peer_id=f"peer-holder-{uuid.uuid4().hex}{uuid.uuid4().hex}",
            source_node_id=f"peer-origin-{uuid.uuid4().hex}{uuid.uuid4().hex}",
            query_id=f"query-{uuid.uuid4().hex}",
            manifest_id=f"manifest-{uuid.uuid4().hex}",
            content_hash=f"content-{uuid.uuid4().hex}",
            version=1,
            summary_digest="digest-remote-cache",
            validation_state="signature_and_manifest_verified",
            accepted=True,
            details={"reason": "test"},
        )

        task = create_task_record("design swarm knowledge replication")
        interpretation = _interpretation("design swarm knowledge replication", topics=["swarm", "knowledge"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )

        remote_items = [item for item in result.relevant_items if item.source_type == "remote_shard_cache"]
        self.assertTrue(remote_items)
        citation = dict(remote_items[0].metadata.get("reuse_citation") or {})
        self.assertEqual(citation["receipt_id"], receipt_id)
        self.assertEqual(citation["validation_state"], "signature_and_manifest_verified")
        snippets = result.context_snippets()
        self.assertTrue(any(dict(item.get("citation") or {}).get("receipt_id") == receipt_id for item in snippets))

    def test_cached_remote_shard_surfaces_reuse_outcome_summary(self) -> None:
        shard_id = f"remote-cache-summary-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        now = _now()
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, created_at, updated_at
                ) VALUES (?, 1, 'system_design', ?, ?, ?, ?, 'peer_received', ?, 0.91, 0.61, 0, 0, 'active', '[]', ?, NULL, ?, ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    "Remote shard with proven downstream reuse",
                    json.dumps(["inspect topology", "reuse holder evidence"]),
                    json.dumps({"os": "unknown", "runtime": "python", "shell": "unknown", "version_family": "unknown"}),
                    f"peer-origin-{uuid.uuid4().hex}{uuid.uuid4().hex}",
                    now,
                    "signed",
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        receipt_id = record_fetch_receipt(
            shard_id=shard_id,
            source_peer_id=f"peer-holder-{uuid.uuid4().hex}{uuid.uuid4().hex}",
            source_node_id=f"peer-origin-{uuid.uuid4().hex}{uuid.uuid4().hex}",
            query_id=f"query-{uuid.uuid4().hex}",
            manifest_id=f"manifest-{uuid.uuid4().hex}",
            content_hash=f"content-{uuid.uuid4().hex}",
            version=1,
            summary_digest="digest-remote-cache-summary",
            validation_state="signature_and_manifest_verified",
            accepted=True,
            details={"reason": "test"},
        )
        record_shard_reuse_outcomes(
            citations=[
                {
                    "kind": "remote_shard",
                    "shard_id": shard_id,
                    "receipt_id": receipt_id,
                    "source_peer_id": "peer-holder",
                    "source_node_id": "peer-origin",
                    "manifest_id": "manifest-1",
                    "content_hash": "content-1",
                    "validation_state": "signature_and_manifest_verified",
                }
            ],
            task_id="task-reuse-summary",
            session_id=self.session_id,
            task_class="system_design",
            response_class="generic_conversation",
            success=True,
            durable=True,
        )

        task = create_task_record("design swarm knowledge replication")
        interpretation = _interpretation("design swarm knowledge replication", topics=["swarm", "knowledge"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )

        remote_items = [item for item in result.relevant_items if item.source_type == "remote_shard_cache"]
        self.assertTrue(remote_items)
        citation = dict(remote_items[0].metadata.get("reuse_citation") or {})
        reuse_outcomes = dict(citation.get("reuse_outcomes") or {})
        self.assertEqual(reuse_outcomes["total_count"], 1)
        self.assertEqual(reuse_outcomes["success_count"], 1)
        self.assertEqual(reuse_outcomes["durable_count"], 1)
        self.assertEqual(reuse_outcomes["last_receipt_id"], receipt_id)

    def test_shared_swarm_context_is_reused_in_live_retrieval(self) -> None:
        save_sniffed_context(
            parent_peer_id=f"peer-{uuid.uuid4().hex}",
            prompt_data={"task_summary": "telegram installer continuity bug"},
            result_data={"summary": "Persist session summaries and replay relevant memory into chat."},
        )
        task = create_task_record("how do we fix telegram installer continuity")
        interpretation = _interpretation(
            "how do we fix telegram installer continuity",
            topics=["telegram", "installer"],
        )
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
        )
        self.assertTrue(any(item.source_type == "swarm_context" for item in result.relevant_items))

    def test_archive_path_uses_cold_hook_only_when_justified(self) -> None:
        task = create_task_record("run safe local status check")
        interpretation = _interpretation("run safe local status check", topics=["status"])
        with mock.patch("core.tiered_context_loader.lookup_cold_archive_candidates", return_value=[]) as cold_lookup:
            self.loader.load(
                task=task,
                classification=classify(task.task_summary, context=interpretation.as_context()),
                interpretation=interpretation,
                persona=self.persona,
                session_id=self.session_id,
            )
            cold_lookup.assert_not_called()

        archive_task = create_task_record("show previous archive for swarm topology")
        archive_interpretation = _interpretation("show previous archive for swarm topology", topics=["archive", "swarm"])
        with mock.patch("core.tiered_context_loader.lookup_cold_archive_candidates", return_value=[]) as cold_lookup:
            self.loader.load(
                task=archive_task,
                classification=classify(archive_task.task_summary, context=archive_interpretation.as_context()),
                interpretation=archive_interpretation,
                persona=self.persona,
                session_id=self.session_id,
            )
            cold_lookup.assert_called_once()

    def test_prompt_assembly_report_records_included_and_excluded_items(self) -> None:
        long_text = "history " * 400
        store_final_response(
            parent_task_id=f"task-{uuid.uuid4().hex}",
            raw=long_text,
            rendered=long_text,
            status="complete",
            confidence=0.9,
        )
        task = create_task_record("show previous archive history")
        interpretation = _interpretation("show previous archive history", topics=["archive"])
        result = self.loader.load(
            task=task,
            classification=classify(task.task_summary, context=interpretation.as_context()),
            interpretation=interpretation,
            persona=self.persona,
            session_id=self.session_id,
            total_context_budget=240,
        )
        self.assertTrue(result.report.items_included)
        self.assertTrue(result.report.items_excluded or result.report.trimming_decisions)
        self.assertTrue(recent_context_access(limit=5))

    def test_existing_local_first_agent_flow_still_works(self) -> None:
        agent = NullaAgent(backend_name="test-backend", device="local-test", persona_id="default")
        agent.start()
        with mock.patch("apps.nulla_agent.orchestrate_parent_task", return_value=None), mock.patch(
            "apps.nulla_agent.request_relevant_holders", return_value=[]
        ), mock.patch("apps.nulla_agent.dispatch_query_shard", return_value=None):
            result = agent.run_once("pls harden tg setup so no passwords leak")
        self.assertIn("response", result)
        self.assertIn("prompt_assembly_report", result)
        self.assertIn("total_context_budget", result["prompt_assembly_report"])


if __name__ == "__main__":
    unittest.main()
