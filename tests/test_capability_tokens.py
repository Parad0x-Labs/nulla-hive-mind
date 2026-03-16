from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core.capability_tokens import (
    expire_stale_capability_tokens,
    issue_assignment_capability,
    load_capability_token,
    mark_capability_token_used,
    verify_assignment_capability,
)
from core.task_capsule import build_task_capsule
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


def _capsule(task_id: str):
    return build_task_capsule(
        parent_agent_id=get_local_peer_id(),
        task_id=task_id,
        task_type="research",
        subtask_type="capability-test",
        summary="Research safe mesh signals",
        sanitized_context={
            "problem_class": "research",
            "environment_tags": {"runtime": "test"},
            "abstract_inputs": ["safe signal"],
            "known_constraints": ["No raw execution."],
        },
        allowed_operations=["reason", "research", "summarize"],
        deadline_ts=datetime.now(timezone.utc) + timedelta(hours=1),
        reward_hint={"points": 5, "wnull_pending": 0},
    )


class CapabilityTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM capability_tokens")
            conn.commit()
        finally:
            conn.close()

    @pytest.mark.xfail(reason="Pre-existing: capability token signing not configured in CI")
    def test_assignment_capability_roundtrip_verifies_and_marks_used(self) -> None:
        task_id = f"task-{uuid.uuid4()}"
        capsule = _capsule(task_id)
        helper_peer_id = "helper-peer-123456"

        token = issue_assignment_capability(
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=helper_peer_id,
            capsule=capsule,
            assignment_mode="single",
            lease_seconds=600,
        )

        decision = verify_assignment_capability(
            token,
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=helper_peer_id,
            capsule=capsule,
        )
        self.assertTrue(decision.ok, decision.reason)

        mark_capability_token_used(str(token["token_id"]))
        remembered = load_capability_token(str(token["token_id"]))
        self.assertIsNotNone(remembered)
        self.assertEqual(str((remembered or {}).get("status")), "used")

    def test_assignment_capability_rejects_tampered_scope(self) -> None:
        task_id = f"task-{uuid.uuid4()}"
        capsule = _capsule(task_id)
        helper_peer_id = "helper-peer-abcdef"

        token = issue_assignment_capability(
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=helper_peer_id,
            capsule=capsule,
            assignment_mode="single",
            lease_seconds=600,
        )
        tampered = dict(token)
        tampered["scope"] = dict(token["scope"])
        tampered["scope"]["allowed_operations"] = ["reason", "call_shell"]

        decision = verify_assignment_capability(
            tampered,
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=helper_peer_id,
            capsule=capsule,
        )
        self.assertFalse(decision.ok)

    def test_expire_stale_capability_tokens_marks_expired(self) -> None:
        task_id = f"task-{uuid.uuid4()}"
        capsule = _capsule(task_id)
        helper_peer_id = "helper-peer-expired"

        token = issue_assignment_capability(
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=helper_peer_id,
            capsule=capsule,
            assignment_mode="single",
            lease_seconds=600,
        )
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE capability_tokens SET expires_at = ?, status = 'active' WHERE token_id = ?",
                ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(), str(token["token_id"])),
            )
            conn.commit()
        finally:
            conn.close()

        expired = expire_stale_capability_tokens(limit=10)
        self.assertEqual(expired, 1)
        remembered = load_capability_token(str(token["token_id"]))
        self.assertEqual(str((remembered or {}).get("status")), "expired")


if __name__ == "__main__":
    unittest.main()
