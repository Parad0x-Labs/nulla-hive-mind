# NULLA Hive Mind Greenloop Summary

Run ID: `greenloop-20260328T194333Z`
Branch: `codex/honest-ollama-prewarm-bootstrap`
Commit: `dcce6606f49ad938bafe75a0b26b8628af07e2c9`
Started: `2026-03-28T19:43:33Z`
Finished: `2026-03-28T20:35:16Z`
Runner: `workstation`

## Verdict
- Required greenloop gates are green on this rerun.
- `ci_fast_green`: `true`
- `overall_full_green`: `true`
- Blockers: `[]`
- Only skipped gate: standalone `python -m apps.nulla_api_server --bind 127.0.0.1 --port 18080` as a signoff gate. That step is structurally obsolete because `ops/llm_eval.py` now self-manages the canonical live acceptance runtime via `run_local_acceptance.run_full_acceptance()`.

## Real Failures Fixed This Cycle

### GL-20260328-001 - packaging_surface
- Failing gate: `python -m build`
- Symptom: clean runtime+dev proof env failed with `No module named build`
- Root cause: `build` was not declared in the `dev` extra even though the greenloop proof path requires it
- Files changed: `pyproject.toml`, `tests/test_install_surface_contracts.py`
- Fix summary: added `build>=1.2` to the `dev` extra and locked it with `test_pyproject_dev_extra_covers_build_and_test_tooling`
- Rerun evidence: `reports/greenloop/logs/greenloop-20260328T194333Z/rerun_packaging_pytest.log` (`10 passed in 0.08s`), `reports/greenloop/logs/greenloop-20260328T194333Z/phase1_v2_build.log`, `reports/greenloop/logs/greenloop-20260328T194333Z/rerun_full_static_build.log`
- Remaining risk: hosts that default to Python 3.9 will still fail until the operator uses a supported interpreter

### GL-20260328-002 - provider_routing
- Failing test: `tests/test_model_execution_layer.py::ModelExecutionLayerTests::test_role_aware_summary_execution_prefers_queen_lane`
- Symptom: provider selection returned `local-qwen-http` instead of `kimi-cloud-http`
- Root cause: local-vs-remote race logic could override ranking truth even when the top-ranked manifest was already a remote queen lane
- Files changed: `core/memory_first_router.py`
- Fix summary: only allow the local-vs-remote race when the top-ranked manifest is already local
- Rerun evidence: `reports/greenloop/logs/greenloop-20260328T194333Z/rerun_race2_pytest.log` (`2 passed in 0.06s`), `reports/greenloop/logs/greenloop-20260328T194333Z/phase3_pytest_shards_rerun.log` (`ok shard 1` through `ok shard 6`)
- Remaining risk: future routing-weight changes still need direct queen-lane and race golden tests

## Gate Status
- `pip install -e ".[dev]"`: PASS in clean Python 3.11 venv
- `pip install -e ".[runtime,dev]"`: PASS in clean Python 3.11 venv
- Optional runtime imports: PASS
- Entry point smoke: PASS
- `ruff check .`: PASS
- `python -m compileall adapters apps core installer ops tools tests`: PASS
- `python -m build`: PASS after packaging fix
- Wheel smoke install/import: PASS
- `python ops/pytest_shards.py --workers 6 --pytest-arg=--tb=short`: PASS on rerun after routing fix
- `python ops/llm_eval.py --skip-live-runtime ...`: PASS
- `python ops/llm_eval.py --output-root reports/llm_eval/latest ... --base-url http://127.0.0.1:18080`: PASS
- `python ops/greenloop_concurrency.py --base-url http://127.0.0.1:18080 --levels 1,2,4`: PASS

## Metrics
- Acceptance latency p50: `463.0 ms`
- Acceptance latency p95: `38519.3 ms`
- Acceptance latency p99: `82754.26 ms`
- Acceptance throughput: `7.669 requests/min`
- Concurrency success rates: `1 worker = 1.0`, `2 workers = 1.0`, `4 workers = 1.0`
- Concurrency throughput: `1 worker = 0.115 rps`, `2 workers = 0.267 rps`, `4 workers = 0.406 rps`
- Scaling efficiency: `1->2 = 1.161`, `1->4 = 0.883`

## Provider Snapshot
- Active acceptance provider: `ollama-local:qwen2.5:7b`
- Active runtime locality: `local`
- Active runtime install profile: `local-only`
- Active model: `qwen2.5:7b`
- Other locally installed Ollama models observed at capture time: `qwen2.5:14b`
- `wan_public_mesh` capability state: `partial`

## Files Changed In The Source Slice
- Packaging: `pyproject.toml`, `tests/test_install_surface_contracts.py`
- Routing: `core/memory_first_router.py`
- Generated proof/update artifacts: `docs/LLM_ACCEPTANCE_REPORT.md`, `reports/greenloop/*`, `reports/llm_eval/latest/*`, `artifacts/acceptance_runs/llm_eval_live/*`

## Risks And Debt
- The host default Python is still `3.9.6`, which is below the repo contract and not a valid proof interpreter.
- The live proof ran from a dirty source checkout, so the acceptance build stamp is `dcce6606f49a.dirty` even though every gate passed.
- This rerun only exercised the local `qwen2.5:7b` lane live. Remote providers were not part of this acceptance profile.
