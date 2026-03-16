# AI-First Alpha Hardening Pass 1 Gauntlet

Date: 2026-03-13

Purpose:
- stress the current alpha-critical path before adding any M4 surface area
- make key regressions fail loudly in CI
- measure readiness with thresholds instead of vibes

Ship-blocking run:

```bash
pytest -q \
  tests/test_alpha_hardening_pass1_gauntlet.py \
  tests/test_milestone1_ai_first_evals.py \
  tests/test_dialogue_continuity_state.py \
  tests/test_runtime_continuity.py \
  tests/test_runtime_capability_ledger.py \
  tests/test_nulla_hive_task_flow.py \
  tests/test_nulla_web_freshness_and_lookup.py \
  tests/test_nulla_chat_truth_instrumentation.py
```

Pass rule:
- the full command above must be green
- every threshold check in `tests/test_alpha_hardening_pass1_gauntlet.py` must pass

Threshold table:

| Check | Corpus / runs | Threshold | Blocker reason |
| --- | --- | --- | --- |
| model-final hit rate on broader non-command corpus | 18 prompts | `>= 95%` | catches chat surfaces falling back to fast paths or planner shells |
| planner-wrapper regression on long-tail prompts | 12 prompts | `100%` clean | catches `Workflow:`, `Here's what I'd suggest`, `Real steps completed:`, `summary_block`, `action_plan` leakage |
| capability-truth regression on free-form asks | 5 prompts | `100%` honest | catches bluffing about unsupported, partial, impossible, or future-only abilities |
| Hive truth-label regression on degraded/fallback cases | 5 scenarios | `100%` labeled | catches unlabeled or misleading Hive source claims |
| multi-turn continuity drift | 6 sequences | `100%` correct | catches forgetting commitments/goals or leaking stale continuity |
| builder bounded-flow truth under repeated runs | 6 runs | `100%` truthful | catches fake builder success, hidden failures, or missing retry artifacts |
| honest degradation under provider/tool failure | 5 scenarios | `100%` honest | catches cache/memory/planner pretending to be fresh answers |

Corpus coverage:
- chat domains: greeting, general chat, business, research, food, relationships, creative ideation, coding, troubleshooting
- live-info: fresh lookup and weather
- Hive: watcher-derived, stale watcher, public-bridge-derived, local-only, future/unsupported
- continuity: preserved followups plus stale-state clearing
- builder: scaffold loop and repair/retry loop under repeated runs
- degradation: provider unavailable, unusable provider output, cache hit, memory hit, live-info fallback

What this gauntlet is for:
- alpha hardening only
- current AI-first path stability
- honesty and boundedness under failure

What this gauntlet is not:
- M4 rollout metrics dashboards
- multi-agent truth
- swarm transparency fabric rollout
- general autonomous software engineering proof
