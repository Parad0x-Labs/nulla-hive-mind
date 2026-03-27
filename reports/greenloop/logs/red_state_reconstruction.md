# Reconstructed Red States

This file reconstructs early red observations from the green-loop session.
It is honest, but not perfect: some first-red standalone artifacts were overwritten during reruns before durable log capture was added.

## packaging-relay-surface
- Observed symptom: fresh editable install import smokes did not expose the `relay` runtime roots cleanly.
- Reconstructed root cause: `relay/` and `relay/bridge_workers/` were missing package markers and setuptools discovery did not include `relay*`.

## llm-eval-direct-exec-import
- Observed symptom: `python ops/llm_eval.py --skip-live-runtime ...` failed before reaching the wrapped gates when executed as a script path.
- Reconstructed root cause: repo root was not inserted into `sys.path` before importing `installer.provider_probe`.

## fresh-btc-concurrency-flake
- Observed symptom: the first live concurrency probe went red only on `lookup` workloads. Aggregate success was `1.0` at workers `1`, `0.875` at workers `2`, and `0.75` at workers `4`.
- Exact assistant failure text captured during the red run: `I could not map `Look up BTC in USD and answer plus where you got it` to a known traded asset or commodity quote. If you mean a stock, token, ETF, or product, give me the exact ticker or full name.`
- Reconstructed root cause: identical concurrent crypto quote requests were stampeding the upstream live-quote path instead of coalescing.

## machine-read-planner-hijack
- Observed symptom: the freshness and adaptive-research regression slice failed after the BTC fix because ordinary live-info chat was being routed into the machine-read planner.
- Reconstructed root cause: the machine-read planner was invoked before a cheap prompt pre-gate, so unrelated fresh and research prompts could hit that planner path.
