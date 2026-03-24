from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from core.public_hive import PublicHiveBridgeConfig
from core.public_hive import auth as public_hive_auth
from core.public_hive import bridge_support as _public_hive_bridge_support
from core.public_hive.bridge import PublicHiveBridge
from core.public_hive.bridge_facade_auth import (
    public_hive_has_auth as _public_hive_has_auth,
)
from core.public_hive.bridge_facade_auth import (
    public_hive_write_enabled as _public_hive_write_enabled,
)
from core.public_hive.bridge_facade_auth import (
    public_hive_write_requires_auth as _public_hive_write_requires_auth,
)
from core.public_hive.bridge_facade_auth import (
    url_requires_auth as _url_requires_auth_impl,
)
from core.public_hive.bridge_facade_bootstrap import (
    ensure_public_hive_auth as _ensure_public_hive_auth,
)
from core.public_hive.bridge_facade_bootstrap import (
    sync_public_hive_auth_from_ssh as _sync_public_hive_auth_from_ssh,
)
from core.public_hive.bridge_facade_bootstrap import (
    write_public_hive_agent_bootstrap as _write_public_hive_agent_bootstrap,
)
from core.public_hive.bridge_facade_config import load_public_hive_bridge_config as _load_public_hive_bridge_config
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
    return _load_public_hive_bridge_config(
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


_load_agent_bootstrap = _public_hive_bridge_support.load_agent_bootstrap
_load_json_file = _public_hive_bridge_support.load_json_file
_split_csv = _public_hive_bridge_support.split_csv
_json_env_object = _public_hive_bridge_support.json_env_object
_json_env_write_grants = _public_hive_bridge_support.json_env_write_grants
_merge_auth_tokens_by_base_url = _public_hive_bridge_support.merge_auth_tokens_by_base_url
_merge_write_grants_by_base_url = _public_hive_bridge_support.merge_write_grants_by_base_url
_clean_token = _public_hive_bridge_support.clean_token
_normalize_base_url = _public_hive_bridge_support.normalize_base_url
_resolve_local_tls_ca_file = _public_hive_bridge_support.resolve_local_tls_ca_file
_discover_local_cluster_bootstrap = _public_hive_bridge_support.discover_local_cluster_bootstrap
find_public_hive_ssh_key = _public_hive_bridge_support.find_public_hive_ssh_key


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
    return _write_public_hive_agent_bootstrap(
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
    return _sync_public_hive_auth_from_ssh(
        ssh_key_path=ssh_key_path,
        project_root=project_root,
        watch_host=watch_host,
        watch_user=watch_user,
        remote_config_path=remote_config_path,
        target_path=target_path,
        runner=runner or subprocess.run,
        clean_token_fn=_clean_token,
        write_public_hive_agent_bootstrap_fn=write_public_hive_agent_bootstrap,
        sync_public_hive_auth_from_ssh_impl=_public_hive_bridge_support.sync_public_hive_auth_from_ssh,
    )


public_hive_has_auth = _public_hive_has_auth
public_hive_write_requires_auth = _public_hive_write_requires_auth


def public_hive_write_enabled(config: PublicHiveBridgeConfig | None = None) -> bool:
    return _public_hive_write_enabled(
        config,
        load_public_hive_bridge_config_fn=load_public_hive_bridge_config,
    )


def _discover_local_cluster_bootstrap(*, project_root: str | Path | None = None) -> dict[str, Any]:
    return _public_hive_bridge_support.discover_local_cluster_bootstrap(
        project_root=project_root,
        load_json_file_fn=_load_json_file,
        clean_token_fn=_clean_token,
        normalize_base_url_fn=_normalize_base_url,
    )


def ensure_public_hive_auth(
    *,
    project_root: str | Path | None = None,
    target_path: Path | None = None,
    watch_host: str | None = None,
    watch_user: str = "root",
    remote_config_path: str = "",
    require_auth: bool = False,
) -> dict[str, Any]:
    return _ensure_public_hive_auth(
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
_url_requires_auth = _url_requires_auth_impl
