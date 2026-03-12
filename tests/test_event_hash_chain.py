from __future__ import annotations

import json
import unittest
import uuid

from storage.db import get_connection
from storage.event_hash_chain import append_hashed_event, repair_chain, verify_chain
from storage.migrations import run_migrations


class EventHashChainTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_repair_chain_fixes_broken_prev_hash_links(self) -> None:
        first = f"evt-{uuid.uuid4().hex}"
        second = f"evt-{uuid.uuid4().hex}"
        append_hashed_event(first, {"kind": "first", "value": 1})
        append_hashed_event(second, {"kind": "second", "value": 2})

        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE event_hash_chain
                SET prev_hash = ?, event_hash = ?
                WHERE event_id = ?
                """,
                ("broken-prev", "broken-hash", second),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertFalse(verify_chain())
        repaired = repair_chain()
        self.assertGreaterEqual(repaired, 1)
        self.assertTrue(verify_chain())

    def test_append_hashed_event_is_idempotent_for_same_event_id(self) -> None:
        event_id = f"evt-{uuid.uuid4().hex}"
        first_hash = append_hashed_event(event_id, {"kind": "same", "value": 1})
        second_hash = append_hashed_event(event_id, {"kind": "same", "value": 1})
        self.assertEqual(first_hash, second_hash)

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM event_hash_chain WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(int((row or {"cnt": 0})["cnt"]), 1)


if __name__ == "__main__":
    unittest.main()
