from __future__ import annotations

import importlib
import threading
import unittest
from datetime import datetime, timezone

import core.api_write_auth as api_write_auth
import network.signer as signer_mod
from core.hive_write_grants import build_hive_write_grant
from core.identity_lifecycle import revoke_identity
from storage.db import get_connection
from storage.migrations import run_migrations


class SignedApiWriteAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        importlib.reload(signer_mod)
        importlib.reload(api_write_auth)
        run_migrations()
        conn = get_connection()
        try:
            for table in ("nonce_cache", "identity_revocations", "identity_key_history"):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        conn = get_connection()
        try:
            for table in ("identity_revocations", "identity_key_history"):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()

    def test_signed_hive_topic_roundtrip(self) -> None:
        agent_id = signer_mod.get_local_peer_id()
        payload = {
            "created_by_agent_id": agent_id,
            "title": "Telegram bot security review",
            "summary": "Compare official docs, token handling, and logging risk.",
            "topic_tags": ["telegram", "security"],
            "status": "open",
            "visibility": "agent_public",
            "evidence_mode": "candidate_only",
        }
        envelope = api_write_auth.build_signed_write_envelope(target_path="/v1/hive/topics", payload=payload)
        unwrapped = api_write_auth.unwrap_signed_write(target_path="/v1/hive/topics", raw_payload=envelope)
        self.assertEqual(unwrapped["created_by_agent_id"], agent_id)

    def test_signed_write_meta_returns_write_grant_separately(self) -> None:
        agent_id = signer_mod.get_local_peer_id()
        write_grant = build_hive_write_grant(
            granted_to=agent_id,
            allowed_paths=["/v1/hive/posts"],
            topic_id="topic-1234567890abcdef",
            max_uses=2,
        )
        payload = {
            "topic_id": "topic-1234567890abcdef",
            "author_agent_id": agent_id,
            "post_kind": "analysis",
            "stance": "support",
            "body": "Bounded analysis with grant attached.",
            "evidence_refs": [],
            "write_grant": write_grant,
        }
        envelope = api_write_auth.build_signed_write_envelope(target_path="/v1/hive/posts", payload=payload)
        unwrapped, meta = api_write_auth.unwrap_signed_write_with_meta(target_path="/v1/hive/posts", raw_payload=envelope)
        self.assertNotIn("write_grant", unwrapped)
        self.assertEqual(meta["write_grant"]["grant_id"], write_grant["grant_id"])
        self.assertEqual(unwrapped["topic_id"], "topic-1234567890abcdef")

    def test_replay_is_rejected(self) -> None:
        agent_id = signer_mod.get_local_peer_id()
        payload = {
            "agent_id": agent_id,
            "status": "idle",
            "capabilities": ["research"],
            "home_region": "eu",
            "current_region": "eu",
            "transport_mode": "lan_only",
            "trust_score": 0.5,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "lease_seconds": 180,
        }
        envelope = api_write_auth.build_signed_write_envelope(target_path="/v1/presence/register", payload=payload)
        api_write_auth.unwrap_signed_write(target_path="/v1/presence/register", raw_payload=envelope)
        with self.assertRaisesRegex(ValueError, "Replay detected"):
            api_write_auth.unwrap_signed_write(target_path="/v1/presence/register", raw_payload=envelope)

    def test_actor_binding_rejects_mismatch(self) -> None:
        payload = {
            "agent_id": f"peer-{'a'*32}",
            "status": "idle",
            "capabilities": ["research"],
            "home_region": "eu",
            "current_region": "eu",
            "transport_mode": "lan_only",
            "trust_score": 0.5,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "lease_seconds": 180,
        }
        envelope = api_write_auth.build_signed_write_envelope(target_path="/v1/presence/register", payload=payload)
        with self.assertRaisesRegex(ValueError, "Signed write signer must match payload field 'agent_id'"):
            api_write_auth.unwrap_signed_write(target_path="/v1/presence/register", raw_payload=envelope)

    def test_cluster_registration_injects_owner_peer_id(self) -> None:
        payload = {
            "node_id": "seed-eu-1",
            "base_url": "https://seed-eu-1.nulla.test",
            "region": "eu",
            "role": "seed",
            "platform_hint": "linux",
            "priority": 10,
            "status": "active",
            "metadata": {},
        }
        envelope = api_write_auth.build_signed_write_envelope(target_path="/v1/cluster/nodes", payload=payload)
        unwrapped = api_write_auth.unwrap_signed_write(target_path="/v1/cluster/nodes", raw_payload=envelope)
        self.assertEqual(unwrapped["metadata"]["owner_peer_id"], signer_mod.get_local_peer_id())

    def test_revoked_identity_cannot_write(self) -> None:
        peer_id = signer_mod.get_local_peer_id()
        revoke_identity(peer_id, scope="signed_write", reason="test_block")
        payload = {
            "created_by_agent_id": peer_id,
            "title": "Legit topic",
            "summary": "This topic should be blocked because the identity is revoked.",
            "topic_tags": ["test"],
            "status": "open",
            "visibility": "agent_public",
            "evidence_mode": "candidate_only",
        }
        envelope = api_write_auth.build_signed_write_envelope(target_path="/v1/hive/topics", payload=payload)
        with self.assertRaisesRegex(ValueError, "revoked"):
            api_write_auth.unwrap_signed_write(target_path="/v1/hive/topics", raw_payload=envelope)

    def test_signed_write_nonce_is_atomic_under_race(self) -> None:
        peer_id = signer_mod.get_local_peer_id()
        payload = {
            "agent_id": peer_id,
            "status": "idle",
            "capabilities": ["research"],
            "home_region": "eu",
            "current_region": "eu",
            "transport_mode": "lan_only",
            "trust_score": 0.5,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "lease_seconds": 180,
        }
        envelope = api_write_auth.build_signed_write_envelope(target_path="/v1/presence/register", payload=payload)
        successes = 0
        failures = 0
        lock = threading.Lock()
        worker_count = 20
        barrier = threading.Barrier(worker_count)

        def worker() -> None:
            nonlocal successes, failures
            try:
                barrier.wait(timeout=2.0)
            except Exception:
                return
            try:
                api_write_auth.unwrap_signed_write(target_path="/v1/presence/register", raw_payload=envelope)
                with lock:
                    successes += 1
            except ValueError:
                with lock:
                    failures += 1

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(worker_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2.0)

        self.assertEqual(successes, 1)
        self.assertEqual(successes + failures, worker_count)


if __name__ == "__main__":
    unittest.main()
