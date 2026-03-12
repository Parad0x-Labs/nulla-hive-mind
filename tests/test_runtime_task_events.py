from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.runtime_task_events import (
    configure_runtime_event_store,
    emit_runtime_event,
    list_runtime_session_events,
    list_runtime_sessions,
    reset_runtime_event_state,
)
from core.runtime_task_rail import render_runtime_task_rail_html
from storage.migrations import run_migrations


class RuntimeTaskEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "runtime-events.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_event_store(str(self._db_path))
        reset_runtime_event_state()

    def tearDown(self) -> None:
        reset_runtime_event_state()
        configure_runtime_event_store(None)
        self._tmp.cleanup()

    def test_runtime_session_store_tracks_recent_sessions_and_events(self) -> None:
        context = {"runtime_session_id": "openclaw:test-session"}
        emit_runtime_event(
            context,
            event_type="task_received",
            message="Received request: inspect the repo",
            details={"request_preview": "inspect the repo"},
        )
        emit_runtime_event(
            context,
            event_type="tool_selected",
            message="Running real tool workspace.search_text.",
            details={"tool_name": "workspace.search_text", "task_class": "debugging"},
        )

        sessions = list_runtime_sessions(limit=10)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], "openclaw:test-session")
        self.assertEqual(sessions[0]["event_count"], 2)
        self.assertEqual(sessions[0]["request_preview"], "inspect the repo")
        self.assertEqual(sessions[0]["task_class"], "debugging")

        events = list_runtime_session_events("openclaw:test-session", after_seq=0, limit=10)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["seq"], 1)
        self.assertEqual(events[1]["seq"], 2)
        self.assertEqual(events[1]["tool_name"], "workspace.search_text")

    def test_runtime_task_rail_html_contains_polling_endpoints(self) -> None:
        html = render_runtime_task_rail_html()
        self.assertIn("NULLA Task Rail", html)
        self.assertIn("NULLA Trace Rail", html)
        self.assertIn("http://127.0.0.1:11435/trace", html)
        self.assertIn("/api/runtime/sessions", html)
        self.assertIn("/api/runtime/events", html)
        self.assertIn("/api/runtime/control-plane/status", html)
        self.assertIn("Adaptation Loop", html)
        self.assertIn("Hive Budget", html)
        self.assertIn("Task Rail", html)


if __name__ == "__main__":
    unittest.main()
