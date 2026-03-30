# Fix Ledger

## FX-20260328-001
- Summary: Added `build>=1.2` to the `dev` extra and locked the packaging contract so clean proof environments can execute `python -m build` without extra manual installs.
- Files changed: `pyproject.toml`, `tests/test_install_surface_contracts.py`
- Tests added or updated: `tests/test_install_surface_contracts.py::test_pyproject_dev_extra_covers_build_and_test_tooling`
- Safety notes: This expands declared proof tooling only; it does not alter runtime-only production dependency resolution.
- Linked failures: `GL-20260328-001`

## FX-20260328-002
- Summary: Preserved provider ranking truth by only permitting the local-vs-remote race when the top-ranked manifest is already local.
- Files changed: `core/memory_first_router.py`
- Tests added or updated: `tests/test_model_execution_layer.py::ModelExecutionLayerTests::test_role_aware_summary_execution_prefers_queen_lane`, `tests/test_provider_failover.py::ProviderFailoverTests::test_local_remote_race_returns_first_successful_winner`
- Safety notes: The rejected intermediate fix that disabled races too broadly was not kept; the final change keeps intended local-first failover behavior intact.
- Linked failures: `GL-20260328-002`
