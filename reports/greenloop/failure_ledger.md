# Failure Ledger

## GL-20260328-001 - packaging_surface
- Test or benchmark ID: `python -m build`
- Reproduction command: `/tmp/nulla_greenloop_runtime_greenloop-20260328T194333Z_py311_v2/bin/python -m build`
- Symptom: The clean runtime+dev proof environment could not execute `python -m build` because the `build` module was missing.
- Root cause: The `dev` extra in `pyproject.toml` did not include `build` even though the greenloop contract requires a clean build gate.
- Files changed: `pyproject.toml`, `tests/test_install_surface_contracts.py`
- Fix summary: Added `build>=1.2` to the `dev` extra and locked the contract with `test_pyproject_dev_extra_covers_build_and_test_tooling`.
- Status: `fixed`
- Rerun evidence: `reports/greenloop/logs/greenloop-20260328T194333Z/rerun_packaging_pytest.log`, `reports/greenloop/logs/greenloop-20260328T194333Z/phase1_v2_build.log`, `reports/greenloop/logs/greenloop-20260328T194333Z/rerun_full_static_build.log`
- Remaining risk: The repo still requires Python >= 3.10, so operators who default to Python 3.9 must use the installer-created or explicit Python 3.11 interpreter.

## GL-20260328-002 - provider_routing
- Test or benchmark ID: `tests/test_model_execution_layer.py::ModelExecutionLayerTests::test_role_aware_summary_execution_prefers_queen_lane`
- Reproduction command: `/tmp/nulla_greenloop_runtime_greenloop-20260328T194333Z_py311_v2/bin/python ops/pytest_shards.py --workers 6 --pytest-arg=--tb=short`
- Symptom: The queen summary path selected `local-qwen-http` instead of the ranked `kimi-cloud-http` lane.
- Root cause: Local-vs-remote race logic in `core/memory_first_router.py` ignored ranking order and could let a local lane win even when the top-ranked manifest was already a remote queen lane.
- Files changed: `core/memory_first_router.py`
- Fix summary: Restricted the race path to cases where the top-ranked manifest is already local.
- Status: `fixed`
- Rerun evidence: `reports/greenloop/logs/greenloop-20260328T194333Z/rerun_race2_pytest.log`, `reports/greenloop/logs/greenloop-20260328T194333Z/phase3_pytest_shards_rerun.log`
- Remaining risk: Future routing-weight changes still need direct queen-lane and race golden coverage.
