from __future__ import annotations

import os
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.public_hive import PublicHiveBridgeConfig
from core.public_hive import auth as public_hive_auth
from core.public_hive import client as public_hive_client
from core.public_hive import presence as public_hive_presence
from core.public_hive import reads as public_hive_reads
from core.public_hive import social as public_hive_social
from core.public_hive import truth as public_hive_truth
from core.public_hive import writes as public_hive_writes
from core.runtime_paths import PROJECT_ROOT, config_path

_UNSET_SENTINEL = object()


class PublicHiveBridge:
    def __init__(
        self,
        config: PublicHiveBridgeConfig | None = None,
        *,
        urlopen: Any | None = None,
    ) -> None:
        self.config = config or load_public_hive_bridge_config()
        self._urlopen = urlopen or urllib.request.urlopen
        self._nullabook_token: str | None = _UNSET_SENTINEL
        self._client = public_hive_client.PublicHiveHttpClient(
            self.config,
            urlopen=self._urlopen,
            nullabook_token_fn=self._get_nullabook_token,
        )

    def _get_nullabook_token(self) -> str | None:
        if self._nullabook_token is _UNSET_SENTINEL:
            try:
                from core.nullabook_identity import load_local_token
                self._nullabook_token = load_local_token()
            except Exception:
                self._nullabook_token = None
        return self._nullabook_token

    def enabled(self) -> bool:
        return bool(self.config.enabled and self.config.meet_seed_urls)

    def auth_configured(self) -> bool:
        return public_hive_has_auth(self.config)

    def write_enabled(self) -> bool:
        return public_hive_write_enabled(self.config)

    def sync_presence(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str = "idle",
        transport_mode: str = "nulla_agent",
    ) -> dict[str, Any]:
        return public_hive_presence.sync_presence(
            self,
            agent_name=agent_name,
            capabilities=capabilities,
            status=status,
            transport_mode=transport_mode,
        )

    def heartbeat_presence(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str = "idle",
        transport_mode: str = "nulla_agent",
    ) -> dict[str, Any]:
        return public_hive_presence.heartbeat_presence(
            self,
            agent_name=agent_name,
            capabilities=capabilities,
            status=status,
            transport_mode=transport_mode,
        )

    def list_public_topics(
        self,
        *,
        limit: int = 24,
        statuses: tuple[str, ...] = ("open", "researching", "disputed", "partial", "needs_improvement"),
    ) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_topics(self, limit=limit, statuses=statuses)

    def get_public_topic(
        self,
        topic_id: str,
        *,
        include_flagged: bool = True,
    ) -> dict[str, Any] | None:
        return public_hive_reads.get_public_topic(self, topic_id, include_flagged=include_flagged)

    def list_public_research_queue(self, *, limit: int = 24) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_research_queue(self, limit=limit)

    def list_public_review_queue(self, *, object_type: str | None = None, limit: int = 24) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_review_queue(self, object_type=object_type, limit=limit)

    def get_public_research_packet(self, topic_id: str) -> dict[str, Any]:
        return public_hive_reads.get_public_research_packet(self, topic_id)

    def _build_research_queue_fallback(self, *, limit: int) -> list[dict[str, Any]]:
        return public_hive_reads.build_research_queue_fallback(self, limit=limit)

    def _build_research_packet_fallback(self, topic_id: str) -> dict[str, Any]:
        return public_hive_reads.build_research_packet_fallback(self, topic_id)

    def _overlay_research_queue_truth(self, rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        return public_hive_reads.overlay_research_queue_truth(self, rows, limit=limit)

    def _overlay_research_packet_truth(self, topic_id: str, direct_packet: dict[str, Any]) -> dict[str, Any]:
        return public_hive_reads.overlay_research_packet_truth(self, topic_id, direct_packet)

    def _get_public_topic(self, topic_id: str) -> dict[str, Any]:
        return public_hive_reads.get_public_topic_raw(self, topic_id)

    def _list_public_topic_posts(self, topic_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_topic_posts(self, topic_id, limit=limit)

    def _list_public_topic_claims(self, topic_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return public_hive_reads.list_public_topic_claims(self, topic_id, limit=limit)

    def _topic_result_settlement_helpers(
        self,
        *,
        topic_id: str,
        claim_id: str,
    ) -> list[str]:
        return public_hive_writes.topic_result_settlement_helpers(self, topic_id=topic_id, claim_id=claim_id)

    def search_public_artifacts(
        self,
        *,
        query_text: str,
        topic_id: str | None = None,
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        return public_hive_reads.search_public_artifacts(self, query_text=query_text, topic_id=topic_id, limit=limit)

    def submit_public_moderation_review(
        self,
        *,
        object_type: str,
        object_id: str,
        decision: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.submit_public_moderation_review(
            self,
            object_type=object_type,
            object_id=object_id,
            decision=decision,
            note=note,
        )

    def get_public_review_summary(
        self,
        *,
        object_type: str,
        object_id: str,
    ) -> dict[str, Any]:
        return public_hive_reads.get_public_review_summary(self, object_type=object_type, object_id=object_id)

    def update_public_topic_status(
        self,
        *,
        topic_id: str,
        status: str,
        note: str | None = None,
        claim_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.update_public_topic_status(
            self,
            topic_id=topic_id,
            status=status,
            note=note,
            claim_id=claim_id,
            idempotency_key=idempotency_key,
        )

    def update_public_topic(
        self,
        *,
        topic_id: str,
        title: str | None = None,
        summary: str | None = None,
        topic_tags: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.update_public_topic(
            self,
            topic_id=topic_id,
            title=title,
            summary=summary,
            topic_tags=topic_tags,
            idempotency_key=idempotency_key,
        )

    def delete_public_topic(
        self,
        *,
        topic_id: str,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.delete_public_topic(
            self,
            topic_id=topic_id,
            note=note,
            idempotency_key=idempotency_key,
        )

    def create_public_topic(
        self,
        *,
        title: str,
        summary: str,
        topic_tags: list[str] | None = None,
        status: str = "open",
        visibility: str = "read_public",
        evidence_mode: str = "candidate_only",
        linked_task_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.create_public_topic(
            self,
            title=title,
            summary=summary,
            topic_tags=topic_tags,
            status=status,
            visibility=visibility,
            evidence_mode=evidence_mode,
            linked_task_id=linked_task_id,
            idempotency_key=idempotency_key,
        )

    def claim_public_topic(
        self,
        *,
        topic_id: str,
        note: str | None = None,
        capability_tags: list[str] | None = None,
        status: str = "active",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.claim_public_topic(
            self,
            topic_id=topic_id,
            note=note,
            capability_tags=capability_tags,
            status=status,
            idempotency_key=idempotency_key,
        )

    def post_public_topic_progress(
        self,
        *,
        topic_id: str,
        body: str,
        progress_state: str = "working",
        claim_id: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.post_public_topic_progress(
            self,
            topic_id=topic_id,
            body=body,
            progress_state=progress_state,
            claim_id=claim_id,
            evidence_refs=evidence_refs,
            idempotency_key=idempotency_key,
        )

    def submit_public_topic_result(
        self,
        *,
        topic_id: str,
        body: str,
        result_status: str = "solved",
        post_kind: str = "verdict",
        claim_id: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.submit_public_topic_result(
            self,
            topic_id=topic_id,
            body=body,
            result_status=result_status,
            post_kind=post_kind,
            claim_id=claim_id,
            evidence_refs=evidence_refs,
            idempotency_key=idempotency_key,
        )

    def publish_public_task(
        self,
        *,
        task_id: str,
        task_summary: str,
        task_class: str,
        assistant_response: str,
        topic_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.publish_public_task(
            self,
            task_id=task_id,
            task_summary=task_summary,
            task_class=task_class,
            assistant_response=assistant_response,
            topic_tags=topic_tags,
        )

    def sync_nullabook_profile(
        self,
        *,
        peer_id: str,
        handle: str,
        bio: str = "",
        display_name: str = "",
        twitter_handle: str = "",
    ) -> dict[str, Any]:
        return public_hive_social.sync_nullabook_profile(
            self,
            peer_id=peer_id,
            handle=handle,
            bio=bio,
            display_name=display_name,
            twitter_handle=twitter_handle,
        )

    def sync_nullabook_post(
        self,
        *,
        peer_id: str,
        handle: str,
        bio: str,
        content: str,
        post_type: str = "social",
        twitter_handle: str = "",
        display_name: str = "",
    ) -> dict[str, Any]:
        return public_hive_social.sync_nullabook_post(
            self,
            peer_id=peer_id,
            handle=handle,
            bio=bio,
            content=content,
            post_type=post_type,
            twitter_handle=twitter_handle,
            display_name=display_name,
        )

    def publish_agent_commons_update(
        self,
        *,
        topic: str,
        topic_kind: str,
        summary: str,
        public_body: str,
        topic_tags: list[str] | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.publish_agent_commons_update(
            self,
            topic=topic,
            topic_kind=topic_kind,
            summary=summary,
            public_body=public_body,
            topic_tags=topic_tags,
        )

    def _presence_request(
        self,
        *,
        agent_name: str,
        capabilities: list[str],
        status: str,
        transport_mode: str,
    ) -> Any:
        return public_hive_presence.build_presence_request(
            self,
            agent_name=agent_name,
            capabilities=capabilities,
            status=status,
            transport_mode=transport_mode,
        )

    def _post_many(
        self,
        route: str,
        *,
        payload: dict[str, Any],
        base_urls: tuple[str, ...],
    ) -> dict[str, Any]:
        return self._client.post_many(route, payload=payload, base_urls=base_urls)

    def _get_json(self, base_url: str, route: str) -> Any:
        return self._client.get_json(base_url, route)

    def _post_json(self, base_url: str, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._client.post_json(base_url, route, payload)

    def _find_related_topic(
        self,
        *,
        task_summary: str,
        task_class: str,
        topic_tags: list[str],
    ) -> dict[str, Any] | None:
        return public_hive_writes.find_related_topic(
            self,
            task_summary=task_summary,
            task_class=task_class,
            topic_tags=topic_tags,
        )

    def _find_agent_commons_topic(
        self,
        *,
        topic: str,
        topic_kind: str,
        topic_tags: list[str],
    ) -> dict[str, Any] | None:
        return public_hive_writes.find_agent_commons_topic(
            self,
            topic=topic,
            topic_kind=topic_kind,
            topic_tags=topic_tags,
        )

    def _post_topic_update(
        self,
        *,
        topic_id: str,
        body: str,
        post_kind: str,
        stance: str,
        evidence_refs: list[dict[str, Any]] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.post_topic_update(
            self,
            topic_id=topic_id,
            body=body,
            post_kind=post_kind,
            stance=stance,
            evidence_refs=evidence_refs,
            idempotency_key=idempotency_key,
        )

    def _update_topic_status(
        self,
        *,
        topic_id: str,
        status: str,
        note: str | None = None,
        claim_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return public_hive_writes.update_topic_status(
            self,
            topic_id=topic_id,
            status=status,
            note=note,
            claim_id=claim_id,
            idempotency_key=idempotency_key,
        )

    def _auth_token_for_url(self, url: str) -> str | None:
        return self._client.auth_token_for_url(url)

    def _write_grant_for_request(self, base_url: str, route: str) -> dict[str, Any] | None:
        return self._client.write_grant_for_request(base_url, route)

    def _ssl_context_for_url(self, url: str) -> ssl.SSLContext | None:
        return self._client.ssl_context_for_url(url)


def load_public_hive_bridge_config() -> PublicHiveBridgeConfig:
    return public_hive_auth.load_public_hive_bridge_config(
        ensure_public_hive_agent_bootstrap_fn=ensure_public_hive_agent_bootstrap,
        load_json_file_fn=_load_json_file,
        load_agent_bootstrap_fn=_load_agent_bootstrap,
        discover_local_cluster_bootstrap_fn=_discover_local_cluster_bootstrap,
        split_csv_fn=_split_csv,
        json_env_object_fn=_json_env_object,
        merge_auth_tokens_by_base_url_fn=_merge_auth_tokens_by_base_url,
        json_env_write_grants_fn=_json_env_write_grants,
        merge_write_grants_by_base_url_fn=_merge_write_grants_by_base_url,
        clean_token_fn=_clean_token,
        config_path_fn=config_path,
        project_root=PROJECT_ROOT,
        env=os.environ,
    )


def ensure_public_hive_agent_bootstrap() -> Path | None:
    return public_hive_auth.ensure_public_hive_agent_bootstrap(
        split_csv_fn=_split_csv,
        clean_token_fn=_clean_token,
        json_env_object_fn=_json_env_object,
        json_env_write_grants_fn=_json_env_write_grants,
        load_agent_bootstrap_fn=_load_agent_bootstrap,
        discover_local_cluster_bootstrap_fn=_discover_local_cluster_bootstrap,
        merge_write_grants_by_base_url_fn=_merge_write_grants_by_base_url,
    )


def _load_agent_bootstrap(*, include_runtime: bool = True) -> dict[str, Any]:
    return public_hive_auth.load_agent_bootstrap(
        include_runtime=include_runtime,
        agent_bootstrap_paths_fn=_agent_bootstrap_paths,
    )


def _agent_bootstrap_paths(*, include_runtime: bool) -> tuple[Path, ...]:
    return public_hive_auth.agent_bootstrap_paths(
        include_runtime=include_runtime,
        config_path_fn=config_path,
    )


def write_public_hive_agent_bootstrap(
    *,
    target_path: Path | None = None,
    project_root: str | Path | None = None,
    meet_seed_urls: list[str] | tuple[str, ...] | None = None,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    write_grants_by_base_url: dict[str, dict[str, dict[str, Any]]] | None = None,
    home_region: str | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool | None = None,
) -> Path | None:
    return public_hive_auth.write_public_hive_agent_bootstrap(
        target_path=target_path,
        project_root=project_root,
        meet_seed_urls=meet_seed_urls,
        auth_token=auth_token,
        auth_tokens_by_base_url=auth_tokens_by_base_url,
        write_grants_by_base_url=write_grants_by_base_url,
        home_region=home_region,
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
        config_path_fn=config_path,
        project_root_default=PROJECT_ROOT,
        load_json_file_fn=_load_json_file,
        load_agent_bootstrap_fn=_load_agent_bootstrap,
        discover_local_cluster_bootstrap_fn=_discover_local_cluster_bootstrap,
        resolve_local_tls_ca_file_fn=_resolve_local_tls_ca_file,
        normalize_base_url_fn=_normalize_base_url,
        clean_token_fn=_clean_token,
        merge_write_grants_by_base_url_fn=_merge_write_grants_by_base_url,
    )


def sync_public_hive_auth_from_ssh(
    *,
    ssh_key_path: str,
    project_root: str | Path | None = None,
    watch_host: str = "",
    watch_user: str = "root",
    remote_config_path: str = "",
    target_path: Path | None = None,
    runner: Any | None = None,
) -> dict[str, Any]:
    return public_hive_auth.sync_public_hive_auth_from_ssh(
        ssh_key_path=ssh_key_path,
        project_root=project_root,
        watch_host=watch_host,
        watch_user=watch_user,
        remote_config_path=remote_config_path,
        target_path=target_path,
        runner=runner or subprocess.run,
        clean_token_fn=_clean_token,
        write_public_hive_agent_bootstrap_fn=write_public_hive_agent_bootstrap,
    )


def _split_csv(value: str) -> list[str]:
    return public_hive_auth.split_csv(value)


def _load_json_file(path: Path) -> dict[str, Any]:
    return public_hive_auth.load_json_file(path)


def public_hive_has_auth(config: PublicHiveBridgeConfig | None = None, *, payload: dict[str, Any] | None = None) -> bool:
    return public_hive_auth.public_hive_has_auth(config, payload=payload)


def public_hive_write_requires_auth(
    config: PublicHiveBridgeConfig | None = None,
    *,
    seed_urls: list[str] | tuple[str, ...] | None = None,
    topic_target_url: str | None = None,
) -> bool:
    return public_hive_auth.public_hive_write_requires_auth(
        config,
        seed_urls=seed_urls,
        topic_target_url=topic_target_url,
    )


def public_hive_write_enabled(config: PublicHiveBridgeConfig | None = None) -> bool:
    return public_hive_auth.public_hive_write_enabled(
        config,
        load_public_hive_bridge_config_fn=load_public_hive_bridge_config,
    )


def _annotate_public_hive_truth(row: dict[str, Any]) -> dict[str, Any]:
    return public_hive_truth.annotate_public_hive_truth(row)


def _annotate_public_hive_packet_truth(packet: dict[str, Any]) -> dict[str, Any]:
    return public_hive_truth.annotate_public_hive_packet_truth(packet)


def _research_queue_truth_complete(row: dict[str, Any]) -> bool:
    return public_hive_truth.research_queue_truth_complete(row)


def _research_packet_truth_complete(packet: dict[str, Any]) -> bool:
    return public_hive_truth.research_packet_truth_complete(packet)


def _resolve_local_tls_ca_file(tls_ca_file: str | None, *, project_root: str | Path | None = None) -> str | None:
    return public_hive_auth.resolve_local_tls_ca_file(tls_ca_file, project_root=project_root or PROJECT_ROOT)


def find_public_hive_ssh_key(project_root: str | Path | None = None) -> Path | None:
    return public_hive_auth.find_public_hive_ssh_key(project_root=project_root)


def ensure_public_hive_auth(
    *,
    project_root: str | Path | None = None,
    target_path: Path | None = None,
    watch_host: str | None = None,
    watch_user: str = "root",
    remote_config_path: str = "",
    require_auth: bool = False,
) -> dict[str, Any]:
    return public_hive_auth.ensure_public_hive_auth(
        project_root=project_root,
        target_path=target_path,
        watch_host=watch_host,
        watch_user=watch_user,
        remote_config_path=remote_config_path,
        require_auth=require_auth,
        load_json_file_fn=_load_json_file,
        discover_local_cluster_bootstrap_fn=_discover_local_cluster_bootstrap,
        load_agent_bootstrap_fn=_load_agent_bootstrap,
        clean_token_fn=_clean_token,
        json_env_object_fn=_json_env_object,
        normalize_base_url_fn=_normalize_base_url,
        public_hive_has_auth_fn=public_hive_has_auth,
        public_hive_write_requires_auth_fn=public_hive_write_requires_auth,
        write_public_hive_agent_bootstrap_fn=write_public_hive_agent_bootstrap,
        find_public_hive_ssh_key_fn=find_public_hive_ssh_key,
        sync_public_hive_auth_from_ssh_fn=sync_public_hive_auth_from_ssh,
    )


def _discover_local_cluster_bootstrap(*, project_root: str | Path | None = None) -> dict[str, Any]:
    return public_hive_auth.discover_local_cluster_bootstrap(
        project_root=project_root,
        load_json_file_fn=_load_json_file,
        clean_token_fn=_clean_token,
        normalize_base_url_fn=_normalize_base_url,
    )


def _json_env_object(value: str) -> dict[str, str]:
    return public_hive_auth.json_env_object(value)


def _json_env_write_grants(value: str) -> dict[str, dict[str, dict[str, Any]]]:
    return public_hive_auth.json_env_write_grants(value)


def _merge_auth_tokens_by_base_url(raw: dict[str, Any]) -> dict[str, str]:
    return public_hive_auth.merge_auth_tokens_by_base_url(raw)


def _merge_write_grants_by_base_url(raw: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    return public_hive_auth.merge_write_grants_by_base_url(raw)


def _clean_token(value: str) -> str | None:
    return public_hive_auth.clean_token(value)


def _url_requires_auth(url: str) -> bool:
    return public_hive_auth.url_requires_auth(url)


def _normalize_base_url(url: str) -> str:
    return public_hive_auth.normalize_base_url(url)


def _normalize_presence_status(value: str) -> str:
    return public_hive_truth.normalize_presence_status(value)


def _task_title(task_summary: str) -> str:
    return public_hive_truth.task_title(task_summary)


def _topic_tags(*, task_class: str, text: str, extra: list[str] | None = None) -> list[str]:
    return public_hive_truth.topic_tags(task_class=task_class, text=text, extra=extra)


def _public_post_body(response: str) -> str:
    return public_hive_truth.public_post_body(response)


def _fallback_public_post_body(*, task_summary: str, task_class: str) -> str:
    return public_hive_truth.fallback_public_post_body(task_summary=task_summary, task_class=task_class)


def _commons_topic_title(topic: str) -> str:
    return public_hive_truth.commons_topic_title(topic)


def _commons_topic_summary(*, topic: str, summary: str) -> str:
    return public_hive_truth.commons_topic_summary(topic=topic, summary=summary)


def _commons_post_body(*, topic: str, summary: str, public_body: str) -> str:
    return public_hive_truth.commons_post_body(topic=topic, summary=summary, public_body=public_body)


def _topic_match_score(
    *,
    task_summary: str,
    task_class: str,
    topic_tags: list[str],
    topic: dict[str, Any],
) -> int:
    return public_hive_truth.topic_match_score(
        task_summary=task_summary,
        task_class=task_class,
        topic_tags=topic_tags,
        topic=topic,
    )


def _content_tokens(text: str) -> list[str]:
    return public_hive_truth.content_tokens(text)


def _http_error_detail(exc: urllib.error.HTTPError, *, fallback: str) -> str:
    return public_hive_truth.http_error_detail(exc, fallback=fallback)


def _route_missing(exc: Exception) -> bool:
    return public_hive_truth.route_missing(exc)
