from __future__ import annotations

import os
from typing import Any

import requests

from adapters.base_adapter import ModelAdapter, ModelRequest, ModelResponse


class OpenAICompatibleAdapter(ModelAdapter):
    def validate_runtime(self) -> list[str]:
        warnings: list[str] = []
        base_url = str(self.manifest.runtime_config.get("base_url") or "").strip()
        if not base_url:
            warnings.append(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        return warnings

    def health_check(self) -> dict[str, Any]:
        base_url = str(self.manifest.runtime_config.get("base_url") or "").rstrip("/")
        if not base_url:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": "missing_base_url"}
        health_path = str(self.manifest.runtime_config.get("health_path") or "/v1/models")
        timeout_seconds = float(self.manifest.runtime_config.get("health_timeout_seconds") or 3.0)
        try:
            response = requests.get(f"{base_url}{health_path}", headers=self._headers(), timeout=timeout_seconds)
            response.raise_for_status()
            return {"ok": True, "provider_id": self.manifest.provider_id, "status_code": response.status_code}
        except Exception as exc:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": str(exc)}

    def run_text_task(self, request: ModelRequest) -> ModelResponse:
        return self._invoke_http(request, force_json=False)

    def run_structured_task(self, request: ModelRequest) -> ModelResponse:
        return self._invoke_http(request, force_json=True)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        force_json = request.output_mode in {"json_object", "action_plan", "tool_intent", "summary_block"}
        return self._invoke_http(request, force_json=force_json)

    def _invoke_http(self, request: ModelRequest, *, force_json: bool) -> ModelResponse:
        base_url = str(self.manifest.runtime_config.get("base_url") or "").rstrip("/")
        if not base_url:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.base_url")
        api_path = str(self.manifest.runtime_config.get("api_path") or "/v1/chat/completions")
        payload: dict[str, Any] = {
            "model": self.manifest.model_name,
            "messages": request.messages or _build_messages(request.system_prompt, request.prompt, attachments=request.attachments),
            "temperature": request.temperature if request.temperature is not None else self.manifest.runtime_config.get("temperature", 0.2),
        }
        if request.max_output_tokens is not None:
            payload["max_tokens"] = int(request.max_output_tokens)
        if force_json and bool(self.manifest.runtime_config.get("supports_json_mode", False)):
            payload["response_format"] = {"type": "json_object"}
        timeout_seconds = float(self.manifest.runtime_config.get("timeout_seconds") or 30.0)
        response = requests.post(
            f"{base_url}{api_path}",
            json=payload,
            headers=self._headers(),
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        output_text = _extract_openai_text(data)
        usage = dict(data.get("usage") or {})
        return ModelResponse(
            output_text=output_text,
            confidence=float(self.manifest.metadata.get("confidence_baseline") or 0.65),
            raw_response=data,
            usage=usage,
            provider_id=self.manifest.provider_id,
            model_name=self.manifest.model_name,
            output_mode=request.output_mode,
        )

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        headers.update({str(k): str(v) for k, v in dict(self.manifest.runtime_config.get("headers") or {}).items()})
        api_key_env = str(self.manifest.runtime_config.get("api_key_env") or "").strip()
        if api_key_env and os.getenv(api_key_env):
            headers["Authorization"] = f"Bearer {os.getenv(api_key_env)}"
        return headers


def _build_messages(system_prompt: str | None, prompt: str, *, attachments: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    attachment_entries = list(attachments or [])
    if not attachment_entries:
        messages.append({"role": "user", "content": prompt})
        return messages

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for attachment in attachment_entries:
        kind = str(attachment.get("kind") or "").lower()
        if kind == "image":
            url = str(attachment.get("url") or attachment.get("path") or "").strip()
            if url:
                content.append({"type": "image_url", "image_url": {"url": url}})
        elif kind == "video":
            transcript = str(attachment.get("transcript") or attachment.get("caption") or "").strip()
            label = str(attachment.get("label") or "Video evidence").strip()
            if transcript:
                content.append({"type": "text", "text": f"{label} transcript: {transcript}"})
            else:
                content.append({"type": "text", "text": f"{label}: video evidence provided but no transcript was available."})
        else:
            snippet = str(attachment.get("text") or attachment.get("caption") or "").strip()
            if snippet:
                content.append({"type": "text", "text": snippet})
    messages.append({"role": "user", "content": content})
    return messages


def _extract_openai_text(payload: dict[str, Any]) -> str:
    choices = list(payload.get("choices") or [])
    if not choices:
        raise RuntimeError("OpenAI-compatible response did not include choices.")
    message = dict(choices[0].get("message") or {})
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict)).strip()
    raise RuntimeError("OpenAI-compatible response did not include textual content.")
