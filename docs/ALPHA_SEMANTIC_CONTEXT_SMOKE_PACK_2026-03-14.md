# Alpha Semantic/Context Smoke Pack

This pack is a blunt answer to one question:

Does NULLA behave like an assistant that tracks intent and context, or like a brittle router that only works on magic phrases?

Files in this pass:

- `tests/test_alpha_semantic_context_smoke.py`
- `docs/ALPHA_SEMANTIC_CONTEXT_SMOKE_PACK_2026-03-14.md`

No runtime code changes are part of this pack. This pass is harness and checklist only.

## Scope

The smoke pack targets the alpha conversational path across these categories:

- A. Context carryover
- B. Semantic Hive intent
- C. Shown-item selection
- D. Utility binding
- E. Planner leakage
- F. Fuzzy entity recovery
- G. Forced lookup escalation
- H. Honest uncertainty
- I. Blank / tiny / messy inputs
- J. Random reasoning sanity
- K. Correction / self-repair

## Automated Pack

Run:

```bash
pytest -q tests/test_alpha_semantic_context_smoke.py
```

Optional focused slices:

```bash
pytest -q tests/test_alpha_semantic_context_smoke.py -k hive
pytest -q tests/test_alpha_semantic_context_smoke.py -k lookup
pytest -q tests/test_alpha_semantic_context_smoke.py -k ordinary_chat
```

Automated pass threshold:

- All tests in `tests/test_alpha_semantic_context_smoke.py` must pass.
- There is no soft threshold here. This is a smoke gate, not a vibes report.

### Cross-Cutting Automated Assertions

These assertions are enforced wherever deterministic:

- `model_final_answer_hit == true` on covered ordinary-chat cases
- no placeholder tokens: `[time]`, `[date]`, `[weather]`
- no planner leakage:
  - `review problem`
  - `choose safe next step`
  - `validate result`
  - `Workflow:`
  - `Here's what I'd suggest`
  - `Real steps completed:`
  - `summary_block`
  - `action_plan`
- Hive shown-item followups start the selected task
- forced lookup/entity prompts enable lookup/research
- weak fuzzy matches admit uncertainty instead of filler

### Automated Case List

`AUTO-B01` Semantic Hive task recovery

- Prompt corpus:
  - `hi check hive pls`
  - `what's in hive`
  - `what online tasks we have`
  - `anything on hive?`
  - `show hive work`
  - `hive tasks?`
  - `what is on the hive mind tasks?`
  - `check hive mind pls`
- Expected behavior:
  - the prompt recovers to the Hive task-list path without requiring the exact canonical wording
  - output is a task list, not a generic chat fallback
- Fail if:
  - it does not route to Hive tasks
  - it leaks planner scaffolding
  - it needs the exact original phrase to work
- Mode: automated
- Exact assertions:
  - first tracker call is the raw prompt
  - second tracker call is canonical `show me the open hive tasks`
  - `response_class == task_list`
  - `model_execution.used_model == true`
  - real task titles appear in the answer

`AUTO-C01` Shown task selection by full title

- Prompt sequence:
  - shown Hive tasks are already pending in session state
  - user sends full exact title
- Expected behavior:
  - selected task starts
  - menu is not repeated
- Fail if:
  - it re-lists tasks
  - it starts the wrong task
- Mode: automated
- Exact assertions:
  - `response_class == task_started`
  - selected topic id matches the title choice
  - no `point at the task name` style menu repeat

`AUTO-C02` Shown task selection by short `#id`

- Prompt sequence:
  - shown Hive tasks are already pending in session state
  - user sends short `#id`
- Expected behavior:
  - selected task starts
  - menu is not repeated
- Fail if:
  - it re-lists tasks
  - it starts the wrong task
- Mode: automated
- Exact assertions:
  - `response_class == task_started`
  - selected topic id matches the short id
  - no menu-repeat text

`AUTO-A01` Utility/context carryover for time

- Prompt sequence:
  - `what time is now in Vilnius?`
  - `and there?`
  - `what where's is in Vilnius?`
- Expected behavior:
  - no placeholder leakage
  - followups stay on time/timezone context
  - malformed Vilnius followup recovers to time
- Fail if:
  - `[time]` appears
  - it falls into brochure filler
  - it loses the city/time context
- Mode: automated
- Exact assertions:
  - all turns return `utility_answer`
  - second turn still binds Vilnius time from recent context
  - third turn binds `current time in Vilnius`

`AUTO-D01` Utility binding for time/date/weather

- Prompt corpus:
  - `what time is now in Vilnius?`
  - `what is the date today?`
  - `what is the weather in London today?`
- Expected behavior:
  - outputs contain bound values, not placeholders
- Fail if:
  - `[time]`, `[date]`, or `[weather]` appears
  - planner scaffold leaks
- Mode: automated
- Exact assertions:
  - time response contains a bound clock value
  - date response contains `today is`
  - weather response contains the grounded weather summary

