from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib import request

from apps.nulla_api_server import (
    PROJECT_ROOT,
    NullaAPIHandler,
    _daemon_runtime_config,
    _dispatch_post,
    _ensure_default_provider,
    _format_runtime_event_text,
    _normalize_chat_history,
    _parameter_count_for_model,
    _parameter_size_for_model,
    _run_agent,
    _stable_openclaw_session_id,
    _stream_agent_with_events,
    create_app,
    main,
)
from core.nulla_workstation_ui import NULLA_WORKSTATION_DEPLOYMENT_VERSION
from core.runtime_task_events import emit_runtime_event
from core.web.api.runtime import (
    RuntimeServices,
    bootstrap_runtime_services,
    build_runtime_version_stamp,
    log_prewarm_results,
)
from core.web.api.service import json_response
from tests.asgi_harness import asgi_request


class NullaAPIServerModelMetadataTests(unittest.TestCase):
    @staticmethod
    def _server_with_runtime(runtime: RuntimeServices | None = None) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer(("127.0.0.1", 0), NullaAPIHandler)
        server.nulla_runtime = runtime or RuntimeServices(display_name="NULLA")
        return server

    def test_create_app_keeps_runtime_services_in_app_state(self) -> None:
        runtime = RuntimeServices(display_name="NULLA", runtime_version_stamp={"release_version": "0.4.0"})

        app = create_app(runtime)

        self.assertIs(app.state.runtime, runtime)
        self.assertEqual(app.state.model_name, "nulla")

    def test_create_app_emits_request_id_header_and_echoes_client_request_id(self) -> None:
        runtime = RuntimeServices(display_name="NULLA", runtime_version_stamp={"release_version": "0.4.0"})
        app = create_app(runtime)

        status, headers, _ = asgi_request(app, method="GET", path="/healthz", headers={"X-Request-ID": "req-api-123"})

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-request-id"], "req-api-123")
        self.assertEqual(headers["x-correlation-id"], "req-api-123")

    def test_create_app_generates_request_id_when_missing(self) -> None:
        runtime = RuntimeServices(display_name="NULLA", runtime_version_stamp={"release_version": "0.4.0"})
        app = create_app(runtime)

        status, headers, _ = asgi_request(app, method="GET", path="/healthz")

        self.assertEqual(status, 200)
        self.assertTrue(headers["x-request-id"])
        self.assertEqual(headers["x-correlation-id"], headers["x-request-id"])

    def test_create_app_keeps_health_responsive_while_post_dispatch_blocks(self) -> None:
        runtime = RuntimeServices(display_name="NULLA", runtime_version_stamp={"release_version": "0.4.0"})
        app = create_app(runtime)
        entered = threading.Event()
        release = threading.Event()

        def blocking_post_dispatcher(**_: object):
            entered.set()
            release.wait(timeout=5)
            return json_response(200, {"ok": True})

        app.state.post_dispatcher = blocking_post_dispatcher

        import uvicorn

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])

        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_level="warning",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        post_thread: threading.Thread | None = None
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    with request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(0.05)
            else:
                self.fail("uvicorn test server did not become healthy")

            post_result: dict[str, object] = {}

            def send_blocking_post() -> None:
                req = request.Request(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    data=json.dumps(
                        {
                            "model": "nulla",
                            "messages": [{"role": "user", "content": "hello"}],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with request.urlopen(req, timeout=5) as response:
                    post_result["status"] = response.status
                    post_result["body"] = response.read().decode("utf-8")

            post_thread = threading.Thread(target=send_blocking_post, daemon=True)
            post_thread.start()
            self.assertTrue(entered.wait(timeout=2.0))

            started = time.perf_counter()
            with request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            latency = time.perf_counter() - started

            self.assertEqual(response.status, 200)
            self.assertTrue(payload["ok"])
            self.assertLess(latency, 0.5)

            release.set()
            post_thread.join(timeout=3.0)
            self.assertEqual(post_result["status"], 200)
        finally:
            release.set()
            server.should_exit = True
            if post_thread is not None:
                post_thread.join(timeout=1.0)
            thread.join(timeout=2.0)

    def test_create_app_streaming_v1_chat_completions_uses_openai_sse(self) -> None:
        runtime = RuntimeServices(display_name="NULLA")
        stream_chunks = iter(
            (
                b'{"model":"nulla","created_at":"2026-03-28T00:00:00.000000Z","message":{"role":"assistant","content":"stream"},"done":false}\n',
                b'{"model":"nulla","created_at":"2026-03-28T00:00:00.000000Z","message":{"role":"assistant","content":" ok"},"done":false}\n',
                b'{"model":"nulla","created_at":"2026-03-28T00:00:00.000000Z","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop","eval_count":2}\n',
            )
        )

        with mock.patch("apps.nulla_api_server._stream_agent_with_events", return_value=stream_chunks):
            response = _dispatch_post(
                path="/v1/chat/completions",
                body={
                    "model": "nulla",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="nulla",
                workspace_root_provider=lambda: "/tmp",
            )

        status = response.status
        body = b"".join(response.stream or ())
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", response.content_type)
        self.assertIn('data: {"id":"chatcmpl-', text)
        self.assertIn('"object":"chat.completion.chunk"', text)
        self.assertIn('"content":"stream"', text)
        self.assertIn('"content":" ok"', text)
        self.assertIn("data: [DONE]", text)

    def test_create_app_streaming_api_chat_preserves_ollama_ndjson(self) -> None:
        runtime = RuntimeServices(display_name="NULLA")
        stream_chunks = iter(
            (
                b'{"model":"nulla","created_at":"2026-03-28T00:00:00.000000Z","message":{"role":"assistant","content":"stream"},"done":false}\n',
                b'{"model":"nulla","created_at":"2026-03-28T00:00:00.000000Z","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop","eval_count":1}\n',
            )
        )

        with mock.patch("apps.nulla_api_server._stream_agent_with_events", return_value=stream_chunks):
            response = _dispatch_post(
                path="/api/chat",
                body={
                    "model": "nulla",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
                headers={"content-type": "application/json"},
                runtime=runtime,
                model_name="nulla",
                workspace_root_provider=lambda: "/tmp",
            )

        status = response.status
        body = b"".join(response.stream or ())
        self.assertEqual(status, 200)
        self.assertIn("application/x-ndjson", response.content_type)
        self.assertIn(b'"content":"stream"', body)
        self.assertNotIn(b"data: [DONE]", body)

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

    def test_prioritize_project_root_on_sys_path_prefers_local_checkout(self) -> None:
        with mock.patch.object(sys, "path", ["/tmp/shadow-core", str(PROJECT_ROOT), "/tmp/elsewhere"]):
            from apps import nulla_api_server

            nulla_api_server._prioritize_project_root_on_sys_path()
            self.assertEqual(sys.path[0], str(PROJECT_ROOT))
            self.assertEqual(sys.path.count(str(PROJECT_ROOT)), 1)

    def test_runtime_version_stamp_uses_build_source_metadata_when_git_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_root = Path(tmp_dir)
            config_dir = project_root / "config"
            config_dir.mkdir(parents=True)
            (config_dir / "build-source.json").write_text(
                json.dumps(
                    {
                        "branch": "main",
                        "ref": "main",
                        "commit": "1234567890abcdef1234567890abcdef12345678",
                        "source_url": "https://github.com/Parad0x-Labs/nulla-hive-mind/archive/refs/heads/main.tar.gz",
                    }
                ),
                encoding="utf-8",
            )

            stamp = build_runtime_version_stamp(
                project_root=project_root,
                runtime_model_tag="qwen2.5:14b",
                workstation_version="test-workstation",
            )

        self.assertEqual(stamp["branch"], "main")
        self.assertEqual(stamp["commit"], "1234567890ab")
        self.assertEqual(stamp["dirty"], False)

    def test_bootstrap_runtime_services_hydrates_public_hive_auth_into_active_runtime_home(self) -> None:
        runtime_home = Path("/tmp/nulla-runtime-home")
        config_home = runtime_home / "config"
        auth_target = config_home / "agent-bootstrap.json"
        probe = mock.Mock(accelerator="cpu", gpu_name=None)
        tier = mock.Mock(ollama_tag="qwen2.5:14b")
        boot = mock.Mock(backend_selection=mock.Mock(backend_name="TorchMPSBackend", device="mps"))
        agent = mock.Mock()
        daemon = mock.Mock(config=mock.Mock(bind_port=49152))
        compute_daemon = mock.Mock()
        model_registry = mock.Mock()
        model_registry.startup_warnings.return_value = []
        persona = mock.Mock(persona_id="default")

        with mock.patch("core.web.api.runtime.bootstrap_runtime_mode", return_value=boot), mock.patch(
            "core.web.api.runtime.is_first_boot",
            return_value=False,
        ), mock.patch("core.web.api.runtime.get_local_peer_id", return_value="peer-123"), mock.patch(
            "core.credit_ledger.ensure_starter_credits",
            return_value=False,
        ), mock.patch("core.web.api.runtime.active_config_home_dir", return_value=config_home), mock.patch(
            "core.web.api.runtime.ensure_public_hive_auth",
            return_value={"ok": False, "status": "missing_remote_config_path", "watch_host": "hive.example.test"},
        ) as ensure_auth, mock.patch("core.web.api.runtime.probe_machine", return_value=probe), mock.patch(
            "core.web.api.runtime.select_qwen_tier",
            return_value=tier,
        ), mock.patch("core.web.api.runtime.ensure_ollama_model"), mock.patch(
            "core.web.api.runtime.build_runtime_version_stamp",
            return_value={"started_at": "2026-03-27T00:00:00.000000Z", "build_id": "0.4.0+test"},
        ), mock.patch(
            "core.web.api.runtime.ComputeModeDaemon",
            return_value=compute_daemon,
        ), mock.patch("core.web.api.runtime.ModelRegistry", return_value=model_registry), mock.patch(
            "core.web.api.runtime.ensure_default_provider",
        ), mock.patch("core.web.api.runtime.load_active_persona", return_value=persona), mock.patch(
            "core.web.api.runtime.get_agent_display_name",
            return_value="NULLA",
        ), mock.patch("core.web.api.runtime.ensure_openclaw_registration", return_value=True), mock.patch(
            "core.web.api.runtime.NullaAgent",
            return_value=agent,
        ), mock.patch("core.web.api.runtime.resolve_local_worker_capacity", return_value=(3, 3)), mock.patch(
            "core.web.api.runtime.NullaDaemon",
            return_value=daemon,
        ):
            runtime = bootstrap_runtime_services(
                project_root=PROJECT_ROOT,
                workstation_version="test-workstation",
            )

        ensure_auth.assert_called_once_with(
            project_root=PROJECT_ROOT,
            target_path=auth_target,
        )
        self.assertEqual(runtime.public_hive_auth["status"], "missing_remote_config_path")

    def test_ensure_default_provider_registers_kimi_when_key_is_present(self) -> None:
        manifests = {}
        registry = mock.Mock()

        def _get_manifest(provider_name: str, model_name: str):
            return manifests.get((provider_name, model_name))

        def _register_manifest(manifest):
            manifests[(manifest.provider_name, manifest.model_name)] = manifest
            return manifest

        registry.get_manifest.side_effect = _get_manifest
        registry.register_manifest.side_effect = _register_manifest

        with mock.patch.dict(
            os.environ,
            {
                "KIMI_API_KEY": "test-key",
                "KIMI_BASE_URL": "https://kimi.example/v1",
                "NULLA_KIMI_MODEL": "kimi-latest",
            },
            clear=False,
        ):
            _ensure_default_provider(registry, "qwen2.5:14b")

        self.assertIn(("ollama-local", "qwen2.5:14b"), manifests)
        self.assertIn(("kimi-remote", "kimi-latest"), manifests)
        self.assertEqual(manifests[("kimi-remote", "kimi-latest")].runtime_config["base_url"], "https://kimi.example/v1")

    def test_ensure_default_provider_registers_vllm_when_base_url_is_present(self) -> None:
        manifests = {}
        registry = mock.Mock()

        def _get_manifest(provider_name: str, model_name: str):
            return manifests.get((provider_name, model_name))

        def _register_manifest(manifest):
            manifests[(manifest.provider_name, manifest.model_name)] = manifest
            return manifest

        registry.get_manifest.side_effect = _get_manifest
        registry.register_manifest.side_effect = _register_manifest

        with mock.patch.dict(
            os.environ,
            {
                "VLLM_BASE_URL": "http://127.0.0.1:8100/v1",
                "NULLA_VLLM_MODEL": "qwen2.5:32b-vllm",
                "VLLM_CONTEXT_WINDOW": "65536",
            },
            clear=False,
        ):
            _ensure_default_provider(registry, "qwen2.5:14b")

        self.assertIn(("ollama-local", "qwen2.5:14b"), manifests)
        self.assertIn(("vllm-local", "qwen2.5:32b-vllm"), manifests)
        self.assertEqual(manifests[("vllm-local", "qwen2.5:32b-vllm")].runtime_config["base_url"], "http://127.0.0.1:8100/v1")
        self.assertEqual(manifests[("vllm-local", "qwen2.5:32b-vllm")].metadata["context_window"], 65536)

    def test_ensure_default_provider_registers_llamacpp_when_base_url_is_present(self) -> None:
        manifests = {}
        registry = mock.Mock()

        def _get_manifest(provider_name: str, model_name: str):
            return manifests.get((provider_name, model_name))

        def _register_manifest(manifest):
            manifests[(manifest.provider_name, manifest.model_name)] = manifest
            return manifest

        registry.get_manifest.side_effect = _get_manifest
        registry.register_manifest.side_effect = _register_manifest

        with mock.patch.dict(
            os.environ,
            {
                "LLAMACPP_BASE_URL": "http://127.0.0.1:8090/v1",
                "NULLA_LLAMACPP_MODEL": "qwen2.5:14b-gguf",
                "LLAMACPP_CONTEXT_WINDOW": "16384",
            },
            clear=False,
        ):
            _ensure_default_provider(registry, "qwen2.5:14b")

        self.assertIn(("ollama-local", "qwen2.5:14b"), manifests)
        self.assertIn(("llamacpp-local", "qwen2.5:14b-gguf"), manifests)
        self.assertEqual(manifests[("llamacpp-local", "qwen2.5:14b-gguf")].runtime_config["base_url"], "http://127.0.0.1:8090/v1")
        self.assertEqual(manifests[("llamacpp-local", "qwen2.5:14b-gguf")].metadata["context_window"], 16384)

    def test_ensure_default_provider_adds_honest_ollama_prewarm_config(self) -> None:
        manifests = {}
        registry = mock.Mock()

        def _get_manifest(provider_name: str, model_name: str):
            return manifests.get((provider_name, model_name))

        def _register_manifest(manifest):
            manifests[(manifest.provider_name, manifest.model_name)] = manifest
            return manifest

        registry.get_manifest.side_effect = _get_manifest
        registry.register_manifest.side_effect = _register_manifest

        _ensure_default_provider(registry, "qwen2.5:14b")

        manifest = manifests[("ollama-local", "qwen2.5:14b")]
        self.assertEqual(manifest.runtime_config["prewarm"]["strategy"], "ollama_generate")
        self.assertEqual(manifest.runtime_config["prewarm"]["keep_alive"], "15m")

    def test_bootstrap_runtime_services_runs_provider_prewarm_logging(self) -> None:
        persona = mock.Mock(persona_id="default")
        agent = mock.Mock()
        daemon = mock.Mock()
        compute_daemon = mock.Mock()
        model_registry = mock.Mock()
        model_registry.startup_warnings.return_value = []

        with mock.patch("core.web.api.runtime.bootstrap_runtime_mode", return_value=mock.Mock(backend_selection=mock.Mock(backend_name="mlx", device="mps"))), mock.patch(
            "core.web.api.runtime.is_first_boot",
            return_value=False,
        ), mock.patch("core.credit_ledger.ensure_starter_credits", return_value=False), mock.patch(
            "core.web.api.runtime.ensure_public_hive_auth",
            return_value={"ok": True, "status": "ok"},
        ), mock.patch(
            "core.web.api.runtime.probe_machine",
            return_value=mock.Mock(accelerator="mps", gpu_name="Apple GPU"),
        ), mock.patch(
            "core.web.api.runtime.select_qwen_tier",
            return_value=mock.Mock(ollama_tag="qwen2.5:14b"),
        ), mock.patch(
            "core.web.api.runtime.ensure_ollama_model",
        ), mock.patch(
            "core.web.api.runtime.build_runtime_version_stamp",
            return_value={"started_at": "2026-03-28T00:00:00.000000Z", "build_id": "test", "branch": "main", "commit": "abc123", "dirty": False},
        ), mock.patch(
            "core.web.api.runtime.ComputeModeDaemon",
            return_value=compute_daemon,
        ), mock.patch(
            "core.web.api.runtime.ModelRegistry",
            return_value=model_registry,
        ), mock.patch(
            "core.web.api.runtime.ensure_default_provider",
        ), mock.patch(
            "core.web.api.runtime.log_prewarm_results",
        ) as log_prewarm, mock.patch(
            "core.web.api.runtime.load_active_persona",
            return_value=persona,
        ), mock.patch(
            "core.web.api.runtime.get_agent_display_name",
            return_value="NULLA",
        ), mock.patch(
            "core.web.api.runtime.ensure_openclaw_registration",
            return_value=True,
        ), mock.patch(
            "core.web.api.runtime.NullaAgent",
            return_value=agent,
        ), mock.patch(
            "core.web.api.runtime.resolve_local_worker_capacity",
            return_value=(3, 3),
        ), mock.patch(
            "core.web.api.runtime.NullaDaemon",
            return_value=daemon,
        ):
            bootstrap_runtime_services(
                project_root=PROJECT_ROOT,
                workstation_version="test-workstation",
            )

        log_prewarm.assert_called_once_with(model_registry)

    def test_log_prewarm_results_treats_background_warmup_as_info(self) -> None:
        registry = mock.Mock()
        registry.prewarm_enabled_providers.return_value = [
            {
                "ok": True,
                "provider_id": "ollama-local:qwen2.5:14b",
                "status": "warming_background",
                "reason": "cold_start_timeout",
                "keep_alive": "15m",
                "timeout_seconds": 45.0,
                "background_timeout_seconds": 90.0,
            }
        ]

        with self.assertLogs("nulla.api", level="INFO") as captured:
            log_prewarm_results(registry)

        self.assertEqual(len(captured.records), 1)
        self.assertEqual(captured.records[0].levelname, "INFO")
        self.assertIn("Provider prewarm continuing in background", captured.output[0])

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
        self.assertEqual(
            _format_runtime_event_text({"event_type": "model_output_chunk", "message": "hello"}),
            "hello",
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

    def test_stream_agent_with_events_prefers_live_model_chunks_over_fake_replay(self) -> None:
        def fake_run_agent(
            user_text: str,
            *,
            session_id: str | None = None,
            source_context: dict | None = None,
        ) -> dict:
            emit_runtime_event(
                {"runtime_event_stream_id": str((source_context or {}).get("runtime_event_stream_id") or "")},
                event_type="model_output_chunk",
                message="Hello",
            )
            emit_runtime_event(
                {"runtime_event_stream_id": str((source_context or {}).get("runtime_event_stream_id") or "")},
                event_type="model_output_chunk",
                message=" world",
            )
            return {"response": "Hello world"}

        with mock.patch("apps.nulla_api_server._run_agent", side_effect=fake_run_agent):
            chunks = list(
                _stream_agent_with_events(
                    "say hello",
                    session_id="openclaw:test",
                    source_context={"conversation_history": []},
                    model="nulla",
                )
            )

        payloads = [json.loads(line) for line in b"".join(chunks).decode("utf-8").splitlines() if line.strip()]
        contents = [payload["message"]["content"] for payload in payloads]
        assert contents.count("Hello") == 1
        assert contents.count(" world") == 1
        assert "Hello world" not in contents
        assert payloads[-1]["done"] is True

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
        stamp = {
            "release_version": "0.4.0-closed-test",
            "build_id": "0.4.0-closed-test+abc123def456.dirty",
            "started_at": "2026-03-14T10:00:00.000000Z",
            "commit": "abc123def456",
            "dirty": True,
            "branch": "feature/local-bootstrap",
        }
        server = self._server_with_runtime(
            RuntimeServices(
                display_name="NULLA",
                runtime_version_stamp=stamp,
                public_hive_auth={
                    "ok": False,
                    "status": "missing_remote_config_path",
                    "watch_host": "hive.example.test",
                    "remote_config_path": "/etc/nulla-hive-mind/watch-config.json",
                    "next_step": "python -m ops.ensure_public_hive_auth --watch-host hive.example.test --remote-config-path /etc/nulla-hive-mind/watch-config.json",
                },
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with mock.patch(
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
                self.assertEqual(payload["capabilities"]["public_hive_auth"]["status"], "missing_remote_config_path")
                self.assertEqual(payload["capabilities"]["public_hive_auth"]["watch_host"], "hive.example.test")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_runtime_version_route_returns_current_runtime_stamp(self) -> None:
        stamp = {
            "release_version": "0.4.0-closed-test",
            "build_id": "0.4.0-closed-test+abc123def456",
            "started_at": "2026-03-14T10:00:00.000000Z",
            "commit": "abc123def456",
            "dirty": False,
            "branch": "feature/local-bootstrap",
        }
        server = self._server_with_runtime(RuntimeServices(display_name="NULLA", runtime_version_stamp=stamp))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with request.urlopen(f"http://127.0.0.1:{port}/api/runtime/version", timeout=5) as response:
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
        server = self._server_with_runtime(
            RuntimeServices(
                display_name="NULLA",
                public_hive_auth={
                    "ok": True,
                    "status": "synced_from_ssh",
                },
            )
        )
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
                self.assertEqual(payload["capabilities"][0]["state"], "implemented")
                self.assertEqual(payload["public_hive_auth"]["status"], "synced_from_ssh")
                self.assertEqual(payload["public_hive_auth"]["ok"], True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)

    def test_main_runs_uvicorn_with_factory_app_and_shutdown(self) -> None:
        runtime = RuntimeServices(display_name="NULLA")
        fake_uvicorn = mock.Mock()
        fake_server = mock.Mock()
        fake_uvicorn.Config.return_value = mock.sentinel.config
        fake_uvicorn.Server.return_value = fake_server

        with mock.patch("apps.nulla_api_server._bootstrap", return_value=runtime), mock.patch.dict(
            "sys.modules",
            {"uvicorn": fake_uvicorn},
        ), mock.patch(
            "sys.argv",
            ["nulla-api-server", "--bind", "127.0.0.1", "--port", "18080"],
        ):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        fake_uvicorn.Config.assert_called_once()
        _, kwargs = fake_uvicorn.Config.call_args
        self.assertEqual(kwargs["host"], "127.0.0.1")
        self.assertEqual(kwargs["port"], 18080)
        self.assertEqual(kwargs["access_log"], False)
        self.assertIsNotNone(fake_uvicorn.Config.call_args.args[0])
        fake_server.run.assert_called_once()
        self.assertTrue(hasattr(runtime, "shutdown"))

    @unittest.skipUnless(os.environ.get("NULLA_LIVE_ROUTE_PROOF") == "1", "live route proof only")
    def test_live_trace_route_carries_workstation_deploy_proof(self) -> None:
        server = self._server_with_runtime()
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
