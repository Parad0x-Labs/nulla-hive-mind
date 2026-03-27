# NULLA Greenloop Summary

Run id: `greenloop-20260327T003718Z`

## Environment
- branch: `codex/modularity-backbone-checkpoint-1`
- commit: `3a57ae68a5e0f00aba3b0ecaeaf373231577fc53`
- dirty tree: `True`
- machine: `Apple M4` | `24.0 GiB RAM` | `Apple M4`
- python: `3.11.15`
- local model inventory: `qwen2.5:14b`, `qwen2.5:7b`
- recommended local stack: `local_dual_ollama`

## Top-level gates
- clean install `.[dev]`: PASS
- clean install `.[runtime,dev]`: PASS
- `ruff check .`: PASS
- `python ops/pytest_shards.py --workers 6 --pytest-arg=--tb=short`: PASS
- `python -m build`: PASS
- `python ops/llm_eval.py --skip-live-runtime ...`: PASS
- `python ops/llm_eval.py --output-root ... --live-run-root ...`: PASS
- mixed-workload concurrency lane on `18080`: PASS

## Key metrics
- live acceptance simple prompt median: `0.028s`
- live acceptance file task median: `0.405s`
- live acceptance live lookup median: `0.16s`
- live acceptance chained task median: `0.563s`
- concurrency success: workers `1=1.0`, `2=1.0`, `4=1.0`
- concurrency p95 at workers 4: `8659.9 ms`
- unsupported-claim rate in this proof pack: `0.0`

## What changed in this repair slice
- fixed packaging parity so fresh installs expose the `relay` runtime roots
- fixed direct `ops/llm_eval.py` execution as a script path
- fixed concurrent BTC live lookups by coalescing identical quote fetches
- fixed machine-read planner hijack on ordinary fresh-info and adaptive-research prompts

## What is still not done
- Kimi is still not a first-class installer runtime profile
- Tether and QVAC are still not first-class real stacks
- some first-red artifacts from this cycle were reconstructed after reruns instead of being preserved in place

## Artifact map
- summary json: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/summary.json`
- provider snapshot: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/provider_snapshot.json`
- latency csv: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/latency.csv`
- concurrency csv: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/concurrency.csv`
- failure ledger: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/failure_ledger.md`
- fix ledger: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/fix_ledger.md`
- final signoff: `/Users/sauliuskruopis/Desktop/Decentralized_NULLA/reports/greenloop/final_signoff.md`
