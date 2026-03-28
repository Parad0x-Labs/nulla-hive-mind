from __future__ import annotations

import os
from collections.abc import Mapping

from core.hardware_tier import probe_machine, select_qwen_tier
from core.model_registry import ModelRegistry
from core.runtime_install_profiles import normalize_install_profile_id, required_ollama_models_for_profile
from storage.model_provider_manifest import ModelProviderManifest

_DEFAULT_KIMI_BASE_URL = "https://api.moonshot.ai/v1"
_DEFAULT_KIMI_MODEL = "kimi-k2"
_KIMI_API_KEY_ENV_NAMES = ("KIMI_API_KEY", "MOONSHOT_API_KEY", "NULLA_KIMI_API_KEY")
_KIMI_BASE_URL_ENV_NAMES = ("KIMI_BASE_URL", "NULLA_KIMI_BASE_URL", "MOONSHOT_BASE_URL")
_KIMI_MODEL_ENV_NAMES = ("KIMI_MODEL", "NULLA_KIMI_MODEL", "MOONSHOT_MODEL")
_DEFAULT_VLLM_BASE_URL = "http://127.0.0.1:8000/v1"
_DEFAULT_VLLM_CONTEXT_WINDOW = 131072
_DEFAULT_LLAMACPP_BASE_URL = "http://127.0.0.1:8080/v1"
_DEFAULT_LLAMACPP_CONTEXT_WINDOW = 32768


def default_runtime_model_tag() -> str:
    return str(select_qwen_tier(probe_machine()).ollama_tag or "").strip() or "qwen2.5:7b"


def ensure_default_runtime_providers(
    registry: ModelRegistry,
    *,
    model_tag: str | None = None,
    env: Mapping[str, str] | None = None,
    install_profile: str | None = None,
) -> tuple[str, ...]:
    env_map = os.environ if env is None else env
    changed: list[str] = []
    local_model = str(model_tag or "").strip() or default_runtime_model_tag()
    active_profile = normalize_install_profile_id(install_profile, allow_auto=False)
    if _ensure_local_ollama_provider(registry, model_tag=local_model):
        changed.append(f"ollama-local:{local_model}")
    for extra_model in required_ollama_models_for_profile(profile_id=active_profile, model_tag=local_model):
        if not extra_model or extra_model == local_model:
            continue
        if _ensure_local_ollama_provider(registry, model_tag=extra_model):
            changed.append(f"ollama-local:{extra_model}")
    if _profile_allows_aux_local_providers(active_profile):
        llamacpp_provider_id = _ensure_llamacpp_provider(registry, model_name=local_model, env=env_map)
        if llamacpp_provider_id:
            changed.append(llamacpp_provider_id)
        vllm_provider_id = _ensure_vllm_provider(registry, model_name=local_model, env=env_map)
        if vllm_provider_id:
            changed.append(vllm_provider_id)
    if _profile_allows_kimi_provider(active_profile):
        kimi_provider_id = _ensure_kimi_provider(registry, env=env_map)
        if kimi_provider_id:
            changed.append(kimi_provider_id)
    return tuple(changed)


