# Greenloop Fix Ledger

## fix-packaging-relay-surface
- Summary: Added relay package markers and expanded packaging contract coverage so fresh editable installs and built artifacts expose the same relay roots.
- Files changed: pyproject.toml, relay/__init__.py, relay/bridge_workers/__init__.py, tests/test_packaging_contract.py
- Tests added or updated: tests/test_packaging_contract.py
- Safety notes: This only widened package discovery to the real runtime roots already used by source mode.
- Linked failure IDs: packaging-relay-surface

## fix-llm-eval-direct-exec-import
- Summary: Made ops/llm_eval.py robust to direct script execution by inserting the repo root into sys.path before local imports.
- Files changed: ops/llm_eval.py
- Tests added or updated: none
- Safety notes: The import path change is local to the CLI wrapper and does not affect runtime routing logic.
- Linked failure IDs: llm-eval-direct-exec-import

## fix-crypto-quote-concurrency
- Summary: Added short-TTL cached and in-flight coalescing for identical live quote requests so concurrent BTC lookups share one upstream fetch.
- Files changed: tools/web/web_research.py, tests/test_web_research_runtime.py, tests/test_nulla_web_freshness_and_lookup.py
- Tests added or updated: tests/test_web_research_runtime.py, tests/test_nulla_web_freshness_and_lookup.py
- Safety notes: The cache key is scoped to the resolved live-quote target and only caches successful quote objects for a short TTL.
- Linked failure IDs: fresh-btc-concurrency-flake

## fix-machine-read-pre-gate
- Summary: Added a cheap prompt pre-gate so the machine-read planner only runs for real machine-spec and safe-directory prompts.
- Files changed: apps/nulla_agent.py, tests/test_nulla_web_freshness_and_lookup.py
- Tests added or updated: tests/test_nulla_web_freshness_and_lookup.py
- Safety notes: The pre-gate narrows planner activation and reduces routing blast radius without loosening any write policy.
- Linked failure IDs: machine-read-planner-hijack
