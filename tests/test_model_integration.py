from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from adapters.optional_transformers_adapter import OptionalTransformersAdapter
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest
from core.model_teacher_pipeline import ModelTeacherPipeline
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest, list_provider_manifests


class ModelIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        _clear_model_manifests()
        self.registry = ModelRegistry()

    def test_register_manifest_and_list_provider_licenses(self) -> None:
        manifest = self.registry.register_manifest(
            {
                "provider_name": "local-qwen-http",
                "model_name": "qwen2.5-7b-instruct",
                "source_type": "http",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "notes": "User-managed HTTP server",
                "capabilities": ["summarize", "classify"],
                "runtime_config": {"base_url": "http://127.0.0.1:8000"},
                "enabled": True,
            }
        )

        self.assertEqual(manifest.provider_id, "local-qwen-http:qwen2.5-7b-instruct")
        listed = list_provider_manifests(enabled_only=True)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].license_name, "Apache-2.0")
        self.assertFalse(self.registry.startup_warnings())

    def test_manifest_validation_rejects_unknown_capability(self) -> None:
        with self.assertRaises(ValueError):
            ModelProviderManifest.model_validate(
                {
                    "provider_name": "bad-provider",
                    "model_name": "bad-model",
                    "source_type": "http",
                    "license_name": "Apache-2.0",
                    "license_url_or_reference": "https://example.test/license",
                    "weight_location": "external",
                    "redistribution_allowed": True,
                    "runtime_dependency": "openai-compatible-local-runtime",
                    "capabilities": ["magic_reasoning"],
                }
            )

    def test_startup_warnings_report_missing_license_metadata(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "missing-license",
                "model_name": "helper",
                "source_type": "subprocess",
                "weight_location": "external",
                "redistribution_allowed": None,
                "runtime_dependency": "external-helper-runtime",
                "capabilities": ["classify"],
                "runtime_config": {"command": ["echo"]},
            }
        )

        warnings = self.registry.startup_warnings()
        self.assertTrue(any("missing license metadata" in warning for warning in warnings))

    def test_optional_transformers_adapter_reports_absence_cleanly(self) -> None:
        manifest = ModelProviderManifest.model_validate(
            {
                "provider_name": "local-transformers",
                "model_name": "qwen-local",
                "source_type": "local_path",
                "adapter_type": "optional_transformers",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "redistribution_allowed": True,
                "runtime_dependency": "optional-transformers",
                "capabilities": ["summarize"],
                "runtime_config": {"model_path": "/tmp/not-real"},
            }
        )
        adapter = OptionalTransformersAdapter(manifest)
        with mock.patch("importlib.util.find_spec", return_value=None):
            warnings = adapter.validate_runtime()
            self.assertTrue(any("optional dependency 'transformers' is not installed" in warning for warning in warnings))
            with self.assertRaises(RuntimeError):
                adapter.invoke(
                    request=mock.Mock(task_kind="summarization", prompt="hi", system_prompt=None, context={}, temperature=None, max_output_tokens=None)
                )

    def test_external_or_user_supplied_weights_can_register_without_bundled_assets(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "user-local-model",
                "model_name": "custom",
                "source_type": "local_path",
                "adapter_type": "local_model_path",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "redistribution_allowed": True,
                "runtime_dependency": "external-runtime",
                "notes": "No weights shipped with NULLA",
                "capabilities": ["classify"],
                "runtime_config": {"model_path": "/replace/me", "command": ["external-runtime"]},
                "enabled": True,
            }
        )
        selected = self.registry.select_manifest(ModelSelectionRequest(task_kind="classification"))
        self.assertIsNotNone(selected)
        self.assertEqual(selected.weight_location, "user-supplied")

    def test_selection_policy_skips_bundled_weights_when_forbidden(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "bundled-bad",
                "model_name": "bad",
                "source_type": "http",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "bundled",
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["classify"],
                "runtime_config": {"base_url": "http://127.0.0.1:8001"},
                "enabled": True,
            }
        )
        self.registry.register_manifest(
            {
                "provider_name": "good-http",
                "model_name": "good",
                "source_type": "http",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "external",
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["classify"],
                "runtime_config": {"base_url": "http://127.0.0.1:8002"},
                "enabled": True,
            }
        )

        selected = self.registry.select_manifest(ModelSelectionRequest(task_kind="classification", forbid_bundled_weights=True))
        self.assertIsNotNone(selected)
        self.assertEqual(selected.provider_name, "good-http")

    def test_teacher_pipeline_returns_candidate_with_provenance(self) -> None:
        self.registry.register_manifest(
            {
                "provider_name": "helper-http",
                "model_name": "helper",
                "source_type": "http",
                "license_name": "Apache-2.0",
                "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "external",
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["format"],
                "runtime_config": {"base_url": "http://127.0.0.1:8000"},
                "enabled": True,
            }
        )
        pipeline = ModelTeacherPipeline(self.registry)
        fake_response = mock.Mock(output_text="Normalized: please harden my telegram setup", confidence=0.7)
        with mock.patch.object(self.registry, "build_adapter") as build_adapter:
            build_adapter.return_value.invoke.return_value = fake_response
            candidate = pipeline.normalization_assist("pls harden tg setup")

        self.assertIsNotNone(candidate)
        self.assertTrue(candidate.candidate_only)
        self.assertEqual(candidate.provider_name, "helper-http")
        self.assertIn("license_name", candidate.provenance)

    def test_register_from_file_loads_sample_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample = Path(tmpdir) / "providers.json"
            sample.write_text(
                """
                {
                  "providers": [
                    {
                      "provider_name": "sample-http",
                      "model_name": "sample-model",
                      "source_type": "http",
                      "license_name": "Apache-2.0",
                      "license_url_or_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                      "weight_location": "external",
                      "redistribution_allowed": true,
                      "runtime_dependency": "openai-compatible-local-runtime",
                      "capabilities": ["summarize"],
                      "runtime_config": {"base_url": "http://127.0.0.1:8000"},
                      "enabled": false
                    }
                  ]
                }
                """,
                encoding="utf-8",
            )
            loaded = self.registry.register_from_file(sample)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].provider_name, "sample-http")


def _clear_model_manifests() -> None:
    conn = get_connection()
    try:
        try:
            conn.execute("DELETE FROM model_provider_manifests")
            conn.commit()
        except Exception:
            conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    unittest.main()
