from __future__ import annotations

import json
import os
import subprocess
from shutil import which
from typing import Any

from adapters.base_adapter import ModelAdapter, ModelRequest, ModelResponse


class LocalSubprocessAdapter(ModelAdapter):
    def validate_runtime(self) -> list[str]:
        command = self._command()
        if not command:
            return [f"{self.manifest.provider_id}: missing runtime_config.command"]
        if which(command[0]) is None:
            return [f"{self.manifest.provider_id}: command not found: {command[0]}"]
        return []

    def health_check(self) -> dict[str, Any]:
        command = self._command()
        if not command:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": "missing_command"}
        if which(command[0]) is None:
            return {"ok": False, "provider_id": self.manifest.provider_id, "error": f"command_not_found:{command[0]}"}
        return {"ok": True, "provider_id": self.manifest.provider_id}

    def invoke(self, request: ModelRequest) -> ModelResponse:
        command = self._command()
        if not command:
            raise RuntimeError(f"{self.manifest.provider_id}: missing runtime_config.command")
        return self._invoke_with_env(request, extra_env={})

    def _invoke_with_env(self, request: ModelRequest, *, extra_env: dict[str, str]) -> ModelResponse:
        command = self._command()
        timeout_seconds = float(self.manifest.runtime_config.get("timeout_seconds") or 60.0)
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in dict(self.manifest.runtime_config.get("env") or {}).items()})
        env.update({str(k): str(v) for k, v in extra_env.items()})
        env.setdefault("NULLA_PROVIDER_NAME", self.manifest.provider_name)
        env.setdefault("NULLA_MODEL_NAME", self.manifest.model_name)
        payload = {
            "task_kind": request.task_kind,
            "prompt": request.prompt,
            "system_prompt": request.system_prompt,
            "context": request.context,
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens,
            "provider_name": self.manifest.provider_name,
            "model_name": self.manifest.model_name,
        }
        completed = subprocess.run(
            command,
            input=json.dumps(payload, sort_keys=True),
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"{self.manifest.provider_id}: subprocess failed with code {completed.returncode}: {completed.stderr.strip()}"
            )
        stdout = completed.stdout.strip()
        if not stdout:
            return ModelResponse(output_text="", confidence=0.3, raw_response={"stderr": completed.stderr.strip()})
        try:
            obj = json.loads(stdout)
            return ModelResponse(
                output_text=str(obj.get("output_text") or obj.get("text") or ""),
                confidence=float(obj.get("confidence") or 0.5),
                raw_response=obj,
                usage=dict(obj.get("usage") or {}),
            )
        except Exception:
            return ModelResponse(output_text=stdout, confidence=0.5, raw_response={"stdout": stdout})

    def _command(self) -> list[str]:
        command = self.manifest.runtime_config.get("command") or []
        if isinstance(command, str):
            return [command]
        return [str(item) for item in command]
