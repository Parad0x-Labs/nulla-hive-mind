from __future__ import annotations

import io
import json
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

from apps.nulla_agent import NullaAgent
from core.public_hive_bridge import (
    PublicHiveBridge,
    PublicHiveBridgeConfig,
    ensure_public_hive_auth,
    load_public_hive_bridge_config,
    public_hive_write_enabled,
    sync_public_hive_auth_from_ssh,
    write_public_hive_agent_bootstrap,
)
from core.hive_write_grants import build_hive_write_grant
from network.signer import get_local_peer_id


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_public_hive_bridge_syncs_presence_with_signed_envelope_and_token() -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        seen["token"] = req.get_header("X-nulla-meet-token")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"ok": True, "result": {"agent_id": "peer-123"}, "error": None})

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            request_timeout_seconds=7,
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.sync_presence(
        agent_name="NULLA",
        capabilities=["persistent_memory", "web_research"],
        status="busy",
        transport_mode="openclaw_api",
    )

    assert result["ok"] is True
    assert seen["url"] == "http://seed-eu.example.test:8766/v1/presence/register"
    assert seen["timeout"] == 7
    assert seen["token"] == "cluster-token"
    envelope = dict(seen["body"])
    assert envelope["target_path"] == "/v1/presence/register"
    assert envelope["payload"]["status"] == "busy"
    assert envelope["payload"]["home_region"] == "eu"


def test_public_hive_bridge_surfaces_http_error_body_for_write_failures() -> None:
    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url,
            429,
            "Too Many Requests",
            hdrs=None,
            fp=io.BytesIO(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Public Hive write quota exhausted for today. Used 48.0/48.0 points at tier established.",
                    }
                ).encode("utf-8")
            ),
        )

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            request_timeout_seconds=7,
        ),
        urlopen=fake_urlopen,
    )

    try:
        bridge.post_public_topic_progress(
            topic_id="topic-1234567890abcdef",
            body="probe",
            progress_state="working",
            claim_id="claim-123",
            idempotency_key="probe-429",
        )
    except ValueError as exc:
        assert "quota exhausted" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError for HTTP 429 write failure")


def test_public_hive_bridge_allows_seed_urls_without_token() -> None:
    with mock.patch("core.public_hive_bridge.ensure_public_hive_agent_bootstrap", return_value=None), mock.patch(
        "core.public_hive_bridge._load_agent_bootstrap",
        return_value={
            "home_region": "eu",
            "meet_seed_urls": ["https://seed-eu.example.test:8766"],
            "tls_insecure_skip_verify": True,
        },
    ):
        cfg = load_public_hive_bridge_config()

    assert cfg.enabled is True
    assert cfg.meet_seed_urls == ("https://seed-eu.example.test:8766",)
    assert cfg.auth_token is None
    assert cfg.tls_insecure_skip_verify is True


def test_public_hive_bridge_loads_route_scoped_write_grants() -> None:
    local_peer_id = get_local_peer_id()
    grant = build_hive_write_grant(
        granted_to=local_peer_id,
        allowed_paths=["/v1/hive/posts"],
        max_uses=3,
    )
    with mock.patch("core.public_hive_bridge.ensure_public_hive_agent_bootstrap", return_value=None), mock.patch(
        "core.public_hive_bridge._load_agent_bootstrap",
        return_value={
            "home_region": "eu",
            "meet_seed_urls": ["https://seed-eu.example.test:8766"],
            "write_grants_by_base_url": {
                "https://seed-eu.example.test:8766": {
                    "/v1/hive/posts": grant,
                }
            },
        },
    ):
        cfg = load_public_hive_bridge_config()

    assert cfg.write_grants_by_base_url["https://seed-eu.example.test:8766"]["/v1/hive/posts"]["grant_id"] == grant["grant_id"]


def test_public_hive_write_enabled_requires_auth_for_public_seed_urls() -> None:
    cfg = PublicHiveBridgeConfig(
        enabled=True,
        meet_seed_urls=("https://seed-eu.example.test:8766",),
        topic_target_url="https://seed-eu.example.test:8766",
        auth_token=None,
    )

    assert public_hive_write_enabled(cfg) is False


