# Final Signoff

- Verdict: `go_with_risk`
- Risk level: `low`
- Commit: `15948c7bbf4aefd6ed0f66c7e586ee805f3ac937`
- Checklist gates: all required gates passed

## What This Signoff Covers

- install profile detection and fail-closed validation
- provider recommendation honesty on this machine
- full shard regression gate on the current head
- build and wheel smoke on the current head
- fast and live llm_eval acceptance on the current head
- measured API concurrency at worker counts 1, 2, and 4

## Remaining Followups

- Keep `Beta2_Website/*` out of `main` until the feed contract is repaired and re-proved.
- Keep the `core/public_hive/*` split and related `hive/` tree out of `main` until it has an isolated redesign proof pack.
- Keep the `core/runtime_task_rail*` split out of `main` until it lands with its own regression pack.
- Decouple `llm_eval` from `docs/LLM_ACCEPTANCE_REPORT.md` writes during proof runs so clean-checkout acceptance can stamp a non-dirty build.
- If Kimi is meant to be first-class on this machine, configure `KIMI_API_KEY` and rerun the provider/install proof lane.

## Notes

- The live acceptance report still stamps `dirty` because `llm_eval` rewrites `docs/LLM_ACCEPTANCE_REPORT.md` during the run, not because this proof checkout had unrelated code changes before launch.
- The measured concurrency curve is stable but not linear: success stayed at 1.0 through worker count 4, while throughput flattened from `0.26 req/s` at one worker to `0.229 req/s` at four workers on the locked local `qwen2.5:7b` lane.
