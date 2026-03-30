# AGENT HANDOVER

This file used to be a front-page handover. It drifted and started making the repo look wider and older than it really is.

Use these instead:

1. `README.md`
2. `docs/README.md`
3. `docs/SYSTEM_SPINE.md`
4. `docs/STATUS.md`
5. `docs/PROOF_PATH.md`
6. `CONTRIBUTING.md`

Current product sentence:

`local-first agent runtime with inspectable execution truth and bounded operator-safe workflows`

Current focus is narrow on purpose:

1. inspectable runtime truth
2. bounded safe execution
3. rock-solid local beta

Do not lead with swarm, WAN mesh, tokens, marketplace, or “world computer” language. Those lanes are weaker than the local runtime story and easy to overclaim.

OpenClaw is a surface, not the product. Use it, integrate with it, and prove NULLA through it, but do not explain NULLA as “chat plus memory plus agents in another UI.”

Mandatory LLM proof path for any claim about model quality, latency, logic, freshness, or live runtime behavior:

1. `ops/run_local_acceptance.py`
2. `ops/llm_eval.py`
3. `config/acceptance/local_qwen25_7b_profile.json`
4. `tests/test_run_local_acceptance.py`
5. `tests/test_milestone1_ai_first_evals.py`
6. `tests/acceptance/test_llm_speed_real.py`
7. `tests/acceptance/test_recent_48h_llm_regression_real.py`
8. `tests/acceptance/test_llm_context_discipline_real.py`
9. `tests/acceptance/test_llm_research_quality_real.py`

Do not claim NULLA is fast, grounded, logical, or green unless this path is part of the proof.

Mandatory clean-room install proof for any claim about install simplicity or beta readiness:

1. wipe local NULLA state, OpenClaw state, Ollama models, and NULLA-provisioned local specialist assets
2. run the no-flag branch-pinned one-line installer
3. verify the machine-selected install profile and local bundle
4. verify runtime `/healthz`, `/v1/models`, provider truth, and file/tooling behavior
5. rerun the mandatory LLM proof path on the installed runtime

Do not say “one-line install works” unless that full clean-room loop passed on the same commit being claimed.

Current truth in one line:

`local NULLA agent -> memory + tools -> optional trusted helpers -> visible results`

Historical handovers and deep internal audits live under `docs/archive/README.md`. They are history, not the first explanation path.