def test_public_hive_bridge_discovers_real_auth_token_from_local_watch_config() -> None:
    watch_config = {
        "auth_token": "real-cluster-token",
        "upstream_base_urls": [
            "http://seed-eu.example.test:8766",
            "http://seed-us.example.test:8766",
        ],
    }
    with mock.patch("core.public_hive_bridge.ensure_public_hive_agent_bootstrap", return_value=None), mock.patch(
        "core.public_hive_bridge._load_agent_bootstrap",
        return_value={},
    ), mock.patch(
        "core.public_hive_bridge._discover_local_cluster_bootstrap",
        return_value={
            "auth_token": watch_config["auth_token"],
            "meet_seed_urls": watch_config["upstream_base_urls"],
            "tls_ca_file": "/tmp/cluster-ca.pem",
        },
    ):
        cfg = load_public_hive_bridge_config()

    assert cfg.enabled is True
    assert cfg.auth_token == "real-cluster-token"
    assert cfg.meet_seed_urls == tuple(watch_config["upstream_base_urls"])
    assert cfg.tls_ca_file == "/tmp/cluster-ca.pem"


def test_sync_public_hive_auth_from_ssh_writes_runtime_bootstrap() -> None:
    remote_payload = {
        "auth_token": "real-cluster-token",
        "upstream_base_urls": [
            "https://seed-eu.example.test:8766",
            "https://seed-us.example.test:8766",
        ],
        "tls_ca_file": "/opt/nulla-hive-mind/config/tls/cluster-ca.pem",
        "tls_insecure_skip_verify": False,
    }

    def fake_runner(cmd, capture_output=False, check=False, text=False, timeout=0):  # noqa: ANN001
        assert cmd[0] == "ssh"
        assert "-i" in cmd
        assert capture_output is True
        assert check is True
        assert text is True
        assert timeout == 12
        return type("Completed", (), {"stdout": json.dumps(remote_payload)})()

    with tempfile.TemporaryDirectory() as tmp_dir:
        key_path = Path(tmp_dir) / "cluster_key"
        key_path.write_text("dummy", encoding="utf-8")
        target_path = Path(tmp_dir) / "agent-bootstrap.json"
        result = sync_public_hive_auth_from_ssh(
            ssh_key_path=str(key_path),
            project_root=Path(tmp_dir),
            watch_host="watch.example.test",
            target_path=target_path,
            runner=fake_runner,
        )

        payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert result["auth_loaded"] is True
    assert payload["auth_token"] == "real-cluster-token"
    assert payload["meet_seed_urls"] == remote_payload["upstream_base_urls"]
    assert payload["tls_ca_file"] == remote_payload["tls_ca_file"]


def test_ensure_public_hive_auth_rewrites_tls_ca_file_to_local_project_path() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir) / "bundle"
        tls_dir = project_root / "config" / "meet_clusters" / "do_ip_first_4node" / "tls"
        tls_dir.mkdir(parents=True, exist_ok=True)
        ca_path = tls_dir / "cluster-ca.pem"
        ca_path.write_text("dummy-ca", encoding="utf-8")
        (project_root / "config" / "agent-bootstrap.json").write_text(
            json.dumps(
                {
                    "meet_seed_urls": ["https://seed-eu.example.test:8766"],
                    "auth_token": "bundle-token",
                    "tls_ca_file": "/opt/nulla-hive-mind/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem",
                }
            ),
            encoding="utf-8",
        )
        target_path = Path(tmp_dir) / "runtime" / "agent-bootstrap.json"

        result = ensure_public_hive_auth(project_root=project_root, target_path=target_path)
        payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert payload["tls_ca_file"] == str(ca_path.resolve())


def test_write_public_hive_agent_bootstrap_stores_project_relative_tls_ca_file() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir) / "bundle"
        tls_dir = project_root / "config" / "meet_clusters" / "do_ip_first_4node" / "tls"
        tls_dir.mkdir(parents=True, exist_ok=True)
        ca_path = tls_dir / "cluster-ca.pem"
        ca_path.write_text("dummy-ca", encoding="utf-8")
        target_path = project_root / "config" / "agent-bootstrap.json"

        written = write_public_hive_agent_bootstrap(
            project_root=project_root,
            target_path=target_path,
            meet_seed_urls=["https://seed-eu.example.test:8766"],
            auth_token="bundle-token",
            tls_ca_file="/opt/nulla-hive-mind/config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem",
        )
        payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert written == target_path.resolve()
    assert payload["tls_ca_file"] == "config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem"


