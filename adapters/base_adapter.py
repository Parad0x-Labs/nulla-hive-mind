from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from storage.model_provider_manifest import ModelProviderManifest


@dataclass
class ModelRequest:
    task_kind: str
    prompt: str
    system_prompt: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    temperature: float | None = None
    max_output_tokens: int | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    output_mode: str = "plain_text"
    trace_id: str | None = None
    contract: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ModelResponse:
    output_text: str
    confidence: float = 0.5
    raw_response: Any = None
    usage: dict[str, Any] = field(default_factory=dict)
    structured_output: Any = None
    provider_id: str = ""
    model_name: str = ""
    output_mode: str = "plain_text"
    error: str | None = None


@dataclass
class ModelStreamChunk:
    delta_text: str
    raw_event: Any = None
    done: bool = False


class ModelAdapter(ABC):
    def __init__(self, manifest: ModelProviderManifest) -> None:
        self.manifest = manifest

    def validate_runtime(self) -> list[str]:
        return []

    def health_check(self) -> dict[str, Any]:
        return {"ok": True, "provider_id": self.manifest.provider_id}

    def prewarm(self) -> dict[str, Any]:
        return {
            "ok": True,
            "provider_id": self.manifest.provider_id,
            "status": "skipped",
            "reason": "not_supported",
        }

    def list_capabilities(self) -> list[str]:
        return list(self.manifest.capabilities)

    def supports_streaming(self) -> bool:
        return False

    def estimate_cost_class(self) -> str:
        base_url = str(self.manifest.runtime_config.get("base_url") or "")
        if self.manifest.adapter_type == "cloud_fallback_provider":
            return "paid_cloud"
        if self.manifest.source_type in {"local_path", "subprocess"}:
            return "free_local"
        if base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost"):
            return "free_local"
        return "remote_unknown"

    def get_license_metadata(self) -> dict[str, Any]:
        return {
            "provider_name": self.manifest.provider_name,
            "model_name": self.manifest.model_name,
            "license_name": self.manifest.license_name,
            "license_reference": self.manifest.resolved_license_reference,
            "weights_bundled": self.manifest.weights_are_bundled,
            "redistribution_allowed": self.manifest.redistribution_allowed,
            "runtime_dependency": self.manifest.runtime_dependency,
        }

    def run_text_task(self, request: ModelRequest) -> ModelResponse:
        return self.invoke(request)

    def run_structured_task(self, request: ModelRequest) -> ModelResponse:
        return self.invoke(request)

    def stream_text_task(self, request: ModelRequest) -> Iterable[ModelStreamChunk]:
        response = self.run_text_task(request)
        yield ModelStreamChunk(delta_text=response.output_text, raw_event=response.raw_response, done=True)

    @abstractmethod
    def invoke(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError
