from __future__ import annotations

import json
import unittest
from unittest import mock

from apps.nulla_api_server import (
    PROJECT_ROOT,
    _format_runtime_event_text,
    _normalize_chat_history,
    _parameter_count_for_model,
    _parameter_size_for_model,
    _run_agent,
    _stream_agent_with_events,
    _stable_openclaw_session_id,
)
from core.runtime_task_events import emit_runtime_event


class NullaAPIServerModelMetadataTests(unittest.TestCase):
    def test_parameter_size_for_model_uses_runtime_tag(self) -> None:
        self.assertEqual(_parameter_size_for_model("qwen2.5:14b"), "14B")
        self.assertEqual(_parameter_size_for_model("ollama/qwen2.5:0.5b"), "0.5B")

    def test_parameter_count_for_model_handles_fractional_billion_sizes(self) -> None:
        self.assertEqual(_parameter_count_for_model("qwen2.5:32b"), 32_000_000_000)
        self.assertEqual(_parameter_count_for_model("qwen2.5:0.5b"), 500_000_000)

    def test_normalize_chat_history_keeps_full_user_assistant_sequence(self) -> None:
        history = _normalize_chat_history(
            [
                {"role": "system", "content": "You are NULLA."},
                {"role": "user", "content": [{"type": "text", "text": "first turn"}]},
                {"role": "assistant", "content": "reply one"},
                {"role": "user", "content": "second turn"},
                {"role": "tool", "content": "ignore this"},
            ]
        )
        self.assertEqual(
            history,
            [
                {"role": "system", "content": "You are NULLA."},
                {"role": "user", "content": "first turn"},
                {"role": "assistant", "content": "reply one"},
                {"role": "user", "content": "second turn"},
            ],
        )

    def test_session_id_prefers_explicit_openclaw_identifiers(self) -> None:
        session_id = _stable_openclaw_session_id(
            body={"conversationId": "abc-123"},
            history=[{"role": "user", "content": "hello"}],
            headers={},
        )
        self.assertTrue(session_id.startswith("openclaw:"))
        self.assertEqual(
            session_id,
            _stable_openclaw_session_id(
                body={"conversationId": "abc-123"},
                history=[{"role": "user", "content": "different"}],
                headers={},
            ),
        )

    def test_format_runtime_event_text_adds_newline(self) -> None:
        self.assertEqual(
            _format_runtime_event_text({"message": "Running real tool workspace.read_file."}),
            "Running real tool workspace.read_file.\n",
        )

    def test_stream_agent_with_events_emits_progress_before_final_response(self) -> None:
        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict | None = None,
        ) -> dict:
            emit_runtime_event(
                source_context,
                event_type="tool_selected",
                message="Running real tool workspace.search_text.",
            )
            emit_runtime_event(
                source_context,
                event_type="tool_executed",
                message="Finished workspace.search_text. Search matches for \"tool_intent\".",
            )
            return {"response": "Grounded final answer."}

        with mock.patch("apps.nulla_api_server._run_agent", side_effect=fake_run_agent):
            chunks = list(
                _stream_agent_with_events(
                    "find tool intent wiring",
                    session_id="openclaw:test",
                    source_context={"conversation_history": []},
                    model="nulla",
                    include_runtime_events=True,
                )
            )

        payloads = [line for line in b"".join(chunks).decode("utf-8").splitlines() if line.strip()]
        contents = [mock_json["message"]["content"] for mock_json in [json.loads(line) for line in payloads]]
        joined = "".join(contents)
        self.assertIn("Running real tool workspace.search_text.\n", joined)
        self.assertIn("Finished workspace.search_text. Search matches for \"tool_intent\".\n", joined)
        self.assertIn("Grounded final answer.", joined)
        self.assertLess(joined.index("Running real tool workspace.search_text.\n"), joined.index("Grounded final answer."))

    def test_stream_agent_with_events_omits_progress_by_default(self) -> None:
        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict | None = None,
        ) -> dict:
            emit_runtime_event(
                source_context,
                event_type="task_received",
                message="Received request: find tool intent wiring",
            )
            emit_runtime_event(
                source_context,
                event_type="tool_selected",
                message="Running real tool workspace.search_text.",
            )
            return {"response": "Clean final answer."}

        with mock.patch("apps.nulla_api_server._run_agent", side_effect=fake_run_agent):
            chunks = list(
                _stream_agent_with_events(
                    "find tool intent wiring",
                    session_id="openclaw:test",
                    source_context={"conversation_history": []},
                    model="nulla",
                )
            )

        payloads = [line for line in b"".join(chunks).decode("utf-8").splitlines() if line.strip()]
        contents = [json.loads(line)["message"]["content"] for line in payloads]
        joined = "".join(contents)
        self.assertNotIn("Received request:", joined)
        self.assertNotIn("Running real tool workspace.search_text.", joined)
        self.assertIn("Clean final answer.", joined)

    def test_run_agent_injects_runtime_session_id_into_source_context(self) -> None:
        seen: dict[str, object] = {}

        class FakeAgent:
            def run_once(self, user_text: str, *, session_id_override: str | None = None, source_context: dict | None = None) -> dict:
                seen["user_text"] = user_text
                seen["session_id_override"] = session_id_override
                seen["source_context"] = dict(source_context or {})
                return {"response": "ok", "confidence": 1.0}

        with mock.patch("apps.nulla_api_server._agent", FakeAgent()):
            result = _run_agent(
                "inspect the repo",
                session_id="openclaw:test-session",
                source_context={"conversation_history": []},
            )

        self.assertEqual(result["response"], "ok")
        self.assertEqual(seen["session_id_override"], "openclaw:test-session")
        source_context = dict(seen["source_context"])  # type: ignore[arg-type]
        self.assertEqual(source_context["runtime_session_id"], "openclaw:test-session")
        self.assertEqual(source_context["platform"], "openclaw")
        self.assertIn("workspace", source_context)
        self.assertIn("workspace_root", source_context)

    def test_run_agent_falls_back_to_project_root_when_cwd_is_gone(self) -> None:
        seen: dict[str, object] = {}

        class FakeAgent:
            def run_once(self, user_text: str, *, session_id_override: str | None = None, source_context: dict | None = None) -> dict:
                seen["source_context"] = dict(source_context or {})
                return {"response": "ok", "confidence": 1.0}

        with mock.patch("apps.nulla_api_server._agent", FakeAgent()), mock.patch(
            "apps.nulla_api_server.Path.cwd",
            side_effect=FileNotFoundError,
        ):
            _run_agent("inspect the repo")

        source_context = dict(seen["source_context"])  # type: ignore[arg-type]
        self.assertEqual(source_context["workspace"], str(PROJECT_ROOT))
        self.assertEqual(source_context["workspace_root"], str(PROJECT_ROOT))


if __name__ == "__main__":
    unittest.main()