`AUTO-E01` Planner leakage guard

- Representative prompts:
  - `17 * 19`
  - `show me the open hive tasks`
  - `what time is now in Vilnius?`
- Expected behavior:
  - no internal scaffold text leaks through
- Fail if any forbidden planner marker appears
- Mode: automated
- Exact assertions:
  - every representative output is clean of all forbidden planner strings

`AUTO-F01` Fuzzy entity recovery

- Prompt corpus:
  - `who is Toly in Solana`
  - `Tolly on X in Solana who is he`
  - `find Tolly solana twitter`
  - `some big guy in Solana, Toly or Tolly, who is he`
- Expected behavior:
  - fuzzy recovery narrows toward Anatoly Yakovenko
  - messy entity prompts do not collapse into generic filler
- Fail if:
  - lookup is not enabled
  - queries do not run
  - answer stays generic
- Mode: automated
- Exact assertions:
  - `research_controller.enabled == true`
  - query seeds are present for deterministic cases
  - response contains `Anatoly Yakovenko`

`AUTO-G01` Forced lookup escalation

- Prompt corpus:
  - `check Toly on X`
  - `find Tolly solana twitter`
- Expected behavior:
  - lookup/research is preferred over generic chat
- Fail if:
  - it stays in plain chat mode without search
  - no lookup query runs
- Mode: automated
- Exact assertions:
  - `research_controller.enabled == true`
  - planned search is called
  - lookup queries are recorded

`AUTO-H01` Honest uncertainty

- Prompt:
  - `check Tolyy on X in Solana`
- Expected behavior:
  - weak match admits uncertainty cleanly
- Fail if:
  - it hallucinates a confident answer
  - it falls back to brochure filler
- Mode: automated
- Exact assertions:
  - `research_controller.admitted_uncertainty == true`
  - uncertainty reason is populated
  - response says it could not pin the figure down confidently

`AUTO-I01` Tiny-input ordinary chat

- Prompt corpus:
  - `hi`
  - `yo`
- Expected behavior:
  - short conversational reply
  - model is the final speaker
- Fail if:
  - planner scaffold leaks
  - model-final path is not hit
- Mode: automated
- Exact assertions:
  - `model_final_answer_hit == true`
  - `template_renderer_hit == false`
  - reply matches deterministic expected wording

`AUTO-J01` Random reasoning sanity

- Prompt corpus:
  - `17 * 19`
  - `if 3 workers finish in 6 days, how long for 6 workers`
  - `sort these priorities: broken auth, typo, outage`
  - `two-line explanation of recursion`
- Expected behavior:
  - plain-text answer
  - no planner shell
  - model-final path stays intact
- Fail if:
  - scaffold leaks
  - model-final path collapses
- Mode: automated
- Exact assertions:
  - `model_final_answer_hit == true`
  - `template_renderer_hit == false`
  - expected answer is returned verbatim

## Manual / Live Transcript Checklist

Some cases are not stable enough for hard deterministic automation yet. These still belong in the smoke pack, but they need a human to judge whether NULLA behaves like an assistant or a brittle router.

Manual run rule:

- use the real local runtime
- same surface you actually care about
- same session for followups/corrections
- score each transcript with the rubric below

### Manual Scoring Rubric

Score each applicable item `0` or `1`:

- understood intent without magic words
- followed recent context
- recovered from messy phrasing
- avoided planner leakage
- bound real values
- escalated to lookup when asked
- admitted uncertainty honestly
- did not repeat menus unnecessarily
- did not answer with generic brochure filler

Per-transcript pass threshold:

- `7/9` or better on applicable items
- plus no hard fail on:
  - planner leakage
  - fake certainty
  - unnecessary menu repeat on selection/correction flows

Overall live-smoke pass threshold:

- at least `80%` of manual transcripts pass
- zero hard fails in categories:
  - B. Semantic Hive intent
  - C. Shown-item selection
  - E. Planner leakage
  - G. Forced lookup escalation
  - H. Honest uncertainty

### Manual Case List

`MAN-B01` Hive correction from tasks to peers

- Prompt sequence:
  - `hi check hive pls`
  - `not tasks, peers`
  - `ok now tasks`
- Expected behavior:
  - first turn shows tasks
  - second turn switches to peers / online presence instead of repeating task menu
  - third turn switches back to tasks cleanly
- Fail if:
  - it doubles down on tasks after `not tasks, peers`
  - it ignores the correction
  - it starts inventing generic Hive brochure text
- Mode: manual
- Scoring rubric:
  - understood intent without magic words
  - followed recent context
  - recovered from messy phrasing
  - did not repeat menus unnecessarily
  - avoided planner leakage
  - did not answer with generic brochure filler

`MAN-C01` Full shown-item selection flow

- Prompt sequence:
  - ask for Hive tasks
  - select by full title
  - ask for Hive tasks again
  - select by short `#id`
- Expected behavior:
  - chosen task starts both times
  - the menu is not re-listed after selection
