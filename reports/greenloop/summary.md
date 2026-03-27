# Greenloop Summary

Run id: `llm-eval-1774597124`  
Commit: `15948c7bbf4aefd6ed0f66c7e586ee805f3ac937` on `main`  
Window: `2026-03-27T07:23:10.058429Z` to `2026-03-27T07:46:13.957054Z`

## Gates

- `pip install -e ".[dev]"`: pass
- `pip install -e ".[runtime,dev]"`: pass
- `ruff check .`: pass
- `python -m compileall -q apps core installer ops tests`: pass
- `python ops/pytest_shards.py --workers 6 --pytest-arg=--tb=short`: pass
- `python -m build`: pass
- `python ops/llm_eval.py --skip-live-runtime ...`: pass
- `python ops/llm_eval.py ... --live-run-root ... --base-url http://127.0.0.1:18080`: pass
- `python ops/greenloop_concurrency.py --base-url http://127.0.0.1:18080 ...`: pass

## Measured Results

- Fast/live acceptance verdict: `GREEN`
- Overall latency: p50 `457 ms`, p95 `8113 ms`, p99 `10953 ms`
- Live acceptance medians: simple `3.309s`, file `0.461s`, live lookup `0.173s`, chained `0.635s`
- Concurrency success rate: workers `1=1.0`, `2=1.0`, `4=1.0`
- Concurrency throughput (req/s): workers `1=0.26`, `2=0.242`, `4=0.229`
- Scaling efficiency: `1->2=0.465`, `1->4=0.22`

## Closed Gaps

- Hybrid-Kimi profile selection now fails closed when the lane is unconfigured.
- Windows installer now forwards the requested profile into profile truth and calls the validator.
- The default provider probe no longer surfaces unsupported Tether/QVAC ideas as normal install lanes.
- llm_eval and local acceptance now preserve the previous blocked/non-green bundle before replacing it.
- The repo now has a canonical concurrency probe instead of a missing `concurrency.csv` generator.

## Deferred Or Rejected Local Slices

- Keep `Beta2_Website/*` out of `main` until its feed contract is repaired and re-proved.
- Keep the `core/public_hive/*` split and related `hive/` tree out of `main` until the surface is redesigned and revalidated.
- Keep the `core/runtime_task_rail*` split out of `main` until it is landed with its own isolated regression pack.
- Keep local screenshots, temp reports, and private acceptance-runtime artifacts out of git.

## Verdict

`go_with_risk` with `low` risk.

The required checklist gates are green. The remaining risk is limited to proof-tooling side effects and unlanded local slices that were explicitly kept out of this landing.
