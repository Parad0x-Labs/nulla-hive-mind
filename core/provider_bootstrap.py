from __future__ import annotations

from core.model_registry import ModelRegistry
from storage.model_provider_manifest import ModelProviderManifest, get_provider_manifest


def parameter_size_for_model(model_tag: str) -> str:
    model_name = str(model_tag or "").strip().split("/", 1)[-1]
    if ":" not in model_name:
        return "7B"
    _, size = model_name.split(":", 1)
    return size.upper()


def ensure_default_provider(registry: ModelRegistry, model_tag: str) -> tuple[ModelProviderManifest, bool]:
    existing = get_provider_manifest("ollama-local", model_tag)
    existing_caps = {str(item).strip().lower() for item in list(getattr(existing, "capabilities", []) or [])}
    has_license = bool(
        str(getattr(existing, "license_name", None) or "").strip()
        and str(getattr(existing, "resolved_license_reference", None) or "").strip()
    )
    if existing and existing.enabled and "tool_intent" in existing_caps and has_license:
        return existing, False

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
        notes=f"Local Qwen via Ollama ({parameter_size}) — auto-registered by NULLA runtime bootstrap",
        capabilities=["summarize", "classify", "format", "extract", "code_basic", "structured_json", "tool_intent"],
        runtime_config={
            "base_url": "http://127.0.0.1:11434",
            "api_path": "/v1/chat/completions",
            "health_path": "/v1/models",
            "timeout_seconds": 180,
            "health_timeout_seconds": 10,
            "temperature": 0.7,
            "supports_json_mode": False,
            "prewarm": {
                "strategy": "ollama_generate",
                "keep_alive": "15m",
                "prompt": " ",
                "raw": True,
                "timeout_seconds": 20,
            },
        },
        metadata={
            "runtime_family": "ollama",
            "confidence_baseline": 0.65,
            "parameter_count": parameter_size,
        },
        enabled=True,
    )
    registry.register_manifest(manifest)
    return manifest, True