- Fail if:
  - it repeats the menu instead of starting the task
  - it starts the wrong task
- Mode: manual
- Scoring rubric:
  - understood intent without magic words
  - followed recent context
  - did not repeat menus unnecessarily
  - avoided planner leakage

`MAN-A02` Full utility/context carryover sequence

- Prompt sequence:
  - `what time is now in Vilnius?`
  - `and in Kaunas?`
  - `what where's is in Vilnius?`
  - `no I meant time`
  - `what about tomorrow?`
- Expected behavior:
  - first three turns stay on time context
  - `no I meant time` self-repairs cleanly
  - `what about tomorrow?` either binds correctly or asks honest clarification
- Fail if:
  - placeholder leaks
  - it answers with a generic brochure dump
  - it ignores the self-repair
- Mode: manual
- Scoring rubric:
  - followed recent context
  - recovered from messy phrasing
  - bound real values
  - admitted uncertainty honestly
  - avoided planner leakage

`MAN-F01` Exact fuzzy entity transcript pack

- Prompt corpus:
  - `who is Toly in Solana`
  - `Tolly on X in Solana who is he`
  - `check Toly on X`
  - `find Tolly solana twitter`
  - `some big guy in Solana, Toly or Tolly, who is he`
- Expected behavior:
  - recovers the likely intended public figure or says it is unsure
  - escalates to lookup when asked
- Fail if:
  - it stays in generic chat mode
  - it pretends certainty without evidence
  - it gives brochure filler
- Mode: manual
- Scoring rubric:
  - understood intent without magic words
  - recovered from messy phrasing
  - escalated to lookup when asked
  - admitted uncertainty honestly
  - did not answer with generic brochure filler

`MAN-E02` Planner leakage spot-check

- Prompts that previously hit weird planner output:
  - `what time is now in Vilnius?`
  - `check hive`
  - `who is Toly in Solana`
  - `17 * 19`
  - `two-line explanation of recursion`
- Expected behavior:
  - no internal scaffold text reaches the user
- Fail if any output contains:
  - `review problem`
  - `choose safe next step`
  - `validate result`
  - `Workflow:`
  - `Here's what I'd suggest`
  - `Real steps completed:`
  - `summary_block`
  - `action_plan`
- Mode: manual
- Scoring rubric:
  - avoided planner leakage

`MAN-I01` Blank / tiny / messy inputs

- Prompt corpus:
  - `...`
  - `?`
  - `ok and hive?`
  - `check`
  - `find him`
  - `what now`
- Expected behavior:
  - context-aware recovery when context exists
  - otherwise compact clarification, not generic dumping
- Fail if:
  - it gives a long irrelevant generic answer
  - it ignores obvious recent context
- Mode: manual
- Scoring rubric:
  - followed recent context
  - recovered from messy phrasing
  - admitted uncertainty honestly
  - did not answer with generic brochure filler

`MAN-K01` Correction / self-repair on Hive

- Prompt sequence:
  - `check hive`
  - `no not hive tasks, hive peers`
  - `no, public ones`
  - `ok back to tasks`
- Expected behavior:
  - shifts target cleanly on each correction
  - does not stay stuck on the earlier branch
- Fail if:
  - it keeps answering the old question
  - it loops the same menu
- Mode: manual
- Scoring rubric:
  - followed recent context
  - recovered from messy phrasing
  - did not repeat menus unnecessarily
  - admitted uncertainty honestly

`MAN-K02` Correction / self-repair on utility context

- Prompt sequence:
  - `what time is now in Vilnius?`
  - `not Vilnius, Kaunas`
  - `no I meant time`
- Expected behavior:
  - adjusts city correctly
  - keeps the utility frame
- Fail if:
  - it falls into generic filler
  - it ignores the correction
- Mode: manual
- Scoring rubric:
  - followed recent context
  - recovered from messy phrasing
  - bound real values
  - avoided planner leakage

## Corpus Summary

Automated corpus covers these prompt families:

- semantic Hive task asks without magic phrases
- shown-task selection by full title and short `#id`
- bound utility outputs for time/date/weather
- deterministic time-context followups around Vilnius
- fuzzy Solana entity lookup
- forced lookup prompts using `check` and `find`
- weak-match uncertainty
- tiny ordinary-chat prompts
- plain reasoning/sanity prompts

Manual corpus extends this with:

- correction-heavy Hive task/peer switching
- full live shown-item selection transcripts
- ambiguous followups like `what about tomorrow?`
- blank-ish inputs where style matters more than literal truth
- exact correction/self-repair behavior across multiple turns

## Gaps That Still Need Human Judgment

Even with this automated pack, these still need a person to judge live:

- whether replies feel assistant-like versus merely passable
- whether clarification is honest but not annoying
- whether tone stays compact instead of brochure-ish
- whether corrections feel natural instead of brittle
- whether messy tiny prompts recover from the right context, not just any context

That is intentional. You do not solve subjective assistant quality with deterministic asserts alone.
