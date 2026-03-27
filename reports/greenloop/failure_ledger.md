# Greenloop Failure Ledger

## packaging-relay-surface
- Category: packaging
- Test or benchmark ID: clean_editable_install_import_smoke
- Reproduction command: `python -m venv <tmp> && <tmp>/bin/pip install -e '.[dev]' && <tmp>/bin/python -c "import relay, relay.bridge_workers"`
- Symptom: Fresh editable installs did not expose the relay runtime roots cleanly.
- Root cause: relay package markers were missing and setuptools discovery did not include relay*.
- Files changed: pyproject.toml, relay/__init__.py, relay/bridge_workers/__init__.py, tests/test_packaging_contract.py
- Fix summary: Added relay package markers and expanded packaging contract coverage so fresh editable installs and built artifacts expose the same relay roots.
- Rerun evidence: /Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/logs/clean_install_dev.log, /Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/logs/clean_install_runtime_dev.log, /Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/logs/build.log
- Remaining risk: Low. New top-level packages still need packaging contract coverage to stay honest.

## llm-eval-direct-exec-import
- Category: tooling
- Test or benchmark ID: ops/llm_eval.py direct execution
- Reproduction command: `./.venv-greenloop/bin/python ops/llm_eval.py --skip-live-runtime --output-root reports/llm_eval/latest --baseline-root reports/llm_eval/baselines`
- Symptom: Direct script execution failed before the wrapped proof lanes started.
- Root cause: ops/llm_eval.py imported local modules before putting the repo root on sys.path.
- Files changed: ops/llm_eval.py
- Fix summary: Made ops/llm_eval.py robust to direct script execution by inserting the repo root into sys.path before local imports.
- Rerun evidence: /Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/llm_eval/latest/summary.json, /Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/llm_eval/latest/summary.md
- Remaining risk: Low. The wrapper is still thin and depends on run_local_acceptance semantics staying stable.

## fresh-btc-concurrency-flake
- Category: live_lookup_concurrency
- Test or benchmark ID: greenloop_concurrency_lookup
- Reproduction command: `python -m apps.nulla_api_server --bind 127.0.0.1 --port 18080 plus mixed workload concurrency probe at workers 1/2/4`
- Symptom: Concurrent BTC fresh lookups degraded to unresolved-quote filler at workers 2 and 4.
- Root cause: Identical live quote requests were not coalesced, so the upstream crypto quote path flaked under concurrency.
- Files changed: tools/web/web_research.py, tests/test_web_research_runtime.py, tests/test_nulla_web_freshness_and_lookup.py
- Fix summary: Added short-TTL cached and in-flight coalescing for identical live quote requests so concurrent BTC lookups share one upstream fetch.
- Rerun evidence: /Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/concurrency.csv, /Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/logs/concurrency_probe.json
- Remaining risk: Low to medium. This removed the observed stampede failure, but the lane still depends on external quote-source availability.

## machine-read-planner-hijack
- Category: routing
- Test or benchmark ID: freshness_and_adaptive_research_regression_pack
- Reproduction command: `./.venv-greenloop/bin/python -m pytest tests/test_web_research_runtime.py tests/test_nulla_web_freshness_and_lookup.py tests/test_alpha_hardening_pass1_gauntlet.py --tb=short`
- Symptom: Ordinary live-info and adaptive-research prompts were entering the machine-read planner instead of the fresh-info and web path.
- Root cause: apps.nulla_agent invoked the machine-read planner before a prompt-level pre-gate filtered unrelated requests.
- Files changed: apps/nulla_agent.py, tests/test_nulla_web_freshness_and_lookup.py
- Fix summary: Added a cheap prompt pre-gate so the machine-read planner only runs for real machine-spec and safe-directory prompts.
- Rerun evidence: /Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/logs/web_regression.log
- Remaining risk: Low. The pre-gate is intentionally cheap, so future machine-read expansion still needs regression tests for boundary prompts.
