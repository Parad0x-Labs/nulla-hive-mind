from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib import request

from apps.nulla_api_server import (
    PROJECT_ROOT,
    NullaAPIHandler,
    _daemon_runtime_config,
    _format_runtime_event_text,
    _normalize_chat_history,
    _parameter_count_for_model,
    _parameter_size_for_model,
    _run_agent,
    _stable_openclaw_session_id,
    _stream_agent_with_events,
)
from core.nulla_workstation_ui import NULLA_WORKSTATION_DEPLOYMENT_VERSION
from core.runtime_task_events import emit_runtime_event


class NullaAPIServerModelMetadataTests(unittest.TestCase):
    def test_daemon_runtime_config_uses_env_overrides_for_isolated_acceptance(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "NULLA_DAEMON_BIND_HOST": "127.0.0.1",
                "NULLA_DAEMON_BIND_PORT": "60220",
                "NULLA_DAEMON_ADVERTISE_HOST": "127.0.0.1",
                "NULLA_DAEMON_HEALTH_BIND_HOST": "127.0.0.1",
                "NULLA_DAEMON_HEALTH_PORT": "0",
            },
            clear=False,
        ):
            config = _daemon_runtime_config(capacity=3, local_worker_threads=6)

        self.assertEqual(config.bind_host, "127.0.0.1")
        self.assertEqual(config.bind_port, 60220)
        self.assertEqual(config.advertise_host, "127.0.0.1")
        self.assertEqual(config.health_bind_host, "127.0.0.1")
        self.assertEqual(config.health_bind_port, 0)
        self.assertEqual(config.capacity, 3)
        self.assertEqual(config.local_worker_threads, 6)

    def test_daemon_runtime_config_ignores_invalid_integer_overrides(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "NULLA_DAEMON_BIND_PORT": "nope",
                "NULLA_DAEMON_HEALTH_PORT": "still-nope",
            },
            clear=False,
        ):
            config = _daemon_runtime_config(capacity=2, local_worker_threads=4)

        self.assertEqual(config.bind_port, 49152)
        self.assertEqual(config.health_bind_port, 0)

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

    def test_healthz_exposes_runtime_version_headers_and_payload(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), NullaAPIHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            stamp = {
                "release_version": "0.4.0-closed-test",
                "build_id": "0.4.0-closed-test+abc123def456.dirty",
                "started_at": "2026-03-14T10:00:00.000000Z",
                "commit": "abc123def456",
                "dirty": True,
                "branch": "feature/local-bootstrap",
            }
            with mock.patch("apps.nulla_api_server._runtime_version_stamp", stamp), mock.patch(
                "apps.nulla_api_server._display_name",
                "NULLA",
            ), mock.patch(
                "apps.nulla_api_server.runtime_capability_snapshot",
                return_value={"feature_flags": {"public_hive_enabled": True}, "capabilities": [{"name": "local_runtime"}]},
            ), request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.headers.get("X-Nulla-Runtime-Version"), "0.4.0-closed-test")
                self.assertEqual(response.headers.get("X-Nulla-Runtime-Build"), "0.4.0-closed-test+abc123def456.dirty")
                self.assertEqual(response.headers.get("X-Nulla-Runtime-Commit"), "abc123def456")
                self.assertEqual(response.headers.get("X-Nulla-Runtime-Dirty"), "1")
                self.assertEqual(payload["runtime"]["branch"], "feature/local-bootstrap")
                self.assertEqual(payload["runtime"]["build_id"], "0.4.0-closed-test+abc123def456.dirty")
                self.assertEqual(payload["capabilities"]["feature_flags"]["public_hive_enabled"], True)
                self.assertEqual(payload["capabilities"]["capabilities"][0]["name"], "local_runtime")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_runtime_version_route_returns_current_runtime_stamp(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), NullaAPIHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            stamp = {
                "release_version": "0.4.0-closed-test",
                "build_id": "0.4.0-closed-test+abc123def456",
                "started_at": "2026-03-14T10:00:00.000000Z",
                "commit": "abc123def456",
                "dirty": False,
                "branch": "feature/local-bootstrap",
            }
            with mock.patch("apps.nulla_api_server._runtime_version_stamp", stamp), request.urlopen(
                f"http://127.0.0.1:{port}/api/runtime/version",
                timeout=5,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["release_version"], "0.4.0-closed-test")
                self.assertEqual(payload["build_id"], "0.4.0-closed-test+abc123def456")
                self.assertEqual(payload["branch"], "feature/local-bootstrap")
                self.assertEqual(response.headers.get("X-Nulla-Runtime-Dirty"), "0")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_runtime_capabilities_route_returns_current_runtime_capability_snapshot(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), NullaAPIHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            snapshot = {
                "mode": "api_server",
                "feature_flags": {"helper_mesh_enabled": True},
                "capabilities": [{"name": "helper_mesh", "state": "partial"}],
            }
            with mock.patch("apps.nulla_api_server.runtime_capability_snapshot", return_value=snapshot), request.urlopen(
                f"http://127.0.0.1:{port}/api/runtime/capabilities",
                timeout=5,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["mode"], "api_server")
                self.assertEqual(payload["feature_flags"]["helper_mesh_enabled"], True)
                self.assertEqual(payload["capabilities"][0]["name"], "helper_mesh")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    @unittest.skipUnless(os.environ.get("NULLA_LIVE_ROUTE_PROOF") == "1", "live route proof only")
    def test_live_trace_route_carries_workstation_deploy_proof(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), NullaAPIHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with request.urlopen(f"http://127.0.0.1:{port}/trace", timeout=5) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.headers.get("X-Nulla-Workstation-Version"), NULLA_WORKSTATION_DEPLOYMENT_VERSION)
                self.assertEqual(response.headers.get("X-Nulla-Workstation-Surface"), "trace-rail")
                self.assertIn(NULLA_WORKSTATION_DEPLOYMENT_VERSION, body)
                self.assertIn('data-workstation-surface="trace-rail"', body)
                self.assertIn("Trace workstation v1", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
