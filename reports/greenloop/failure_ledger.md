# Failure Ledger

## GL-001 - install_profile_validation
- Test or benchmark ID: `tests/test_validate_install_profile.py::test_validate_install_profile_blocks_unready_hybrid_kimi`
- Reproduction command: `python -m pytest -q tests/test_validate_install_profile.py -k hybrid_kimi`
- Symptom: Selecting the hybrid Kimi install profile could continue into launcher generation even when the remote lane was not configured.
- Root cause: The installers summarized profile truth but never validated the selected profile readiness before proceeding.
- Status: `fixed`
- Remaining risk: Kimi remains optional on this machine because no KIMI_API_KEY is configured.
- Evidence before: Installer/profile lane review during greenloop found no fail-closed validation for unready hybrid-kimi selections.
- Evidence after: tests/test_validate_install_profile.py::test_validate_install_profile_blocks_unready_hybrid_kimi; tests/test_validate_install_profile.py::test_validate_install_profile_accepts_ready_hybrid_kimi

## GL-002 - windows_installer_contract
- Test or benchmark ID: `tests/test_install_script_contract.py::test_install_wrappers_forward_install_profile_and_extra_args`
- Reproduction command: `python -m pytest -q tests/test_install_script_contract.py -k install_profile`
- Symptom: The Windows install path could drop the requested install profile when building profile truth and skip explicit readiness validation.
- Root cause: The batch launcher path did not thread NULLA_INSTALL_PROFILE into the provider-truth helper and did not call the profile validator.
- Status: `fixed`
- Remaining risk: Windows parity still depends on the same remote lane being configured when hybrid-kimi is selected.
- Evidence before: The batch contract lacked requested_profile forwarding and validate_install_profile invocation.
- Evidence after: tests/test_install_script_contract.py::test_install_wrappers_forward_install_profile_and_extra_args

## GL-003 - provider_surface_honesty
- Test or benchmark ID: `tests/test_provider_probe.py::test_default_probe_report_hides_unsupported_remote_ideas`
- Reproduction command: `python -m pytest -q tests/test_provider_probe.py -k unsupported`
- Symptom: The default provider probe could surface unsupported remote ideas like Tether or QVAC alongside real install lanes.
- Root cause: Unsupported remote concepts were included in the default stack surface instead of being isolated behind an explicit flag.
- Status: `fixed`
- Remaining risk: Unsupported remote ideas still exist behind --show-unsupported for explicit operator review.
- Evidence before: Default provider probe output mixed unsupported remote ideas into the normal recommendation surface.
- Evidence after: tests/test_provider_probe.py::test_default_probe_report_hides_unsupported_remote_ideas

## GL-004 - proof_artifact_preservation
- Test or benchmark ID: `tests/test_llm_eval_artifact_preservation.py::test_preserve_previous_live_run_artifacts_copies_non_green_bundle`
- Reproduction command: `python -m pytest -q tests/test_llm_eval_artifact_preservation.py tests/test_run_local_acceptance.py -k preserve_previous`
- Symptom: A fresh green run could overwrite the previous blocked or red proof bundle and destroy the first-red evidence.
- Root cause: The acceptance scripts replaced prior output directories in place without preserving failed bundles first.
- Status: `fixed`
- Remaining risk: Preserved bundles can still contain local absolute paths unless the operator chooses a sanitized output root.
- Evidence before: Earlier greenloop reruns replaced blocked output directories in place.
- Evidence after: tests/test_llm_eval_artifact_preservation.py::test_preserve_previous_output_bundle_copies_non_green_summary; tests/test_llm_eval_artifact_preservation.py::test_preserve_previous_live_run_artifacts_copies_non_green_bundle; tests/test_run_local_acceptance.py::test_preserve_previous_run_artifacts_copies_non_green_bundle

## GL-005 - proof_path_coverage
- Test or benchmark ID: `tests/test_greenloop_concurrency.py::test_summarize_measurements_counts_successes_and_percentiles`
- Reproduction command: `python ops/greenloop_concurrency.py --base-url http://127.0.0.1:18080 --workspace-root /tmp/nulla-greenloop-proof/workspace --output-csv reports/greenloop/concurrency.csv --output-json reports/greenloop/concurrency.json`
- Symptom: The checklist required a real concurrency/performance lane, but the repo had no canonical command to generate concurrency.csv.
- Root cause: Greenloop stopped at llm_eval and never added a first-class concurrency probe to the proof path.
- Status: `fixed`
- Remaining risk: This probe measures single-node API concurrency only; it does not profile helper-mesh or WAN traffic.
- Evidence before: Repo search showed no tracked greenloop concurrency probe or generator for reports/greenloop/concurrency.csv.
- Evidence after: tests/test_greenloop_concurrency.py::test_parse_levels_rejects_zero; tests/test_greenloop_concurrency.py::test_summarize_measurements_counts_successes_and_percentiles; tests/test_greenloop_concurrency.py::test_write_summary_csv_writes_expected_columns; reports/greenloop/concurrency.csv
