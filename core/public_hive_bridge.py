from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from core.public_hive import PublicHiveBridgeConfig
from core.public_hive import auth as public_hive_auth
from core.public_hive.bridge import PublicHiveBridge
from core.runtime_paths import PROJECT_ROOT, config_path

__all__ = [
    "PublicHiveBridge",
    "PublicHiveBridgeConfig",
    "ensure_public_hive_auth",
    "load_public_hive_bridge_config",
    "public_hive_write_enabled",
    "sync_public_hive_auth_from_ssh",
    "write_public_hive_agent_bootstrap",
]


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
