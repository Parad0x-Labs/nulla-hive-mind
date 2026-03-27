# Fix Ledger

## FX-001
- Summary: Added explicit install-profile validation so shell and Windows launchers fail closed when hybrid-kimi is selected without a ready remote lane.
- Files changed: installer/install_nulla.sh, installer/install_nulla.bat, installer/validate_install_profile.py, tests/test_install_script_contract.py, tests/test_validate_install_profile.py
- Tests added or updated: tests/test_install_script_contract.py::test_install_wrappers_forward_install_profile_and_extra_args, tests/test_validate_install_profile.py::test_validate_install_profile_blocks_unready_hybrid_kimi, tests/test_validate_install_profile.py::test_validate_install_profile_accepts_ready_hybrid_kimi
- Safety notes: The validator only blocks clearly unready remote profiles; default local-only installs remain unchanged.
- Linked failures: GL-001, GL-002

## FX-002
- Summary: Separated unsupported remote ideas from the default provider probe surface so install recommendations stay grounded in real lanes.
- Files changed: installer/provider_probe.py, tests/test_provider_probe.py
- Tests added or updated: tests/test_provider_probe.py::test_default_probe_report_hides_unsupported_remote_ideas
- Safety notes: Unsupported ideas still remain visible behind --show-unsupported for deliberate operator review.
- Linked failures: GL-003

## FX-003
- Summary: Preserved previous blocked or red llm_eval and local-acceptance bundles before writing a new run so the first-red evidence survives reruns.
- Files changed: ops/llm_eval.py, ops/run_local_acceptance.py, tests/test_llm_eval_artifact_preservation.py, tests/test_run_local_acceptance.py
- Tests added or updated: tests/test_llm_eval_artifact_preservation.py::test_preserve_previous_output_bundle_copies_non_green_summary, tests/test_llm_eval_artifact_preservation.py::test_preserve_previous_live_run_artifacts_copies_non_green_bundle, tests/test_run_local_acceptance.py::test_preserve_previous_run_artifacts_copies_non_green_bundle
- Safety notes: Preservation only triggers when the prior bundle is blocked or non-green, so clean reruns do not explode the artifact tree.
- Linked failures: GL-004

## FX-004
- Summary: Added a canonical greenloop concurrency probe that records success rate, throughput, and latency percentiles at worker counts 1, 2, and 4.
- Files changed: .gitignore, ops/greenloop_concurrency.py, tests/test_greenloop_concurrency.py
- Tests added or updated: tests/test_greenloop_concurrency.py::test_parse_levels_rejects_zero, tests/test_greenloop_concurrency.py::test_summarize_measurements_counts_successes_and_percentiles, tests/test_greenloop_concurrency.py::test_write_summary_csv_writes_expected_columns
- Safety notes: The probe only hits /api/chat with isolated workspaces and does not mutate shared user state.
- Linked failures: GL-005