def test_ensure_public_hive_auth_hydrates_target_from_bundled_project_config() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir) / "bundle"
        (project_root / "config").mkdir(parents=True, exist_ok=True)
        (project_root / "config" / "agent-bootstrap.json").write_text(
            json.dumps(
                {
                    "meet_seed_urls": ["https://seed-eu.example.test:8766"],
                    "auth_token": "bundle-token",
                    "tls_insecure_skip_verify": True,
                }
            ),
            encoding="utf-8",
        )
        target_path = Path(tmp_dir) / "runtime" / "agent-bootstrap.json"

        result = ensure_public_hive_auth(project_root=project_root, target_path=target_path)

        payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["status"] == "hydrated_from_bundle"
    assert payload["auth_token"] == "bundle-token"
    assert payload["meet_seed_urls"] == ["https://seed-eu.example.test:8766"]


def test_ensure_public_hive_auth_resolves_relative_tls_ca_file_against_project_root() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir) / "bundle"
        tls_dir = project_root / "config" / "meet_clusters" / "do_ip_first_4node" / "tls"
        tls_dir.mkdir(parents=True, exist_ok=True)
        ca_path = tls_dir / "cluster-ca.pem"
        ca_path.write_text("dummy-ca", encoding="utf-8")
        (project_root / "config" / "agent-bootstrap.json").write_text(
            json.dumps(
                {
                    "meet_seed_urls": ["https://seed-eu.example.test:8766"],
                    "auth_token": "bundle-token",
                    "tls_ca_file": "config/meet_clusters/do_ip_first_4node/tls/cluster-ca.pem",
                }
            ),
            encoding="utf-8",
        )
        target_path = Path(tmp_dir) / "runtime" / "agent-bootstrap.json"

        result = ensure_public_hive_auth(project_root=project_root, target_path=target_path)
        payload = json.loads(target_path.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert payload["tls_ca_file"] == str(ca_path.resolve())


def test_ensure_public_hive_auth_marks_ssh_sync_as_ok() -> None:
    remote_payload = {
        "auth_token": "real-cluster-token",
        "upstream_base_urls": ["https://seed-eu.example.test:8766"],
    }

    def fake_runner(cmd, capture_output=False, check=False, text=False, timeout=0):  # noqa: ANN001
        del cmd, capture_output, check, text, timeout
        return type("Completed", (), {"stdout": json.dumps(remote_payload)})()

    with tempfile.TemporaryDirectory() as tmp_dir:
        key_path = Path(tmp_dir) / "cluster_key"
        key_path.write_text("dummy", encoding="utf-8")
        with mock.patch("core.public_hive_bridge.find_public_hive_ssh_key", return_value=key_path), mock.patch(
            "core.public_hive_bridge.subprocess.run",
            side_effect=fake_runner,
        ):
            result = ensure_public_hive_auth(
                project_root=Path(tmp_dir),
                target_path=Path(tmp_dir) / "agent-bootstrap.json",
                require_auth=True,
            )

    assert result["ok"] is True
    assert result["status"] == "synced_from_ssh"


def test_public_hive_bridge_joins_existing_related_topic_before_creating_duplicate() -> None:
    seen: list[tuple[str, str]] = []

    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        del timeout, context
        method = req.get_method()
        seen.append((method, req.full_url))
        if method == "GET" and req.full_url.endswith("/v1/hive/topics?limit=24"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {
                            "topic_id": "topic-remote-1234",
                            "created_by_agent_id": "peer-remote-12345678",
                            "title": "OpenClaw and Liquefy integration audit",
                            "summary": "Research thread about OpenClaw bridge continuity and Liquefy integration.",
                            "topic_tags": ["openclaw", "liquefy", "research"],
                            "status": "open",
                        }
                    ],
                    "error": None,
                }
            )
        if method == "POST" and req.full_url.endswith("/v1/hive/posts"):
            envelope = json.loads(req.data.decode("utf-8"))
            assert envelope["payload"]["topic_id"] == "topic-remote-1234"
            return _FakeResponse({"ok": True, "result": {"post_id": "post-remote-1"}, "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.publish_public_task(
        task_id="task-public-1",
        task_summary="Research OpenClaw and Liquefy integration",
        task_class="research",
        assistant_response="Research thread opened.",
        topic_tags=["openclaw", "liquefy"],
    )

    assert result["ok"] is True
    assert result["status"] == "joined_existing_topic"
    assert result["topic_id"] == "topic-remote-1234"
    assert ("POST", "http://seed-eu.example.test:8766/v1/hive/topics") not in seen


def test_public_hive_bridge_posts_into_existing_agent_commons_topic() -> None:
    seen: list[tuple[str, str]] = []

    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        del timeout, context
        method = req.get_method()
        seen.append((method, req.full_url))
        if method == "GET" and req.full_url.endswith("/v1/hive/topics?limit=48"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {
                            "topic_id": "topic-commons-123456",
                            "created_by_agent_id": "peer-remote-12345678",
                            "title": "Agent Commons: better human-visible watcher and task-flow UX",
                            "summary": "Idle agent commons thread for bounded brainstorming and curiosity.",
                            "topic_tags": ["agent_commons", "brainstorm", "design"],
                            "status": "researching",
                        }
                    ],
                    "error": None,
                }
            )
        if method == "POST" and req.full_url.endswith("/v1/hive/posts"):
            envelope = json.loads(req.data.decode("utf-8"))
            assert envelope["payload"]["topic_id"] == "topic-commons-123456"
            assert "Commons update" in envelope["payload"]["body"]
            return _FakeResponse({"ok": True, "result": {"post_id": "post-commons-1"}, "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    result = bridge.publish_agent_commons_update(
        topic="Agent commons brainstorm: better human-visible watcher and task-flow UX",
        topic_kind="design",
        summary="Focus on clickable topics and live work-flow views.",
        public_body="Agent commons update: clickable topics, live flow, and better research visibility.",
        topic_tags=["agent_commons", "brainstorm", "design"],
    )

    assert result["ok"] is True
    assert result["status"] == "joined_existing_commons_topic"
    assert result["topic_id"] == "topic-commons-123456"
    assert ("POST", "http://seed-eu.example.test:8766/v1/hive/topics") not in seen


def test_public_hive_bridge_claims_posts_progress_and_submits_result() -> None:
    seen: list[tuple[str, str, dict[str, object]]] = []

    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        del timeout, context
        method = req.get_method()
        payload = json.loads(req.data.decode("utf-8")) if getattr(req, "data", None) else {}
        seen.append((method, req.full_url, payload))
        if method == "POST" and req.full_url.endswith("/v1/hive/topic-claims"):
            assert payload["payload"]["topic_id"] == "topic-1234567890abcdef"
            return _FakeResponse({"ok": True, "result": {"claim_id": "claim-1"}, "error": None})
        if method == "POST" and req.full_url.endswith("/v1/hive/posts"):
            post_body = payload["payload"]["body"]
            if "Progress" in post_body:
                return _FakeResponse({"ok": True, "result": {"post_id": "post-progress-1"}, "error": None})
            return _FakeResponse({"ok": True, "result": {"post_id": "post-result-1"}, "error": None})
        if method == "POST" and req.full_url.endswith("/v1/hive/topic-status"):
            assert payload["payload"]["status"] == "solved"
            assert payload["payload"]["claim_id"] == "claim-1"
            return _FakeResponse({"ok": True, "result": {"topic_id": "topic-1234567890abcdef", "status": "solved"}, "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    claim = bridge.claim_public_topic(
        topic_id="topic-1234567890abcdef",
        note="Working it live.",
        capability_tags=["dashboard", "ux"],
    )
    progress = bridge.post_public_topic_progress(
        topic_id="topic-1234567890abcdef",
        body="Progress: watcher now emits real task events.",
        progress_state="working",
        claim_id="claim-1",
    )
    result = bridge.submit_public_topic_result(
        topic_id="topic-1234567890abcdef",
        body="Result: claims, progress, and result submission are all wired.",
        result_status="solved",
        claim_id="claim-1",
    )

    assert claim["ok"] is True
    assert claim["claim_id"] == "claim-1"
    assert progress["ok"] is True
    assert progress["post_id"] == "post-progress-1"
    assert result["ok"] is True
    assert result["post_id"] == "post-result-1"
    assert any(url.endswith("/v1/hive/topic-status") for _method, url, _payload in seen)


def test_public_hive_bridge_attaches_route_scoped_write_grant() -> None:
    local_peer_id = get_local_peer_id()
    post_grant = build_hive_write_grant(
        granted_to=local_peer_id,
        allowed_paths=["/v1/hive/posts"],
        topic_id="topic-1234567890abcdef",
        max_uses=5,
    )
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        del timeout, context
        seen["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse({"ok": True, "result": {"post_id": "post-1"}, "error": None})

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
            write_grants_by_base_url={
                "http://seed-eu.example.test:8766": {
                    "/v1/hive/posts": post_grant,
                }
            },
        ),
        urlopen=fake_urlopen,
    )

    bridge.post_public_topic_progress(
        topic_id="topic-1234567890abcdef",
        body="Progress: scoped grants are attached.",
        progress_state="working",
        claim_id="claim-1",
    )

    envelope = dict(seen["payload"])
    assert envelope["payload"]["write_grant"]["grant_id"] == post_grant["grant_id"]


def test_public_hive_bridge_reads_review_queue_and_submits_review() -> None:
    seen: list[tuple[str, str, dict[str, object]]] = []

    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        del timeout, context
        method = req.get_method()
        payload = json.loads(req.data.decode("utf-8")) if getattr(req, "data", None) else {}
        seen.append((method, req.full_url, payload))
        if method == "GET" and req.full_url.endswith("/v1/hive/review-queue?limit=5&object_type=post"):
            return _FakeResponse({"ok": True, "result": [{"object_type": "post", "object_id": "post-1"}], "error": None})
        if method == "POST" and req.full_url.endswith("/v1/hive/moderation/reviews"):
            return _FakeResponse({"ok": True, "result": {"current_state": "approved", "quorum_reached": True}, "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
            auth_token="cluster-token",
        ),
        urlopen=fake_urlopen,
    )

    queue = bridge.list_public_review_queue(object_type="post", limit=5)
    review = bridge.submit_public_moderation_review(
        object_type="post",
        object_id="post-1",
        decision="approve",
        note="Promote after manual review.",
    )

    assert queue[0]["object_id"] == "post-1"
    assert review["quorum_reached"] is True
    assert seen[-1][2]["payload"]["decision"] == "approve"


def test_public_hive_bridge_reads_research_queue_packet_and_artifacts() -> None:
    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        del timeout, context
        method = req.get_method()
        if method == "GET" and req.full_url.endswith("/v1/hive/research-queue?limit=5"):
            return _FakeResponse({"ok": True, "result": [{"topic_id": "topic-1", "title": "Research queue topic"}], "error": None})
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/research-packet"):
            return _FakeResponse({"ok": True, "result": {"topic": {"topic_id": "topic-1", "title": "Research queue topic"}}, "error": None})
        if method == "GET" and "/v1/hive/artifacts/search?" in req.full_url:
            return _FakeResponse({"ok": True, "result": [{"artifact_id": "artifact-1", "title": "bundle"}], "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
        ),
        urlopen=fake_urlopen,
    )

    queue = bridge.list_public_research_queue(limit=5)
    packet = bridge.get_public_research_packet("topic-1")
    artifacts = bridge.search_public_artifacts(query_text="bundle", topic_id="topic-1", limit=5)

    assert queue[0]["topic_id"] == "topic-1"
    assert packet["topic"]["title"] == "Research queue topic"
    assert artifacts[0]["artifact_id"] == "artifact-1"


def test_public_hive_bridge_falls_back_when_research_queue_route_is_missing() -> None:
    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        del timeout, context
        method = req.get_method()
        if method == "GET" and req.full_url.endswith("/v1/hive/research-queue?limit=5"):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", hdrs=None, fp=None)
        if method == "GET" and req.full_url.endswith("/v1/hive/topics?limit=32"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {
                            "topic_id": "topic-1",
                            "title": "Research queue topic",
                            "summary": "Need a credible first research pass.",
                            "status": "researching",
                            "topic_tags": ["agent_commons", "watcher"],
                            "created_at": "2026-03-09T20:00:00+00:00",
                            "updated_at": "2026-03-09T20:10:00+00:00",
                        }
                    ],
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/posts?limit=120"):
            return _FakeResponse({"ok": True, "result": [{"post_id": "post-1", "body": "First note"}], "error": None})
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/claims?limit=48"):
            return _FakeResponse({"ok": True, "result": [], "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
        ),
        urlopen=fake_urlopen,
    )

    queue = bridge.list_public_research_queue(limit=5)

    assert queue[0]["topic_id"] == "topic-1"
    assert queue[0]["compat_fallback"] is True
    assert queue[0]["packet_schema"] == "brain_hive.research_packet.v1"
    assert queue[0]["truth_source"] == "public_bridge"
    assert queue[0]["truth_label"] == "public-bridge-derived"
    assert queue[0]["truth_transport"] == "compat_fallback"


def test_public_hive_bridge_falls_back_when_research_packet_route_is_missing() -> None:
    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        del timeout, context
        method = req.get_method()
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/research-packet"):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", hdrs=None, fp=None)
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "topic_id": "topic-1",
                        "title": "Research queue topic",
                        "summary": "Need a credible first research pass.",
                        "status": "researching",
                        "topic_tags": ["agent_commons", "watcher"],
                        "created_at": "2026-03-09T20:00:00+00:00",
                        "updated_at": "2026-03-09T20:10:00+00:00",
                    },
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/posts?limit=400"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [{"post_id": "post-1", "post_kind": "analysis", "body": "First note"}],
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/claims?limit=200"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [{"claim_id": "claim-1", "agent_id": "peer-1", "status": "active"}],
                    "error": None,
                }
            )
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
        ),
        urlopen=fake_urlopen,
    )

    packet = bridge.get_public_research_packet("topic-1")

    assert packet["topic"]["topic_id"] == "topic-1"
    assert packet["execution_state"]["active_claim_count"] == 1
    assert packet["compat_fallback"] is True
    assert packet["truth_source"] == "public_bridge"
    assert packet["truth_label"] == "public-bridge-derived"
    assert packet["truth_transport"] == "compat_fallback"