def _ensure_local_ollama_provider(registry: ModelRegistry, *, model_tag: str) -> bool:
    existing = registry.get_manifest("ollama-local", model_tag)
    existing_caps_raw = getattr(existing, "capabilities", ()) if existing is not None else ()
    if not isinstance(existing_caps_raw, (list, tuple, set)):
        existing_caps_raw = ()
    existing_caps = {str(item).strip().lower() for item in existing_caps_raw}
    has_license = bool(
        str(getattr(existing, "license_name", None) or "").strip()
        and str(getattr(existing, "resolved_license_reference", None) or "").strip()
    )
    if existing and existing.enabled and "tool_intent" in existing_caps and has_license:
        return False
    parameter_size = parameter_size_for_model(model_tag)
    manifest = ModelProviderManifest(
        provider_name="ollama-local",
        model_name=model_tag,
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/main/LICENSE",
        license_url_or_reference="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/main/LICENSE",
        weight_location="external",
        runtime_dependency="ollama",
        notes=f"Local Qwen via Ollama ({parameter_size}) — auto-registered by NULLA runtime",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "structured_json", "tool_intent"],
        runtime_config={
            "base_url": "http://127.0.0.1:11434",
            "api_path": "/v1/chat/completions",
            "health_path": "/v1/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.7,
            "supports_json_mode": False,
        },
        metadata={
            "runtime_family": "ollama",
            "confidence_baseline": 0.65,
            "parameter_count": parameter_size,
            "orchestration_role": "drone",
            "deployment_class": "local",
            "tool_support": ["structured_json", "tool_calls"],
            "max_safe_concurrency": 1,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return True


def _ensure_kimi_provider(
    registry: ModelRegistry,
    *,
    env: Mapping[str, str],
) -> str:
    api_key = _env_first(env, *_KIMI_API_KEY_ENV_NAMES)
    if not api_key:
        return ""
    api_key_env = next((name for name in _KIMI_API_KEY_ENV_NAMES if str(env.get(name) or "").strip()), "KIMI_API_KEY")
    model_name = _env_first(env, *_KIMI_MODEL_ENV_NAMES) or _DEFAULT_KIMI_MODEL
    existing = registry.get_manifest("kimi-remote", model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    if existing and existing.enabled and has_base_url:
        return existing.provider_id
    base_url = _env_first(env, *_KIMI_BASE_URL_ENV_NAMES) or _DEFAULT_KIMI_BASE_URL
    manifest = ModelProviderManifest(
        provider_name="kimi-remote",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-openai-compatible-provider",
        notes="Kimi via Moonshot OpenAI-compatible API — auto-registered when a Kimi/Moonshot API key is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.3,
            "supports_json_mode": True,
            "api_key_env": api_key_env,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.78,
            "orchestration_role": "queen",
            "deployment_class": "remote",
            "context_window": 128000,
            "tool_support": ["structured_json", "code_complex"],
            "max_safe_concurrency": 2,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_vllm_provider(
    registry: ModelRegistry,
    *,
    model_name: str,
    env: Mapping[str, str],
) -> str:
    base_url = _env_first(env, "VLLM_BASE_URL", "NULLA_VLLM_BASE_URL")
    if not base_url:
        return ""
    resolved_model_name = _env_first(env, "VLLM_MODEL", "NULLA_VLLM_MODEL") or model_name or default_runtime_model_tag()
    existing = registry.get_manifest("vllm-local", resolved_model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    if existing and existing.enabled and has_base_url:
        return existing.provider_id
    context_window = _env_int(
        env,
        "VLLM_CONTEXT_WINDOW",
        "NULLA_VLLM_CONTEXT_WINDOW",
        default=_DEFAULT_VLLM_CONTEXT_WINDOW,
    )
    max_safe_concurrency = _env_int(
        env,
        "VLLM_MAX_SAFE_CONCURRENCY",
        "NULLA_VLLM_MAX_SAFE_CONCURRENCY",
        default=2,
    )
    manifest = ModelProviderManifest(
        provider_name="vllm-local",
        model_name=resolved_model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        runtime_dependency="vllm",
        notes="Local vLLM OpenAI-compatible lane — auto-registered when VLLM_BASE_URL is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "code_complex", "structured_json", "long_context"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.4,
            "supports_json_mode": True,
            "context_window": context_window,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.74,
            "orchestration_role": "queen",
            "deployment_class": "local",
            "context_window": context_window,
            "tool_support": ["structured_json", "tool_calls", "code_complex"],
            "max_safe_concurrency": max_safe_concurrency,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def _ensure_llamacpp_provider(
    registry: ModelRegistry,
    *,
    model_name: str,
    env: Mapping[str, str],
) -> str:
    base_url = _env_first(
        env,
        "LLAMACPP_BASE_URL",
        "NULLA_LLAMACPP_BASE_URL",
        "LLAMA_CPP_BASE_URL",
        "NULLA_LLAMA_CPP_BASE_URL",
    )
    if not base_url:
        return ""
    resolved_model_name = _env_first(
        env,
        "LLAMACPP_MODEL",
        "NULLA_LLAMACPP_MODEL",
        "LLAMA_CPP_MODEL",
        "NULLA_LLAMA_CPP_MODEL",
    ) or model_name or default_runtime_model_tag()
    existing = registry.get_manifest("llamacpp-local", resolved_model_name)
    has_base_url = bool(str(getattr(existing, "runtime_config", {}).get("base_url") or "").strip()) if existing else False
    if existing and existing.enabled and has_base_url:
        return existing.provider_id
    context_window = _env_int(
        env,
        "LLAMACPP_CONTEXT_WINDOW",
        "NULLA_LLAMACPP_CONTEXT_WINDOW",
        "LLAMA_CPP_CONTEXT_WINDOW",
        "NULLA_LLAMA_CPP_CONTEXT_WINDOW",
        default=_DEFAULT_LLAMACPP_CONTEXT_WINDOW,
    )
    max_safe_concurrency = _env_int(
        env,
        "LLAMACPP_MAX_SAFE_CONCURRENCY",
        "NULLA_LLAMACPP_MAX_SAFE_CONCURRENCY",
        "LLAMA_CPP_MAX_SAFE_CONCURRENCY",
        "NULLA_LLAMA_CPP_MAX_SAFE_CONCURRENCY",
        default=1,
    )
    manifest = ModelProviderManifest(
        provider_name="llamacpp-local",
        model_name=resolved_model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        runtime_dependency="llama.cpp",
        notes="Local llama.cpp OpenAI-compatible lane — auto-registered when LLAMACPP_BASE_URL is configured.",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "structured_json"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "health_path": "/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.4,
            "supports_json_mode": True,
            "context_window": context_window,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "confidence_baseline": 0.67,
            "orchestration_role": "drone",
            "deployment_class": "local",
            "context_window": context_window,
            "tool_support": ["structured_json"],
            "max_safe_concurrency": max_safe_concurrency,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest.provider_id


def parameter_size_for_model(model_tag: str) -> str:
    model_name = str(model_tag or "").strip().split("/", 1)[-1]
    if ":" not in model_name:
        return "7B"
    _, size = model_name.split(":", 1)
    return size.upper()


def _env_first(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return ""


def _env_int(env: Mapping[str, str], *names: str, default: int) -> int:
    for name in names:
        value = str(env.get(name) or "").strip()
        if not value:
            continue
        try:
            return max(1, int(value))
        except Exception:
            continue
    return max(1, int(default))


def _profile_allows_aux_local_providers(profile_id: str) -> bool:
    if not profile_id:
        return True
    return profile_id in {"local-max", "full-orchestrated"}


def _profile_allows_kimi_provider(profile_id: str) -> bool:
    if not profile_id:
        return True
    return profile_id in {"hybrid-kimi", "full-orchestrated"}


__all__ = [
    "default_runtime_model_tag",
    "ensure_default_runtime_providers",
]
