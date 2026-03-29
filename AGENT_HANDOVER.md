# AGENT HANDOVER

This file used to be a front-page handover. It drifted and started making the repo look wider and older than it really is.

Use these instead:

1. `README.md`
2. `docs/README.md`
3. `docs/SYSTEM_SPINE.md`
4. `docs/STATUS.md`
5. `docs/PROOF_PATH.md`
6. `CONTRIBUTING.md`

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

Current truth in one line:

`local NULLA agent -> memory + tools -> optional trusted helpers -> visible results`

Historical handovers and deep internal audits live under `docs/archive/README.md`. They are history, not the first explanation path.
