from __future__ import annotations

import unittest

from core.brain_hive_research import build_research_queue_entry, build_topic_research_packet
from storage.db import get_connection
from storage.migrations import run_migrations


class BrainHiveResearchPacketTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM artifact_manifests")
            conn.commit()
        finally:
            conn.close()

    def test_build_topic_research_packet_extracts_trading_features_and_questions(self) -> None:
        packet = build_topic_research_packet(
            topic={
                "topic_id": "topic-1",
                "title": "NULLA Trading Learning Desk",
                "summary": "Manual trader learning desk.",
                "status": "researching",
                "visibility": "read_public",
                "evidence_mode": "mixed",
                "topic_tags": ["trading_learning", "manual_trader"],
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
            },
            claims=[{"claim_id": "claim-1", "agent_id": "peer-1", "status": "active", "capability_tags": ["research"]}],
            posts=[
                {
                    "post_id": "post-1",
                    "post_kind": "analysis",
                    "stance": "support",
                    "body": "Flow and missed mooners posted.",
                    "created_at": "2026-03-09T00:01:00+00:00",
                    "evidence_refs": [
                        {"kind": "trading_hidden_edges", "items": [{"metric": "max_price_change", "score": 0.81, "support": 277}]},
                        {"kind": "trading_missed_mooners", "items": [{"id": "miss-1", "token_mint": "MintA", "ts": 1773000000.0}]},
                        {"kind": "trading_live_flow", "items": [{"detail": "LOW_LIQ", "kind": "PASS", "ts": 1773000100.0}]},
                        {"kind": "task_event", "event_type": "progress_update", "progress_state": "working"},
                    ],
                }
            ],
        )

        self.assertEqual(packet["topic"]["topic_id"], "topic-1")
        self.assertEqual(packet["execution_state"]["active_claim_count"], 1)
        self.assertTrue(packet["trading_feature_export"]["hidden_edges"])
        self.assertTrue(packet["trading_feature_export"]["flow_reason_counts"])
        self.assertTrue(any("Best way to research topic" in item for item in packet["derived_research_questions"]))

    def test_build_research_queue_entry_computes_priority(self) -> None:
        row = build_research_queue_entry(
            topic={
                "topic_id": "topic-2",
                "title": "Agent commons: watcher UX",
                "summary": "Improve watcher UX and task flow.",
                "status": "open",
                "topic_tags": ["agent_commons", "design"],
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
            },
            claims=[],
            posts=[],
        )

        self.assertEqual(row["topic_id"], "topic-2")
        self.assertGreater(row["research_priority"], 0.4)
        self.assertTrue(row["suggested_questions"])

    def test_build_research_queue_entry_uses_commons_signal_to_raise_priority(self) -> None:
        base = build_research_queue_entry(
            topic={
                "topic_id": "topic-3",
                "title": "Commons to research bridge",
                "summary": "Queue should react when Commons pressure builds around a topic.",
                "status": "open",
                "topic_tags": ["agent_commons", "research"],
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
            },
            claims=[],
            posts=[],
        )
        boosted = build_research_queue_entry(
            topic={
                "topic_id": "topic-3",
                "title": "Commons to research bridge",
                "summary": "Queue should react when Commons pressure builds around a topic.",
                "status": "open",
                "topic_tags": ["agent_commons", "research"],
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
            },
            claims=[],
            posts=[],
            commons_signal={
                "candidate_count": 2,
                "review_required_count": 1,
                "approved_count": 1,
                "promoted_count": 0,
                "top_score": 4.8,
                "support_weight": 3.4,
                "challenge_weight": 0.2,
                "training_signal_count": 2,
                "downstream_use_count": 1,
                "reasons": ["commons_review_pressure", "commons_training_signal"],
            },
        )

        self.assertGreater(boosted["research_priority"], base["research_priority"])
        self.assertGreater(boosted["commons_signal_strength"], 0.0)
        self.assertIn("commons_review_pressure", boosted["steering_reasons"])


if __name__ == "__main__":
    unittest.main()
