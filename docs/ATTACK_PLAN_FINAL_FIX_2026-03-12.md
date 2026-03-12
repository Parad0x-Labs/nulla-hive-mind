# NULLA Final Attack Plan — Fix Everything Once and For All

**Date:** 2026-03-12  
**Based on:** `CODEX_NULLA_REAL_TEST_FINDINGS_2026-03-12.md` + codebase verification  
**Status:** Prioritized execution plan

---

## Verification Summary

The findings doc is **correct**. Cross-checked against codebase:

| Finding | Verified | Location |
|---------|----------|----------|
| wttr.in weather fallback | ✓ | `tools/web/web_research.py:183` |
| Google News RSS news fallback | ✓ | `tools/web/web_research.py:232` |
| consent.google.com blocked | ✓ | `core/source_credibility.py:36` |
| DDG challenge/captcha detection | ✓ | `tools/browser/browser_render.py:11` |
| Hive list exclusions (show me, open hive tasks) | ✓ | `apps/nulla_agent.py:4083-4097` |
| Runtime preamble strip | ✓ | `apps/nulla_agent.py:2971` |
| Tool failure → research fallback | ✓ | `apps/nulla_agent.py:2570-2606` |

**Tool intent short-circuit:** The fallback path exists. When `missing_intent`/`invalid_payload` and `_should_fallback_after_tool_failure` is True (research task, `_wants_fresh_info`, or curiosity), the agent returns `None` and continues to web_notes + model synthesis. That’s why "latest telegram bot api updates" works now.

---

## What’s Fixed (No Action)

- Live weather/news fallbacks
- Hive list 500 (create vs list routing)
- Runtime preamble leak
- DDG challenge honesty
- consent.google.com in news
- Canonical port 11435

---

## Attack Plan — Prioritized

### Phase 1: Quality & Synthesis (High Impact, Low Risk)

**1.1 Sharpen Telegram / live-info answers**

- **Target:** Explicit changelog/doc URLs, tighter summaries, less generic phrasing.
- **Files:** `apps/nulla_agent.py`, `retrieval/web_adapter.py`, prompt/context assembly.
- **Actions:**
  - Add "official changelog" / "release notes" hints to web research for `latest X` queries.
  - Prefer `core.telegram.org` and similar official domains in source scoring.
  - Adjust synthesis prompt to require concrete URLs when available.

**1.2 Normalize weather/news snippets**

- **Target:** Concise summaries, cleaner formatting, less raw page text.
- **Files:** `tools/web/web_research.py`, `retrieval/web_adapter.py`, `core/source_credibility.py`.
- **Actions:**
  - Add snippet normalizer for weather (temp, condition, location).
  - Add news formatter (headline | date | source).
  - Filter out noisy interstitials beyond consent.google.com.

**1.3 Polish Hive task list formatting**

- **Target:** Clear bullets, "what next" phrasing, open vs claimed vs researching.
- **Files:** `apps/nulla_agent.py`, `core/hive_activity_tracker.py`.
- **Actions:**
  - Refactor `_render_hive_task_list` / `_render_hive_overview` for cleaner output.
  - Add status badges (open / claimed / researching).
  - Add short "Say 'start #id' to begin" footer.

---

### Phase 2: UX & Conversation (Medium Impact)

**2.1 Reduce greeting/smalltalk repetition**

- **Target:** Less canned, more varied responses.
- **Files:** `apps/nulla_agent.py` (smalltalk fast path ~line 1005).
- **Actions:**
  - Add 2–3 variants per greeting type.
  - Optionally: after N turns, bypass deterministic smalltalk and use model.

**2.2 Improve context-following for Hive**

- **Target:** "ok", "yes", "do it", "pick one" resolve to last Hive context.
- **Files:** `core/hive_activity_tracker.py`, `apps/nulla_agent.py`.
- **Actions:**
  - Broaden `_looks_like_contextual_hive_pull_request` for short affirmatives.
  - Use session state (last listed tasks, pending nudge) more consistently.

---

### Phase 3: Robustness (Lower Priority)

**3.1 Broaden tool failure fallback**

- **Target:** More queries fall through to research when tool intent fails.
- **Files:** `apps/nulla_agent.py` (`_should_fallback_after_tool_failure`).
- **Actions:**
  - Add task classes: e.g. `learning`, `exploration`.
  - Add markers: "how to", "what is", "explain", "guide".
  - Consider defaulting to fallback for chat surface when `executed_steps` is empty.

**3.2 Clean adaptation training data**

- **Target:** Remove "Real steps completed" and "invalid tool payload" from adaptation corpora.
- **Files:** `.nulla_local/data/adaptation/` corpora, eval/train splits.
- **Actions:**
  - Script to strip runtime preamble and failure text from corpus.
  - Re-run adaptation pipeline on cleaned data.

---

### Phase 4: World Computer (Aspirational — Defer)

- Swarm depth, economics, settlement remain incomplete.
- Treat as roadmap, not immediate fix.
- Reference: `docs/WORLD_COMPUTER_EXECUTION_PLAN.md`.

---

## Execution Order

```
Week 1:
  1.1 Sharpen Telegram/live-info answers
  1.2 Normalize weather/news snippets
  1.3 Polish Hive task list formatting

Week 2:
  2.1 Greeting/smalltalk variants
  2.2 Context-following for Hive

Week 3+:
  3.1 Broaden tool fallback
  3.2 Clean adaptation data
```

---

## Quick Wins (Same-Day)

1. **Hive list polish** — Adjust `_render_hive_task_list` formatting (≈1h).
2. **Greeting variants** — Add 2–3 variants per phrase (≈30min).
3. **Telegram URL hint** — Add `core.telegram.org` to preferred domains (≈30min).

---

## Test Commands

```sh
# Regression slice
python3 -m pytest tests/test_openclaw_tooling_context.py tests/test_nulla_hive_task_flow.py tests/test_nulla_runtime_contracts.py tests/test_nulla_web_freshness_and_lookup.py tests/test_web_research_runtime.py tests/test_source_credibility.py tests/test_browser_render_flag.py tests/test_web_adapter.py

# Deep contracts
sh scripts/run_nulla_deep_contract_tests.sh
```

---

## Success Criteria

- [ ] Telegram-style queries return explicit changelog URLs when available.
- [ ] Weather/news answers use normalized snippets, not raw fragments.
- [ ] Hive list has clear status badges and "what next" guidance.
- [ ] Greetings vary across sessions.
- [ ] "ok" / "do it" after Hive list resolves to correct task.
- [ ] All regression + deep contract tests pass.