def test_public_hive_bridge_overlays_truth_fields_when_direct_research_packet_is_stale() -> None:
    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        del timeout, context
        method = req.get_method()
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/research-packet"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "topic": {
                            "topic_id": "topic-1",
                            "title": "Research queue topic",
                            "summary": "Need a credible first research pass.",
                            "status": "researching",
                            "topic_tags": ["agent_commons", "watcher"],
                            "created_at": "2026-03-09T20:00:00+00:00",
                            "updated_at": "2026-03-09T20:10:00+00:00",
                        }
                    },
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": {
                        "topic_id": "topic-1",
                        "title": "Research queue topic",
                        "summary": "Need a credible first research pass.",
                        "status": "researching",
                        "topic_tags": ["agent_commons", "watcher"],
                        "created_at": "2026-03-09T20:00:00+00:00",
                        "updated_at": "2026-03-09T20:10:00+00:00",
                    },
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/posts?limit=400"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {
                            "post_id": "post-1",
                            "post_kind": "summary",
                            "body": "Research synthesis card",
                            "created_at": "2026-03-09T20:11:00+00:00",
                            "evidence_refs": [
                                {
                                    "kind": "research_synthesis_card",
                                    "question": "Research queue topic",
                                    "searched": ["research queue topic implementation docs"],
                                    "found": ["Credible evidence should stay visible."],
                                    "source_domains": ["developer.apple.com"],
                                    "artifacts": [{"label": "bundle artifact-1", "state": "resolved"}],
                                    "promoted_findings": ["Credible evidence should stay visible."],
                                    "confidence": "grounded",
                                    "blockers": [],
                                    "state_token": "state-1",
                                }
                            ],
                        }
                    ],
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/claims?limit=200"):
            return _FakeResponse({"ok": True, "result": [], "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
        ),
        urlopen=fake_urlopen,
    )

    packet = bridge.get_public_research_packet("topic-1")

    assert packet["truth_transport"] == "direct_overlay"
    assert packet["latest_synthesis_card"]["question"] == "Research queue topic"
    assert "research_quality_status" in packet


def test_public_hive_bridge_overlays_truth_fields_when_direct_research_queue_is_stale() -> None:
    def fake_urlopen(req, timeout=0, context=None):  # noqa: ANN001
        del timeout, context
        method = req.get_method()
        if method == "GET" and req.full_url.endswith("/v1/hive/research-queue?limit=5"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [{"topic_id": "topic-1", "title": "Research queue topic", "updated_at": "2026-03-09T20:10:00+00:00"}],
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics?limit=32"):
            return _FakeResponse(
                {
                    "ok": True,
                    "result": [
                        {
                            "topic_id": "topic-1",
                            "title": "Research queue topic",
                            "summary": "Need a credible first research pass.",
                            "status": "researching",
                            "topic_tags": ["agent_commons", "watcher"],
                            "created_at": "2026-03-09T20:00:00+00:00",
                            "updated_at": "2026-03-09T20:10:00+00:00",
                        }
                    ],
                    "error": None,
                }
            )
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/posts?limit=120"):
            return _FakeResponse({"ok": True, "result": [{"post_id": "post-1", "body": "First note"}], "error": None})
        if method == "GET" and req.full_url.endswith("/v1/hive/topics/topic-1/claims?limit=48"):
            return _FakeResponse({"ok": True, "result": [], "error": None})
        raise AssertionError(f"Unexpected request: {method} {req.full_url}")

    bridge = PublicHiveBridge(
        PublicHiveBridgeConfig(
            enabled=True,
            meet_seed_urls=("http://seed-eu.example.test:8766",),
            topic_target_url="http://seed-eu.example.test:8766",
            home_region="eu",
        ),
        urlopen=fake_urlopen,
    )

    queue = bridge.list_public_research_queue(limit=5)

    assert queue[0]["truth_transport"] == "direct_overlay"
    assert "research_quality_status" in queue[0]


def test_agent_start_uses_limited_presence_when_hive_task_intake_is_disabled() -> None:
    agent = NullaAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    disabled_prefs = type("Prefs", (), {"accept_hive_tasks": False})()

    with mock.patch("apps.nulla_agent.load_preferences", return_value=disabled_prefs), mock.patch.object(
        agent.public_hive_bridge,
        "sync_presence",
        return_value={"ok": True, "status": "posted"},
    ) as sync_presence, mock.patch.object(agent, "_start_public_presence_heartbeat", return_value=None):
        agent.start()

    assert sync_presence.call_args.kwargs["status"] == "limited"


def test_agent_only_exports_public_tasks_to_public_hive() -> None:
    agent = NullaAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")

    public_task = type(
        "Task",
        (),
        {
            "task_id": "task-public-1",
            "task_summary": "Research OpenClaw and Liquefy integration",
            "share_scope": "public_knowledge",
        },
    )()
    private_task = type(
        "Task",
        (),
        {
            "task_id": "task-private-1",
            "task_summary": "Research OpenClaw and Liquefy integration",
            "share_scope": "local_only",
        },
    )()

    with mock.patch.object(agent.public_hive_bridge, "publish_public_task", return_value={"ok": True}) as publish:
        agent._maybe_publish_public_task(
            task=public_task,
            classification={"task_class": "research", "topic_hints": ["openclaw", "liquefy"]},
            assistant_response="Research thread opened.",
            session_id="openclaw:public-export",
        )
        publish.assert_called_once()

    with mock.patch.object(agent.public_hive_bridge, "publish_public_task", return_value={"ok": True}) as publish:
        agent._maybe_publish_public_task(
            task=private_task,
            classification={"task_class": "research", "topic_hints": ["openclaw", "liquefy"]},
            assistant_response="Research thread opened.",
            session_id="openclaw:private-export",
        )
        publish.assert_not_called()
